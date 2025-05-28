from __future__ import annotations
import os, json, time, traceback, re
from typing import Final
import psycopg2
from psycopg2 import sql
import requests
from flask import Flask, jsonify, request, Response

app = Flask(__name__)

# ---------- PostgreSQL --------------------------------------------
DB_HOST, DB_PORT   = os.getenv("DB_HOST"), os.getenv("DB_PORT")
DB_NAME, DB_USER   = os.getenv("DB_NAME"), os.getenv("DB_USER")
DB_PASSWORD        = os.getenv("DB_PASSWORD")

db_conn = psycopg2.connect(
    host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
    user=DB_USER, password=DB_PASSWORD
)
db_conn.autocommit = True

def check_device_registered(device_id: str) -> bool:
    try:
        with db_conn.cursor() as cur:
            cur.execute(
                sql.SQL("SELECT 1 FROM {t} WHERE {c}=%s LIMIT 1")
                   .format(t=sql.Identifier("users"), c=sql.Identifier("deviceID")),
                (device_id,)
            )
            return cur.fetchone() is not None
    except Exception as e:
        print(f"[DB CHECK] error: {e}")
        return False

# ---------- runtime-состояние -------------------------------------
user_state: dict[str, dict[str, bool | str]] = {}

# ---------- фразы --------------------------------------------------
activation_phrases: Final = {
    "записать сон","запиши сон","запиши мой сон",
    "запись сна","запись снов","дневник снов",
    "запусти dreamember","включи запись сна",
    "запусти дневник снов","запусти запись сна",
    "запусти запись снов","открой дневник снов",
}
help_phrases: Final = {"помощь","help","что ты умеешь","как пользоваться"}

# ---------- health-эндпойнт ---------------------------------------
@app.route("/health", methods=["GET","HEAD"])
def health() -> Response:
    payload = {"status":"ok"}
    if request.method == "GET" and request.args.get("verbose") == "1":
        payload |= {"ts":int(time.time()),"users_in_mem":len(user_state)}
    return jsonify(payload)

# ----------------------------- WEBHOOK -----------------------------
@app.route("/webhook", methods=["POST"])
def handle_smartapp():
    data    = request.json or {}
    pl      = data.get("payload", {})
    user_id = data.get("uuid", {}).get("userId")
    text    = pl.get("message", {}).get("original_text", "").strip().lower()

    # 💬 выводим полный JSON + какой ассистент выбран
    print("=== Incoming webhook ===")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    char_id = pl.get("character", {}).get("id", "sber")
    print(f"Assistant voice: {char_id}")
    print("========================")

    if not user_id:
        return jsonify(default_error(data))

    # ---------- состояние пользователя ----------------------------
    state = user_state.setdefault(user_id, {
        "welcomed": False,
        "awaiting": False,
        "awaiting_login": False,
        "awaiting_password": False,
        "temp_login": "",
        "registered": None
    })
    if state["registered"] is None:
        state["registered"] = check_device_registered(user_id)

    # ---------- welcome (один раз) --------------------------------
    if pl.get("intent") == "run_app" and not state["welcomed"]:
        state["welcomed"] = True
        return jsonify(answer(
            "Привет! Я «Дримембер» — ваш дневник снов.\n"
            "Скажите «Запиши сон» или «Помощь», чтобы узнать команды.",
            data
        ))

    # если пользователь молчит (пустой текст)
    if text == "":
        return jsonify(answer(
            "Привет! Я «Дримембер» — ваш дневник снов.\n"
            "Скажите «Запиши сон» или «Помощь».",
            data
        ))

    # ---------- помощь --------------------------------------------
    if text in help_phrases:
        return jsonify(answer(
            "Я «Дримембер».\n"
            "1) Скажите «Запиши сон».\n"
            "2) Расскажите сон (до 90 сек.).\n"
            "3) Смотрите записи на сайте.",
            data
        ))

    # ---------- регистрация ---------------------------------------
    if (not state["registered"]
        and not state["awaiting_login"]
        and not state["awaiting_password"]
        and text in activation_phrases):
        state["awaiting_login"] = True
        return jsonify(answer(
            "Назовите логин латиницей (только маленькие буквы/цифры).",
            data
        ))

    if state["awaiting_login"]:
        login_input = text
        if len(login_input) < 3 or not re.fullmatch(r'[a-z0-9_-]+', login_input):
            return jsonify(answer(
                "Логин ≥3 символов, маленькие латинские буквы, цифры, «-» и «_». "
                "Повторите логин.", data
            ))
        state["temp_login"] = login_input  # храним в нижнем регистре
        state["awaiting_login"] = False
        state["awaiting_password"] = True
        return jsonify(answer(
            "Теперь придумайте пароль (≥8 символов, латиница/цифры).",
            data
        ))

    if state["awaiting_password"]:
        password = text
        if len(password) < 8 or not re.fullmatch(r'[A-Za-z0-9_-]+', password):
            return jsonify(answer(
                "Пароль ≥8 символов, используйте латиницу/цифры.",
                data
            ))
        login = state["temp_login"]
        state["awaiting_password"] = False
        try:
            resp = requests.post(
                "https://dreamember.onrender.com/api/user/registration",
                json={"login": login, "password": password, "deviceID": user_id},
                timeout=5)
            if resp.ok:
                state["registered"] = True
                return jsonify(answer("Регистрация успешна! Скажите «Запиши сон».", data))
            state["awaiting_login"] = True
            return jsonify(answer("Логин занят, назовите другой.", data))
        except Exception:
            state["awaiting_login"] = True
            print("[REGISTER] error:", traceback.format_exc())
            return jsonify(answer("Сервер регистрации недоступен, повторите позже.", data))

    # ---------- запись сна ----------------------------------------
    if text in activation_phrases:
        if not state["registered"]:
            state["awaiting_login"] = True
            return jsonify(answer(
                "Устройство ещё не привязано. Назовите логин латиницей.",
                data
            ))
        if state["awaiting"]:
            return jsonify(answer("Я слушаю. Расскажите сон.", data))
        state["awaiting"] = True
        return jsonify(answer("Готов записать сон. Начинайте.", data))

    if state["awaiting"]:
        state["awaiting"] = False
        try:
            resp = requests.post(
                "https://dreamember.onrender.com/api/dream",
                json={"text": text, "deviceID": user_id},
                timeout=5)
            resp.raise_for_status()
            return jsonify(answer(
                "Сон записан! Хорошего дня 🤍\n"
                "Просмотреть сны можно на сайте: <https://dreamember.onrender.com/>",
                data
            ))
        except Exception:
            state["awaiting"] = True
            print("[DREAM SAVE] error:", traceback.format_exc())
            return jsonify(answer(
                "Сервер временно недоступен. Повторите позже.",
                data
            ))

    # ---------- fallback ------------------------------------------
    return jsonify(answer(
        "Не расслышал. Скажите «Запиши сон» или «Помощь».",
        data
    ))

# ------------------- вспомогательные функции ----------------------
URL_RE = re.compile(r'https?://\\S+|<[^>]+>')

def answer(text: str, data: dict, end_session: bool = False) -> dict:
    """
    Сбер/Афина не зачитывают URL — заменяем на «сайте»
    """
    char_id = data.get("payload", {}).get("character", {}).get("id", "sber")
    pronounce = URL_RE.sub("сайте", text) if char_id in {"sber", "athena"} else text
    return {
        "messageName": "ANSWER_TO_USER",
        "sessionId": data["sessionId"],
        "messageId": data["messageId"],
        "uuid": data["uuid"],
        "payload": {
            "pronounceText": pronounce,
            "pronounceTextType": "application/text",
            "items": [{"bubble": {"text": text, "markdown": True}}],
            "auto_listening": False,
            "finished": end_session,
        },
    }

def default_error(data: dict) -> dict:
    return answer("Ошибка. Попробуйте ещё раз.", data, end_session=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))