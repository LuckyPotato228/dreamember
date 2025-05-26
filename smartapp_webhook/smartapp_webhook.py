from __future__ import annotations
import json
import time
import traceback
from typing import Final

import requests
from flask import Flask, jsonify, request, Response

app = Flask(__name__)

# ---------- состояние пользователя --------------------------------
# теперь в state храним ещё и token
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

def is_registered(device_id: str) -> bool:
    """
    Проверяем существование аккаунта по сохранённому токену:
    GET /api/user/auth с заголовком Authorization: Bearer <token>.
    Если 200 — пользователь авторизован (deviceID в базе).
    Иначе — не зарегистрирован/токен недействителен.
    """
    state = user_state.get(device_id, {})
    token = state.get("token")
    if not token:
        # токена нет — явно не регистрировали
        return False

    try:
        resp = requests.get(
            "https://dreamember.onrender.com/api/user/auth",
            headers={"Authorization": f"Bearer {token}"},
            timeout=3
        )
        print(f"[is_registered] GET /api/user/auth → {resp.status_code}")
        return resp.status_code == 200
    except requests.RequestException as e:
        print(f"[is_registered] network error: {e}")
        return False

# ----------------------------- WEBHOOK ----------------------------
@app.route("/webhook", methods=["POST"])
def handle_smartapp():
    data = request.json or {}
    pl = data.get("payload", {})
    user_id = data.get("uuid", {}).get("userId")
    text = pl.get("message", {}).get("original_text", "").strip().lower()

    # если нет userId или пустой текст — возвращаем ошибку
    if not user_id or not text:
        return jsonify(default_error(data))

    # инициализируем состояние для этого user_id
    state = user_state.setdefault(user_id, {
        "welcomed": False,
        "awaiting": False,
        "awaiting_login": False,
        "awaiting_password": False,
        "temp_login": "",
        "token": None,        # здесь будем хранить JWT
        "registered": False,  # флаг регистрации
    })

    # первое приветствие
    if pl.get("intent") == "run_app" and not state["welcomed"]:
        state["welcomed"] = True
        return jsonify(answer(
            "Привет! Я «Дримембер» — ваш личный дневник снов.\n"
            "Скажите «Запиши сон» или «Помощь», чтобы узнать команды.",
            data
        ))

    # обновляем флаг registered на основании токена
    state["registered"] = is_registered(user_id)
    registered = state["registered"]

    # команда «помощь»
    if text in help_phrases:
        return jsonify(answer(
            "Я «Дримембер» — ваш дневник снов.\n"
            "1) Скажите «Запиши сон».\n"
            "2) Расскажите сон до 90 секунд.\n"
            "3) Позже посмотрите запись на сайте.",
            data
        ))

    # если не зарегистрирован и сказал «запиши сон» — запускаем регистрацию
    if not registered and text in activation_phrases \
       and not state["awaiting_login"] and not state["awaiting_password"]:
        state["awaiting_login"] = True
        return jsonify(answer(
            "Похоже, вы ещё не зарегистрированы. Назовите логин (e-mail или любое слово).",
            data
        ))

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
            print(f"[register] POST /user/registration → {resp.status_code}")
            if resp.ok:
                body = resp.json()
                # сохраняем токен из ответа
                state["token"] = body.get("token")
                state["registered"] = True
                return jsonify(answer("Регистрация успешна! Скажите «Запиши сон».", data))
            else:
                state["awaiting_login"] = True
                return jsonify(answer("Логин занят. Назовите другой логин.", data))
        except Exception as e:
            print(f"[register] error: {e}\n{traceback.format_exc()}")
            state["awaiting_login"] = True
            return jsonify(answer("Сервер регистрации недоступен, повторите позже.", data))

    # если зарегистрирован и «запиши сон» — включаем режим прослушивания
    if registered and text in activation_phrases:
        if state["awaiting"]:
            return jsonify(answer("Я слушаю. Расскажите сон.", data))
        state["awaiting"] = True
        return jsonify(answer("Готов записать сон. Начинайте.", data))

    # сохраняем текст сна
    if state["awaiting"]:
        state["awaiting"] = False
        try:
            resp = requests.post(
                "https://dreamember.onrender.com/api/dream",
                json={"text": text, "deviceID": user_id},
                timeout=5
            )
            print(f"[dream] POST /api/dream → {resp.status_code}")
            resp.raise_for_status()
            return jsonify(answer(
                "Сон записан! Хорошего вам дня 🤍\n"
                "Просмотреть свои сны можно на сайте: https://dreamember.onrender.com/",
                data
            ))
        except Exception as e:
            print(f"[dream] error: {e}\n{traceback.format_exc()}")
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
