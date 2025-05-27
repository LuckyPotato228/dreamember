from __future__ import annotations
import os
import json
import time
import traceback
import re
from typing import Final

import psycopg2
from psycopg2 import sql
import requests
from flask import Flask, jsonify, request, Response

app = Flask(__name__)

# ---------- подключение к Postgres через psycopg2 ----------------
DB_HOST     = os.getenv("DB_HOST")
DB_PORT     = os.getenv("DB_PORT")
DB_NAME     = os.getenv("DB_NAME")
DB_USER     = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

db_conn = psycopg2.connect(
    host     = DB_HOST,
    port     = DB_PORT,
    dbname   = DB_NAME,
    user     = DB_USER,
    password = DB_PASSWORD,
)
db_conn.autocommit = True

def check_device_registered(device_id: str) -> bool:
    """
    Проверяем в таблице users наличие записи с данным deviceID.
    """
    try:
        with db_conn.cursor() as cur:
            query = sql.SQL(
                "SELECT 1 FROM {table} WHERE {col} = %s LIMIT 1"
            ).format(
                table=sql.Identifier("users"),
                col=sql.Identifier("deviceID")
            )
            cur.execute(query, (device_id,))
            return cur.fetchone() is not None
    except Exception as e:
        print(f"[DB CHECK] error: {e}")
        return False

# ---------- состояние пользователя --------------------------------
user_state: dict[str, dict[str, bool | str | None]] = {}

# ---------- фразы -------------------------------------------------
activation_phrases: Final = {
    "записать сон", "запиши сон", "запиши мой сон",
    "запись сна", "запись снов", "дневник снов",
    "запусти dreamember", "включи запись сна",
    "запусти дневник снов", "запусти запись сна",
    "запусти запись снов", "открой дневник снов",
}
help_phrases: Final = {"помощь", "help", "что ты умеешь", "как пользоваться"}

# ---------- /health -----------------------------------------------
@app.route("/health", methods=["GET", "HEAD"])
def health() -> Response:
    payload = {"status": "ok"}
    if request.method == "GET" and request.args.get("verbose") == "1":
        payload |= {"ts": int(time.time()), "users_in_mem": len(user_state)}
    return jsonify(payload)

# ----------------------------- WEBHOOK ----------------------------
@app.route("/webhook", methods=["POST"])
def handle_smartapp():
    data    = request.json or {}
    pl      = data.get("payload", {})
    user_id = data.get("uuid", {}).get("userId")
    text    = pl.get("message", {}).get("original_text", "").strip().lower()

    if not user_id:
        return jsonify(default_error(data))

    state = user_state.setdefault(user_id, {
        "welcomed": False,
        "awaiting": False,
        "awaiting_login": False,
        "awaiting_password": False,
        "temp_login": "",
    })

    if text == "":
        state["welcomed"] = False
        state["welcomed"] = True
        return jsonify(answer(
            "Привет! Я «Дримембер» — ваш личный дневник снов.\n"
            "Скажите «Запиши сон» или «Помощь», чтобы узнать команды.",
            data
        ))

    # первое приветствие
    if pl.get("intent") == "run_app" and not state["welcomed"]:
        state["welcomed"] = True
        return jsonify(answer(
            "Привет! Я «Дримембер» — ваш личный дневник снов.\n"
            "Скажите «Запиши сон» или «Помощь», чтобы узнать команды.",
            data
        ))

    # команда «помощь»
    if text in help_phrases:
        return jsonify(answer(
            "Я «Дримембер» — ваш дневник снов.\n"
            "1) Скажите «Запиши сон».\n"
            "2) Расскажите сон до 90 секунд.\n"
            "3) Позже посмотрите запись на сайте.",
            data
        ))

    # ввод логина (QoL: проверяем длину и допустимые символы)
    if state["awaiting_login"]:
        login_input = text
        if len(login_input) < 3 or not re.match(r'^[A-Za-z0-9_-]+$', login_input):
            # повторяем запрос логина
            return jsonify(answer(
                "Логин должен быть не менее 3 символов и состоять только из букв, цифр, \"-\" и \"_\"."
                " Назовите логин ещё раз.", data
            ))
        state["temp_login"] = login_input
        state["awaiting_login"] = False
        state["awaiting_password"] = True
        return jsonify(answer("Отлично! Теперь придумайте и скажите пароль.", data))

    # ввод пароля и регистрация (QoL: проверка пароля)
    if state["awaiting_password"]:
        password = text
        if len(password) < 8 or not re.match(r'^[A-Za-z0-9_-]+$', password):
            state["awaiting_password"] = True
            return jsonify(answer(
                "Пароль должен быть не короче 8 символов и состоять только из букв, цифр, \"-\" и \"_\"."
                " Придумайте пароль ещё раз.", data
            ))
        login = state.get("temp_login", "")
        state["awaiting_password"] = False
        try:
            resp = requests.post(
                "https://dreamember.onrender.com/api/user/registration",
                json={"login": login, "password": password, "deviceID": user_id},
                timeout=5
            )
            print(f"[register] POST → {resp.status_code}")
            if resp.ok:
                return jsonify(answer("Регистрация успешна! Скажите «Запиши сон».", data))
            else:
                # разбираем сообщение об ошибке
                err = ""
                try:
                    err = resp.json().get("message", "").lower()
                except Exception:
                    pass
                if "login" in err:
                    state["awaiting_login"] = True
                    return jsonify(answer("Логин занят. Назовите другой логин.", data))
                if "deviceid" in err or "колонки" in err or "привяз" in err:
                    state["awaiting"] = True
                    return jsonify(answer("Ваша колонка уже привязана. Расскажите сон.", data))
                state["awaiting_login"] = True
                return jsonify(answer("Не удалось зарегистрироваться. Назовите логин ещё раз.", data))
        except Exception as e:
            print(f"[register] error: {e}\n{traceback.format_exc()}")
            state["awaiting_login"] = True
            return jsonify(answer("Сервер регистрации недоступен, повторите позже.", data))

    # проверка привязки перед записью сна
    if text in activation_phrases:
        if not check_device_registered(user_id):
            state["awaiting_login"] = True
            return jsonify(answer(
                "Похоже, вы ещё не зарегистрированы. Назовите логин (e-mail или любое слово).",
                data
            ))
        state["awaiting"] = True
        return jsonify(answer("Готов записать сон. Начинайте.", data))

    # запись сна
    if state["awaiting"]:
        state["awaiting"] = False
        try:
            resp = requests.post(
                "https://dreamember.onrender.com/api/dream",
                json={"text": text, "deviceID": user_id},
                timeout=5
            )
            if resp.status_code in (401, 403):
                state["awaiting"] = True
                return jsonify(answer(
                    "Колонка не привязана. Введите ID на сайте и повторите команду.",
                    data
                ))
            resp.raise_for_status()
            return jsonify(answer(
                "Сон записан! Хорошего дня 🤍\n"
                "Просмотреть свои сны можно на сайте: <https://dreamember.onrender.com/>",
                data
            ))
        except Exception:
            state["awaiting"] = True
            print("❌ Ошибка при отправке сна:", traceback.format_exc())
            return jsonify(answer(
                "Сервер временно недоступен. Повторите попытку через минуту.",
                data
            ))

    # fallback
    return jsonify(answer(
        "Не расслышал команду. Скажите «Запиши сон» или «Помощь"
        , data
    ))

# ----------------- вспомогательные функции ------------------------
def answer(text: str, data: dict, end_session: bool = False) -> dict:
    return {
        "messageName": "ANSWER_TO_USER",
        "sessionId": data["sessionId"],
        "messageId": data["messageId"],
        "uuid": data["uuid"],
        "payload": {
            "pronounceText": text,
            "pronounceTextType": "application/text",
            "items": [{"bubble": {"text": text, "markdown": True}}],
            "auto_listening": False,
            "finished": end_session,
        },
    }

def default_error(data: dict) -> dict:
    return answer(
        "Произошла техническая ошибка. Попробуйте ещё раз позже.",
        data,
        end_session=True
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
