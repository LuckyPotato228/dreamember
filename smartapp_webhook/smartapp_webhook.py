from __future__ import annotations
import json, time, traceback
from typing import Final

import requests
from flask import Flask, jsonify, request, Response

app = Flask(__name__)

# ---------- состояние пользователя --------------------------------
# убрали флаг "registered" — будем брать актуальный с сервера
user_state: dict[str, dict[str, bool | str]] = {}

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

def is_registered(device_id: str) -> bool:
    """
    Проверяет на сервере, зарегистрирован ли пользователь с данным device_id.
    Ожидаем JSON {"exists": true/false} при 200 или 404, если нет.
    """
    try:
        resp = requests.get(
            f"https://dreamember.onrender.com/api/user/exists/{device_id}",
            timeout=3
        )
        # если 404 — считаем незарегистрированным
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        data = resp.json()
        return bool(data.get("exists", False))
    except requests.RequestException:
        # в случае ошибки сети/таймаута вернём False (предложим регистрироваться позже)
        return False

# ----------------------------- WEBHOOK ----------------------------
@app.route("/webhook", methods=["POST"])
def handle_smartapp():
    data = request.json or {}
    pl = data.get("payload", {})
    user_id = data.get("uuid", {}).get("userId")

    # если нет user_id или текстовой части — выходим с ошибкой
    text = pl.get("message", {}).get("original_text", "").strip().lower()
    if not user_id or not text:
        return jsonify(default_error(data))

    # инициализируем состояние сессии
    state = user_state.setdefault(user_id, {
        "welcomed": False,   # приветствие один раз
        "awaiting": False,   # ждём, когда пользователь расскажет сон
        "awaiting_login": False,
        "awaiting_password": False,
        "temp_login": ""
    })

    # первая проверка: приветствие при запуске
    if pl.get("intent") == "run_app" and not state["welcomed"]:
        state["welcomed"] = True
        return jsonify(answer(
            "Привет! Я «Дримембер» — ваш личный дневник снов.\n"
            "Скажите «Запиши сон» или «Помощь», чтобы узнать команды.",
            data
        ))

    # *** проверяем регистрацию каждый раз ***
    registered = is_registered(user_id)

    # команда помощи
    if text in help_phrases:
        return jsonify(answer(
            "Я «Дримембер» — ваш дневник снов.\n"
            "1) Скажите «Запиши сон».\n"
            "2) Расскажите сон до 90 секунд.\n"
            "3) Позже посмотрите запись на сайте.",
            data
        ))

    # если не зарегистрирован и пользователь хочет «записать сон» — начинаем flow регистрации
    if not registered and text in activation_phrases and not state["awaiting_login"] and not state["awaiting_password"]:
        state["awaiting_login"] = True
        return jsonify(answer("Похоже, вы ещё не зарегистрированы. Назовите логин (e-mail или любое слово).", data))

    # ввод логина
    if state["awaiting_login"]:
        state["temp_login"] = text
        state["awaiting_login"] = False
        state["awaiting_password"] = True
        return jsonify(answer("Отлично! Теперь придумайте и скажите пароль.", data))

    # ввод пароля и регистрация на сервере
    if state["awaiting_password"]:
        login, password = state["temp_login"], text
        state["awaiting_password"] = False
        try:
            resp = requests.post(
                "https://dreamember.onrender.com/api/user/registration",
                json={"login": login, "password": password, "deviceID": user_id},
                timeout=5
            )
            if resp.ok:
                return jsonify(answer("Регистрация успешна! Скажите «Запиши сон».", data))
            else:
                # логин занят
                state["awaiting_login"] = True
                return jsonify(answer("Логин занят. Назовите другой логин.", data))
        except Exception:
            state["awaiting_login"] = True
            return jsonify(answer("Сервер регистрации недоступен, повторите позже.", data))

    # если зарегистрирован и фраза активации — начинаем запись сна
    if registered and text in activation_phrases:
        if state["awaiting"]:
            return jsonify(answer("Я слушаю. Расскажите сон.", data))
        state["awaiting"] = True
        return jsonify(answer("Готов записать сон. Начинайте.", data))

    # если уже в режиме ожидания текста сна — сохраняем
    if state["awaiting"]:
        state["awaiting"] = False
        try:
            resp = requests.post(
                "https://dreamember.onrender.com/api/dream",
                json={"text": text, "deviceID": user_id},
                timeout=5
            )
            resp.raise_for_status()
            return jsonify(answer(
                "Сон записан! Хорошего вам дня 🤍\n"
                "Просмотреть свои сны можно на сайте: https://dreamember.onrender.com/",
                data
            ))
        except Exception:
            state["awaiting"] = True
            return jsonify(answer("Не смог сохранить сон. Повторите позже.", data))

    # fallback
    return jsonify(answer("Не расслышал. Скажите «Запиши сон» или «Помощь».", data))

# ----------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ------------------------
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
    return answer("Ошибка в запросе. Повторите ещё раз.", data, end_session=True)

if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
