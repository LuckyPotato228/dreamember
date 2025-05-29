from __future__ import annotations
import os, json, time, traceback, re
from typing import Final
import psycopg2
from psycopg2 import sql
import requests
from flask import Flask, jsonify, request, Response

app = Flask(__name__)

# ---------- Postgres ----------------------------------------------
DB_HOST, DB_PORT = os.getenv("DB_HOST"), os.getenv("DB_PORT")
DB_NAME, DB_USER = os.getenv("DB_NAME"), os.getenv("DB_USER")
DB_PASSWORD      = os.getenv("DB_PASSWORD")

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
                (device_id,))
            return cur.fetchone() is not None
    except Exception as e:
        print("[DB CHECK] error:", e)
        return False

# ---------- helpers: tone -----------------------------------------
def tone(data: dict) -> str:
    """возвращает 'ty' для Joy, 'vy' для Sber/Athena"""
    char_id = data.get("payload", {}).get("character", {}).get("id", "sber")
    return "ty" if char_id == "joy" else "vy"

def adapt(template: str, t: str) -> str:
    """подменяет {you}/{your} в шаблоне"""
    repl = {"ty": ("тебе", "твой"), "vy": ("вам", "ваш")}
    return template.replace("{you}", repl[t][0]).replace("{your}", repl[t][1])

URL_RE = re.compile(r'https?://\S+|<[^>]+>')

# ---------- runtime state -----------------------------------------
user_state: dict[str, dict[str, bool | str]] = {}

# ---------- phrases ----------------------------------------------
activation_phrases: Final = {
    "записать сон", "запиши сон", "запиши мой сон",
    "запись сна", "запись снов", "дневник снов",
    "запусти dreamember", "включи запись сна",
    "запусти дневник снов", "запусти запись сна",
    "запусти запись снов", "открой дневник снов",
}
help_phrases: Final = {"помощь", "help", "что ты умеешь", "как пользоваться"}

# ---------------- health ------------------------------------------
@app.route("/health", methods=["GET","HEAD"])
def health() -> Response:
    payload = {"status": "ok"}
    if request.method == "GET" and request.args.get("verbose") == "1":
        payload |= {"ts": int(time.time()), "users_in_mem": len(user_state)}
    return jsonify(payload)

# ---------------- webhook -----------------------------------------
@app.route("/webhook", methods=["POST"])
def handle_smartapp():
    data    = request.json or {}
    pl      = data.get("payload", {})
    user_id = data.get("uuid", {}).get("userId")
    text    = pl.get("message", {}).get("original_text", "").strip().lower()
    t       = tone(data)

    # лог
    print("=== Incoming webhook ===")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    print("========================")

    if not user_id:
        return jsonify(default_error(data))

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

    # welcome
    if pl.get("intent") == "run_app" and not state["welcomed"]:
        state["welcomed"] = True
        return jsonify(answer(
            adapt("Привет! Я «Дримембер» — {your} дневник снов.\n"
                  "Скажи «Запиши сон» или «Помощь», чтобы я помог {you}.", t),
            data
        ))

    if text == "":
        return jsonify(answer(
            adapt("Привет! Я «Дримембер». Скажи «Запиши сон».", t),
            data
        ))

    # help
    if text in help_phrases:
        return jsonify(answer(
            adapt("Я «Дримембер».\n"
                  "1) Скажи «Запиши сон».\n"
                  "2) Расскажи сон (до 90 сек.) — я сохраню для {you}.", t),
            data
        ))

    # --- регистрация ---
    if (not state["registered"]
        and not state["awaiting_login"]
        and not state["awaiting_password"]
        and text in activation_phrases):
        state["awaiting_login"] = True
        return jsonify(answer(
            adapt("Назови логин (строчные латинские буквы/цифры).", t),
            data
        ))

    if state["awaiting_login"]:
        login_input = text
        if len(login_input) < 3 or not re.fullmatch(r'[a-z0-9_-]+', login_input):
            return jsonify(answer(
                adapt("Логин ≥3 символа, только буквы/цифры, «-», «_». Повтори логин.", t),
                data
            ))
        state["temp_login"] = login_input
        state["awaiting_login"] = False
        state["awaiting_password"] = True
        return jsonify(answer(
            adapt("Теперь придумай пароль (≥8 символов).", t),
            data
        ))

    if state["awaiting_password"]:
        password = text
        if len(password) < 8 or not re.fullmatch(r'[A-Za-z0-9_-]+', password):
            return jsonify(answer(
                adapt("Пароль ≥8 символов, латиница/цифры. Повтори пароль.", t),
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
                return jsonify(answer(
                    adapt("Регистрация успешна! Скажи «Запиши сон».", t), data))
            state["awaiting_login"] = True
            return jsonify(answer(
                adapt("Логин занят. Назови другой логин.", t), data))
        except Exception:
            state["awaiting_login"] = True
            print("[REGISTER] error:", traceback.format_exc())
            return jsonify(answer(
                adapt("Сервер регистрации недоступен, повтори позже.", t), data))

    # --- запись сна ---
    if text in activation_phrases:
        if not state["registered"]:
            state["awaiting_login"] = True
            return jsonify(answer(
                adapt("Устройство не привязано. Назови логин.", t), data))
        if state["awaiting"]:
            return jsonify(answer(adapt("Я слушаю, расскажи сон.", t), data))
        state["awaiting"] = True
        return jsonify(answer(adapt("Готов записать сон. Начинай.", t), data))

    if state["awaiting"]:
        state["awaiting"] = False
        try:
            resp = requests.post(
                "https://dreamember.onrender.com/api/dream",
                json={"text": text, "deviceID": user_id},
                timeout=5)
            resp.raise_for_status()
            return jsonify(answer(
                adapt("Сон записан! Хорошего дня 🤍\n"
                      "Посмотреть записи можно на <https://dreamember.onrender.com/>", t),
                data))
        except Exception:
            state["awaiting"] = True
            print("[DREAM SAVE] error:", traceback.format_exc())
            return jsonify(answer(
                adapt("Сервер недоступен, повтори позже.", t), data))

    # fallback
    return jsonify(answer(
        adapt("Не расслышал. Скажи «Запиши сон» или «Помощь».", t), data
    ))

# ------------------- answer & default -----------------------------
def answer(text: str, data: dict, end_session: bool = False) -> dict:
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
    return answer(
        adapt("Техническая ошибка. Попробуй ещё раз.", tone(data)), data, end_session=True
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
