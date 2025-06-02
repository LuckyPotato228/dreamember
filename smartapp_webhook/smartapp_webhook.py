from __future__ import annotations
import os, json, traceback, re
from typing import Final
import psycopg2
from psycopg2 import sql
import requests
from flask import Flask, jsonify, request, Response

app = Flask(__name__)

# ---------- Postgres ---------------------------------------------
DB_HOST, DB_PORT = os.getenv("DB_HOST"), os.getenv("DB_PORT")
DB_NAME, DB_USER = os.getenv("DB_NAME"), os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

db_conn = psycopg2.connect(
    host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
    user=DB_USER, password=DB_PASSWORD
)
db_conn.autocommit = True

def check_device_registered(device_id: str) -> bool:
    with db_conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT 1 FROM {t} WHERE {c}=%s LIMIT 1")
               .format(t=sql.Identifier("users"), c=sql.Identifier("deviceID")),
            (device_id,))
        return cur.fetchone() is not None

# ---------- helpers: voice & placeholders -------------------------
def voice(data: dict) -> str:
    return data.get("payload", {}).get("character", {}).get("id", "sber")

PH = {
    "{say}"     : ("Скажи"   , "Скажите"),
    "{tell}"    : ("Назови"  , "Назовите"),
    "{create}"  : ("Придумай", "Придумайте"),
    "{repeat}"  : ("Повтори" , "Повторите"),
    "{describe}": ("Расскажи", "Расскажите"),
    "{start}"   : ("Начинай" , "Начинайте"),
    "{ready}"   : ("готова"  , "готова/готов"),
    "{you}"     : ("тебе"    , "вам"),
    "{your}"    : ("твой"    , "ваш"),
    "{help}"    : ("помогла" , "помогла/помог"),
}

def adapt(text: str, v: str) -> str:
    """
    Заменяет плейсхолдеры на формы «ты» (Joy) или «вы» (Sber/Athena),
    + корректирует род и слово «помог/помогла».
    """
    is_ty = v == "joy"
    rep   = {k: (ty if is_ty else vy) for k, (ty, vy) in PH.items()}

    # {ready}: муж. род только у Sber
    if not is_ty and v == "sber":
        rep["{ready}"] = "готов"
    elif not is_ty and v == "athena":
        rep["{ready}"] = "готова"

    # {help}: жен. род для Joy и Athena, муж. для Sber
    rep["{help}"] = "помогла" if v in ("joy", "athena") else "помог"

    for k, val in rep.items():
        text = text.replace(k, val)
    return text

URL_RE = re.compile(r'https?://\S+|<[^>]+>')     # скрываем озвучку URL

# ---------- runtime-state -----------------------------------------
user_state: dict[str, dict[str, bool | str]] = {}

# ---------- фразы активации и помощи ------------------------------
activation_phrases: Final = {
    "записать сон","запиши сон","запиши мой сон",
    "запись сна","запись снов","дневник снов",
    "запусти dreamember","включи запись сна",
    "запусти дневник снов","запусти запись сна",
    "запусти запись снов","открой дневник снов",
}
help_phrases: Final = {"помощь","help","что ты умеешь","как пользоваться"}

# ---------- формирование ответа -----------------------------------

def answer(
    text: str,
    data: dict,
    *,
    buttons: list[dict] | None = None,
    textEntry: bool = False,
    end_session: bool = False
) -> dict:
    """
    Универсальная функция для формирования JSON-ответа.
    Параметры:
      - text (str): отображаемый текст (с markdown).
      - data (dict): оригинальный запрос, чтобы перекинуть sessionId и messageId.
      - buttons (list[dict]|None): список кнопок-подсказок, каждая кнопка:
            {
              "type": "text",
              "title": "Название кнопки",
              "payload": "текст, который придёт, если пользователь нажмёт кнопку"
            }
      - textEntry (bool): если True, на стороне Сбер-Графа отобразится блок TextEntry (пользователь может ввести свободный текст).
      - end_session (bool): если True, диалог помечается как закончившийся.
    """
    # 1. Готовим озвучиваемый текст: убираем URL через регулярку
    pronounce = URL_RE.sub("сайте", text)
    # 2. Собираем базовую структуру ответа
    payload: dict = {
        "pronounceText": pronounce,
        "pronounceTextType": "application/text",
        "items": [
            {"bubble": {"text": text, "markdown": True}}
        ],
        "auto_listening": False,
        "finished": end_session
    }
    # 3. Если нужны кнопки-подсказки, добавляем их в payload
    if buttons:
        payload["buttons"] = buttons
    # 4. Если требуется текстовый ввод (логин/пароль), включаем блок textEntry
    if textEntry:
        payload["textEntry"] = True

    return {
        "messageName": "ANSWER_TO_USER",
        "sessionId": data.get("sessionId", ""),
        "messageId": data.get("messageId", ""),
        "uuid": data.get("uuid", {}),
        "payload": payload
    }

def default_error(data: dict) -> dict:
    return answer(
        adapt("Техническая ошибка. {repeat} позже.", voice(data)),
        data,
        end_session=True
    )

# ---------- health -------------------------------------------------
@app.route("/health", methods=["GET", "HEAD"])
def health() -> Response:
    return jsonify({"status": "ok", "users": len(user_state)})

# ---------- webhook ----------------------------------------------
@app.route("/webhook", methods=["POST"])
def handle_smartapp():
    data    = request.json or {}
    pl      = data.get("payload", {})
    user_id = data.get("uuid", {}).get("userId")
    text    = pl.get("message", {}).get("original_text", "").strip().lower()
    v       = voice(data)

    # --- Логируем входящий запрос ---
    print("=== Incoming webhook ===")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"Assistant voice: {v}")
    print("========================")

    # Если userId нет — возвращаем общую ошибку
    if not user_id:
        return jsonify(default_error(data))

    # Достаём или инициализируем состояние пользователя
    st = user_state.setdefault(user_id, {
        "welcomed": False, "awaiting": False,
        "awaiting_login": False, "awaiting_password": False,
        "temp_login": "", "registered": None
    })
    # Если ещё не проверяли регистрацию — проверяем в БД
    if st["registered"] is None:
        try:
            st["registered"] = check_device_registered(user_id)
        except Exception:
            # Если не удалось дозвониться до БД, считаем, что пользователь не зарегистрирован,
            # но логика всё равно предложит регистрацию при попытке «Запиши сон»
            st["registered"] = False

    # ---------- этап 1: приветствие (intent == "run_app") ----------
    if pl.get("intent") == "run_app" and not st["welcomed"]:
        st["welcomed"] = True
        text_reply = adapt(
            "Привет! Я «Дримембер» — {your} дневник снов.\n"
            "{say} «Запиши сон» или «Помощь», чтобы я {help} {you}.", v
        )
        # Кнопки-подсказки: «Запиши сон» и «Помощь»
        buttons = [
            {"type": "text", "title": "Запиши сон", "payload": "запиши сон"},
            {"type": "text", "title": "Помощь",   "payload": "помощь"}
        ]
        return jsonify(answer(text_reply, data, buttons=buttons))

    # ---------- этап 2: если пришёл пустой текст ------------
    if text == "":
        text_reply = adapt(
            "Привет! Я «Дримембер». {say} «Запиши сон».", v
        )
        buttons = [
            {"type": "text", "title": "Запиши сон", "payload": "запиши сон"},
            {"type": "text", "title": "Помощь",   "payload": "помощь"}
        ]
        return jsonify(answer(text_reply, data, buttons=buttons))

    # ---------- этап 3: помощь ------------
    if text in help_phrases:
        text_reply = adapt(
            "Я «Дримембер».\n"
            "1) {say} «Запиши сон».\n"
            "2) {describe} сон (до 90 сек.).", v
        )
        buttons = [
            {"type": "text", "title": "Запиши сон", "payload": "запиши сон"},
            {"type": "text", "title": "Помощь",   "payload": "помощь"}
        ]
        return jsonify(answer(text_reply, data, buttons=buttons))

    # ---------- этап 4: регистрация (запрос логина) ------------
    if (not st["registered"] and not st["awaiting_login"]
        and not st["awaiting_password"] and text in activation_phrases):
        st["awaiting_login"] = True
        text_reply = adapt("{tell} логин латиницей (строчные буквы/цифры).", v)
        # Включаем блок TextEntry, чтобы пользователь ввёл логин вручную
        return jsonify(answer(text_reply, data, textEntry=True))

    # ---------- этап 5: пользователь вводит логин ------------
    if st["awaiting_login"]:
        # Проверяем корректность логина
        if len(text) < 3 or not re.fullmatch(r"[a-z0-9_-]+", text):
            text_reply = adapt(
                "Логин ≥3 символов, буквы, цифры, «-», «_». {repeat} логин.", v
            )
            # Снова остаёмся в режиме ввода логина
            return jsonify(answer(text_reply, data, textEntry=True))

        # Логин валидный
        st["temp_login"] = text
        st["awaiting_login"] = False
        st["awaiting_password"] = True
        text_reply = adapt("{create} пароль (≥8 символов).", v)
        # Блок TextEntry для пароля
        return jsonify(answer(text_reply, data, textEntry=True))

    # ---------- этап 6: пользователь вводит пароль ------------
    if st["awaiting_password"]:
        # Проверяем корректность пароля
        if len(text) < 8 or not re.fullmatch(r"[A-Za-z0-9_-]+", text):
            text_reply = adapt(
                "Пароль ≥8 символов, латиница/цифры. {repeat} пароль.", v
            )
            # Остаёмся в режиме ввода пароля
            return jsonify(answer(text_reply, data, textEntry=True))
        # Формируем запрос к API регистрации
        try:
            resp = requests.post(
                "https://dreamember.onrender.com/api/user/registration",
                json={
                    "login": st["temp_login"],
                    "password": text,
                    "deviceID": user_id
                },
                timeout=5
            )
            if resp.ok:
                st["registered"] = True
                st["awaiting_password"] = False
                # После успешной регистрации предлагаем сразу «Запиши сон»
                text_reply = adapt("Регистрация успешна! {say} «Запиши сон».", v)
                buttons = [
                    {"type": "text", "title": "Запиши сон", "payload": "запиши сон"},
                    {"type": "text", "title": "Помощь",   "payload": "помощь"}
                ]
                return jsonify(answer(text_reply, data, buttons=buttons))
            else:
                # Логин занят — начинаем сначала с ввода логина
                st["awaiting_login"] = True
                st["awaiting_password"] = False
                text_reply = adapt("Логин занят. {tell} другой логин.", v)
                return jsonify(answer(text_reply, data, textEntry=True))
        except Exception:
            # Сервер регистрации недоступен
            st["awaiting_login"] = True
            st["awaiting_password"] = False
            text_reply = adapt("Сервер регистрации недоступен, {repeat} позже.", v)
            return jsonify(answer(text_reply, data, textEntry=True))

    # ---------- этап 7: начало записи сна ------------
    if text in activation_phrases:
        if not st["registered"]:
            # Если вдруг пользователь ещё не зарегистрировался
            st["awaiting_login"] = True
            text_reply = adapt("Устройство не привязано. {tell} логин.", v)
            return jsonify(answer(text_reply, data, textEntry=True))

        # Если мы уже ждали текст сна (awaiting=True), просим продолжать
        if st["awaiting"]:
            text_reply = adapt("Я слушаю, {describe} сон.", v)
            # Пусть остаётся возможность просто поговорить дальше
            return jsonify(answer(text_reply, data))

        # Если пользователь только что сказал «Запиши сон» и awaiting=False
        st["awaiting"] = True
        text_reply = adapt("{ready} записать {your} сон. {start}.", v)
        # Две кнопки: «Начать запись» или «Отмена»
        buttons = [
            {"type": "text", "title": "Начать запись", "payload": "начать запись"},
            {"type": "text", "title": "Отмена",        "payload": "отмена"}
        ]
        return jsonify(answer(text_reply, data, buttons=buttons))

    # ---------- этап 8: пользователь диктует текст сна ------------
    if st["awaiting"]:
        st["awaiting"] = False
        try:
            resp = requests.post(
                "https://dreamember.onrender.com/api/dream",
                json={"text": text, "deviceID": user_id},
                timeout=5
            )
            resp.raise_for_status()
            text_reply = adapt(
                "Сон записан! Хорошего дня 🤍\n"
                "Записи на сайте: https://dreamember.onrender.com/", v
            )
            # Две кнопки: «Записи на сайте» и «Запиши ещё сон»
            buttons = [
                {"type": "text", "title": "Записи на сайте", "payload": "записи на сайте"},
                {"type": "text", "title": "Запиши ещё сон",  "payload": "запиши сон"}
            ]
            return jsonify(answer(text_reply, data, buttons=buttons))
        except Exception:
            # Если возникла проблема с сервером, снова ждём, чтобы пользователь мог повторить
            st["awaiting"] = True
            text_reply = adapt("Сервер недоступен, {repeat} позже.", v)
            buttons = [
                {"type": "text", "title": "Попробовать снова", "payload": "запиши сон"},
                {"type": "text", "title": "Помощь",            "payload": "помощь"}
            ]
            return jsonify(answer(text_reply, data, buttons=buttons))

    # ---------- этап 9: фолбэк ------------
    text_reply = adapt("Не расслышал. {say} «Запиши сон».", v)
    buttons = [
        {"type": "text", "title": "Запиши сон", "payload": "запиши сон"},
        {"type": "text", "title": "Помощь",   "payload": "помощь"}
    ]
    return jsonify(answer(text_reply, data, buttons=buttons))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
