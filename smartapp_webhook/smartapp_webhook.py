from __future__ import annotations

import json           # <— добавили
import time
import traceback
from typing import Final

import requests
from flask import Flask, jsonify, request, Response

app = Flask(__name__)

# ---------- состояние пользователя --------------------------------
user_state: dict[str, dict[str, bool | str]] = {}

# --- фразы --------------------------------------------------------
activation_phrases: Final = {
    "записать сон", "запиши сон", "запиши мой сон",
    "запись сна", "запись снов", "дневник снов",
    "запусти dreamember", "включи запись сна",
}
help_phrases: Final = {"помощь", "help", "что ты умеешь", "как пользоваться"}

@app.route("/health", methods=["GET", "HEAD"])
def health() -> Response:
    payload = {"status": "ok"}
    if request.method == "GET" and request.args.get("verbose") == "1":
        payload |= {"ts": int(time.time()), "users_in_mem": len(user_state)}
    return jsonify(payload)

def is_registered(device_id: str) -> bool:
    try:
        r = requests.get(
            f"https://dreamember.onrender.com/api/device/{device_id}/exists",
            timeout=3,
        )
        return r.status_code == 200
    except requests.RequestException:
        return False

# ---- единый обработчик webhook -----------------------------------
@app.route("/webhook", methods=["POST"])
def handle_smartapp():
    data = request.json

    # 🤖 логируем полный запрос
    print("=== Incoming webhook ===")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    print("========================")

    # 1️⃣ Приветствие на старте новой сессии
    if data.get("new_session", False):
        return jsonify(answer(
            "Привет! Я «Дримембер» — ваш личный дневник снов.\n"
            "Скажите «Запиши сон» или «Помощь», чтобы узнать команды.",
            data
        ))

    # 2️⃣ Основная логика
    user_id = data.get("uuid", {}).get("userId")
    text = (
        data.get("payload", {})
            .get("message", {})
            .get("original_text", "")
            .strip()
            .lower()
    )
    if not user_id or not text:
        return jsonify(default_error(data))

    state = user_state.setdefault(user_id, {
        "awaiting": False,
        "registered": False,
        "awaiting_login": False,
        "awaiting_password": False,
        "temp_login": ""
    })
    if not state["registered"]:
        state["registered"] = is_registered(user_id)

    # — помощь —
    if text in help_phrases:
        return jsonify(answer(
            "Я «Дримембер» — ваш дневник снов.\n"
            "Чтобы начать:\n"
            "1) Скажите «Запиши сон».\n"
            "2) Расскажите сон до 90 секунд.\n"
            "3) Сохранённые записи можно будет увидеть на сайте.",
            data
        ))

    # — регистрация голосом (логин/пароль) —
    if not state["registered"] and not state["awaiting_login"] and not state["awaiting_password"]:
        if text in activation_phrases:
            state["awaiting_login"] = True
            return jsonify(answer(
                "Чтобы начать, придумайте и назовите, пожалуйста, логин (e-mail или любое слово).",
                data
            ))
    if state["awaiting_login"]:
        state["temp_login"] = text
        state["awaiting_login"] = False
        state["awaiting_password"] = True
        return jsonify(answer(
            "Отлично! Теперь придумайте и скажите пароль для входа.",
            data
        ))
    if state["awaiting_password"]:
        login = state["temp_login"]
        password = text
        state["awaiting_password"] = False
        try:
            resp = requests.post(
                "https://dreamember.onrender.com/api/registration",
                json={"login": login, "password": password, "deviceID": user_id},
                timeout=5,
            )
            print("Registration response:", resp.status_code, resp.text)
            if resp.ok:
                state["registered"] = True
                return jsonify(answer(
                    "Регистрация прошла успешно! Теперь скажите «Запиши сон», "
                    "чтобы я сохранил вашу первую запись.",
                    data
                ))
            else:
                state["awaiting_login"] = True
                return jsonify(answer(
                    "Не получилось зарегистрироваться. Возможно, логин занят. "
                    "Придумайте другой логин и скажите его.",
                    data
                ))
        except Exception:
            print("Registration error:", traceback.format_exc())
            state["awaiting_login"] = True
            return jsonify(answer(
                "Сервер регистрации недоступен. Попробуйте назвать логин чуть позже.",
                data
            ))

    # — активация записи сна —
    if text in activation_phrases:
        if state["awaiting"]:
            return jsonify(answer("Я слушаю. Расскажите ваш сон.", data))
        state["awaiting"] = True
        return jsonify(answer("Готов записать сон. Начинайте рассказывать.", data))

    # — сохранение текста сна —
    if state["awaiting"]:
        state["awaiting"] = False
        payload = {"text": text, "deviceID": user_id}
        try:
            resp = requests.post(
                "https://dreamember.onrender.com/api/dream",
                json=payload,
                timeout=5,
            )
            print("Dream save response:", resp.status_code, resp.text)
            resp.raise_for_status()
            return jsonify(answer("Сон записан! Хороших снов 🤍", data))
        except Exception:
            print("Dream save error:", traceback.format_exc())
            state["awaiting"] = True
            return jsonify(answer("Не смог сохранить сон. Попробуйте ещё раз через минуту.", data))

    # — fallback —
    return jsonify(answer(
        "Не расслышал. Скажите «Запиши сон» или «Помощь», чтобы узнать команды.",
        data
    ))

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
    return answer("Ошибка в запросе. Повтори ещё раз.", data, end_session=True)

if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
