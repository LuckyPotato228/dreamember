from __future__ import annotations
import os, json, re, time, traceback
from typing import Final

import psycopg2
from psycopg2 import sql
import requests
from flask import Flask, jsonify, request, Response

app = Flask(__name__)

# ---------- Postgres ---------------------------------------------
DB_HOST, DB_PORT = os.getenv("DB_HOST"), os.getenv("DB_PORT")
DB_NAME, DB_USER = os.getenv("DB_NAME"), os.getenv("DB_USER")
DB_PASSWORD      = os.getenv("DB_PASSWORD")

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
def voice(data: dict) -> str:                       # joy / athena / sber
    return data.get("payload", {}).get("character", {}).get("id", "sber")


PH = {                       # «ты»-форма , «вы»-форма
    "{say}"     : ("Скажи",    "Скажите"),
    "{tell}"    : ("Запиши",   "Запишите"),
    "{create}"  : ("Придумай", "Придумайте"),
    "{repeat}"  : ("Повтори",  "Повторите"),
    "{describe}": ("Расскажи", "Расскажите"),
    "{start}"   : ("Начинай",  "Начинайте"),
    "{ready}"   : ("готова",   "готова/готов"),  # уточняем ниже
    "{you}"     : ("тебе",     "вам"),
    "{your}"    : ("твой",     "ваш"),
    "{help}"    : ("помогла",  "помогла/помог"),
}

def adapt(text: str, v: str) -> str:
    """Подставляем нужные формы в шаблонную строку."""
    is_ty = v == "joy"
    rep   = {k: (ty if is_ty else vy) for k, (ty, vy) in PH.items()}

    # род для {ready}
    if not is_ty and v == "sber":
        rep["{ready}"] = "готов"
    elif not is_ty and v == "athena":
        rep["{ready}"] = "готова"

    # род для {help}
    rep["{help}"] = "помогла" if v in ("joy", "athena") else "помог"

    for k, val in rep.items():
        text = text.replace(k, val)
    return text


URL_RE = re.compile(r'https?://\S+|<[^>]+>')   # скрываем озвучку URL


# ---------- runtime-state -----------------------------------------
user_state: dict[str, dict[str, bool | str | None]] = {}

def reset_state(st: dict) -> None:
    """Полный сброс этапов диалога."""
    st.update(awaiting=False,
              awaiting_login=False,
              awaiting_password=False,
              temp_login="")


# ---------- фразы -------------------------------------------------
activation_phrases: Final = {
    "записать сон", "запиши сон", "запиши мой сон",
    "запись сна", "запись снов", "дневник снов",
    "запусти dreamember", "включи запись сна",
    "запусти дневник снов", "запусти запись сна",
    "запусти запись снов", "открой дневник снов",
}
help_phrases: Final = {"помощь", "help", "что ты умеешь", "как пользоваться"}


# ---------- health ------------------------------------------------
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

    if not user_id:
        return jsonify(default_error(data))

    st = user_state.setdefault(user_id, {
        "welcomed": False,
        "awaiting": False,
        "awaiting_login": False,
        "awaiting_password": False,
        "temp_login": "",
        "registered": False,
    })

    # 🆕  актуализируем статус привязки КАЖДЫЙ запрос
    st["registered"] = check_device_registered(user_id)

    # ---------- «Отмена» ------------------------------------------
    if text in {"отмена", "cancel"}:
        reset_state(st)
        return jsonify(answer(
            adapt("Хорошо, вернёмся к началу. {say} «Запиши сон» или «Помощь».", v),
            data, suggestions=["Запиши сон", "Помощь"]
        ))

    # ---------- welcome -------------------------------------------
    if pl.get("intent") == "run_app" and not st["welcomed"]:
        st["welcomed"] = True
        return jsonify(answer(
            adapt("Привет! Я «Дримембер» — {your} дневник снов.\n"
                  "{say} «Запиши сон» или «Помощь», чтобы я {help} {you}.", v),
            data, suggestions=["Запиши сон", "Помощь"]
        ))

    if text == "":
        return jsonify(answer(
            adapt("Привет! Я «Дримембер». {say} «Запиши сон».", v),
            data, suggestions=["Запиши сон", "Помощь"]
        ))

    # ---------- help ----------------------------------------------
    if text in help_phrases:
        return jsonify(answer(
            adapt("Я «Дримембер».\n1) {say} «Запиши сон».\n"
                  "2) {describe} сон (до 90 сек.).", v),
            data, suggestions=["Запиши сон"]
        ))

    # ---------- REGISTRATION – запрос логина ----------------------
    if (not st["registered"] and not st["awaiting_login"]
        and not st["awaiting_password"] and text in activation_phrases):
        st["awaiting_login"] = True
        return jsonify(answer(
            adapt("{tell} логин латиницей (строчные буквы/цифры).", v),
            data, suggestions=["Отмена"]
        ))

    # ---------- REGISTRATION – ввод логина ------------------------
    if st["awaiting_login"]:
        if len(text) < 3 or not re.fullmatch(r'[a-z0-9_-]+', text):
            return jsonify(answer(
                adapt("Логин ≥3 символов, буквы, цифры, «-», «_». {repeat} логин.", v),
                data, suggestions=["Отмена"]
            ))
        st.update(temp_login=text,
                  awaiting_login=False,
                  awaiting_password=True)
        return jsonify(answer(
            adapt("{create} пароль (≥8 символов латиницы/цифр).", v),
            data, suggestions=["Отмена"]
        ))

    # ---------- REGISTRATION – ввод пароля + API ------------------
    if st["awaiting_password"]:
        if len(text) < 8 or not re.fullmatch(r'[A-Za-z0-9_-]+', text):
            return jsonify(answer(
                adapt("Пароль ≥8 символов, латиница/цифры. {repeat} пароль.", v),
                data, suggestions=["Отмена"]
            ))
        payload = {"login": st["temp_login"], "password": text, "deviceID": user_id}
        try:
            resp = requests.post(
                "https://dreamember.onrender.com/api/user/registration",
                json=payload, timeout=5)

            if resp.ok:                       # 201 Created
                st["registered"] = True
                reset_state(st)
                return jsonify(answer(
                    adapt("Регистрация успешна! {say} «Запиши сон».", v),
                    data, suggestions=["Запиши сон"]
                ))

            # --- разбор возможного ответа -------------------------
            msg = ""
            try:
                msg = resp.json().get("message", "").lower()
            except Exception:
                pass

            if "deviceid" in msg or "колонк" in msg:
                st["registered"] = True
                reset_state(st)
                return jsonify(answer(
                    adapt("Устройство уже привязано. {ready} записать сон.", v),
                    data, suggestions=["Запиши сон"]
                ))

            if "login" in msg:
                st.update(awaiting_login=True, awaiting_password=False)
                return jsonify(answer(
                    adapt("Такой логин уже есть. {tell} другой логин.", v),
                    data, suggestions=["Отмена"]
                ))

            st.update(awaiting_login=True, awaiting_password=False)
            return jsonify(answer(
                adapt("Не удалось зарегистрироваться. {repeat} позже.", v),
                data, suggestions=["Отмена"]
            ))

        except Exception:
            st.update(awaiting_login=True, awaiting_password=False)
            return jsonify(answer(
                adapt("Сервер регистрации недоступен. {repeat} позже.", v),
                data, suggestions=["Отмена"]
            ))

    # ---------- RECORD DREAM – активация --------------------------
    if text in activation_phrases:
        if not st["registered"]:
            st["awaiting_login"] = True
            return jsonify(answer(
                adapt("Устройство не привязано. {tell} логин.", v),
                data, suggestions=["Отмена"]
            ))
        if st["awaiting"]:
            return jsonify(answer(
                adapt("Я слушаю, {describe} сон.", v), data))
        st["awaiting"] = True
        return jsonify(answer(
            adapt("{ready} записать {your} сон. {start}.", v),
            data, suggestions=["Отмена"]
        ))

    # ---------- RECORD DREAM – приём текста -----------------------
    if st["awaiting"]:
        st["awaiting"] = False
        try:
            resp = requests.post(
                "https://dreamember.onrender.com/api/dream",
                json={"text": text, "deviceID": user_id},
                timeout=5)

            if resp.status_code in (401, 403):
                # колонка «оторвалась» → запускаем регистрацию
                st["registered"] = False
                reset_state(st)
                st["awaiting_login"] = True
                return jsonify(answer(
                    adapt("Колонка ещё не привязана. {tell} логин латиницей.", v),
                    data, suggestions=["Отмена"]
                ))

            resp.raise_for_status()
            return jsonify(answer(
                adapt("Сон записан! Хорошего дня 🤍\n"
                      "Посмотреть записи на сайте: <https://dreamember.onrender.com/>", v),
                data, suggestions=["Запиши сон"]
            ))
        except Exception:
            st["awaiting"] = True
            return jsonify(answer(
                adapt("Сервер недоступен, {repeat} позже.", v),
                data, suggestions=["Отмена"]
            ))

    # ---------- fallback ------------------------------------------
    return jsonify(answer(
        adapt("Не расслышал. {say} «Запиши сон».", v),
        data, suggestions=["Запиши сон", "Помощь"]
    ))


# ---------- answer / default --------------------------------------
def answer(text: str, data: dict,
           suggestions: list[str] | None = None,
           end_session: bool = False) -> dict:
    pronounce = URL_RE.sub("сайте", text)   # ссылки не озвучиваем
    payload = {
        "pronounceText": pronounce,
        "pronounceTextType": "application/text",
        "items": [{"bubble": {"text": text, "markdown": True}}],
        "auto_listening": False,
        "finished": end_session,
    }
    if suggestions:
        payload["suggestions"] = {
            "buttons": [{"title": s, "action": {"text": s}} for s in suggestions]
        }

    return {
        "messageName": "ANSWER_TO_USER",
        "sessionId": data["sessionId"],
        "messageId": data["messageId"],
        "uuid": data["uuid"],
        "payload": payload,
    }

def default_error(data: dict) -> dict:
    return answer(
        adapt("Техническая ошибка. {repeat} позже.", voice(data)),
        data, suggestions=["Запиши сон"], end_session=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
