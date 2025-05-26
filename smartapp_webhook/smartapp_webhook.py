from __future__ import annotations
import json
import time
import traceback
from typing import Final

import requests
from flask import Flask, jsonify, request, Response

app = Flask(__name__)

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
    data = request.json or {}
    pl = data.get("payload", {})
    user_id = data.get("uuid", {}).get("userId")
    text = pl.get("message", {}).get("original_text", "").strip().lower()

    # если нет userId — сразу ошибка
    if not user_id:
        return jsonify(default_error(data))

    # инициализируем состояние для user_id
    state = user_state.setdefault(user_id, {
        "welcomed": False,
        "awaiting": False,         # ждём рассказа сна
        "awaiting_login": False,   # ждём логин для регистрации
        "awaiting_password": False,# ждём пароль для регистрации
        "temp_login": "",          # временный логин
        "registered": False,       # будем проверять при отправке сна
    })

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

    # если не зарегистрирован и сказал «запиши сон» — запускаем регистрацию
    if not state["registered"] and text in activation_phrases \
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
            print(f"[register] POST → {resp.status_code}")
            if resp.ok:
                state["registered"] = True
                return jsonify(answer("Регистрация успешна! Скажите «Запиши сон».", data))
            else:
                state["awaiting_login"] = True
                return jsonify(answer("Логин занят. Назовите другой логин.", data))
        except Exception as e:
            print(f"[register] error: {e}\n{traceback.format_exc()}")
            state["awaiting_login"] = True
            return jsonify(answer("Сервер регистрации недоступен, повторите позже.", data))

    # ———————— запись сна и ленивый чек регистрации ————————
    if state["awaiting"]:
        state["awaiting"] = False
        try:
            resp = requests.post(
                "https://dreamember.onrender.com/api/dream",
                json={"text": text, "deviceID": user_id},
                timeout=5
            )
            # именно здесь проверяем, привязан ли deviceID:
            # если сервер ответил 401 или 403 — значит колонка ещё не привязана
            if resp.status_code in (401, 403):
                state["awaiting"] = True
                return jsonify(answer(
                    "Похоже, вы ещё не завершили регистрацию. "
                    f"Идентификатор устройства: {user_id}. "
                    "Введите его на сайте и повторите команду.",
                    data
                ))

            # иначе либо 400 (BadRequest при невалидном текстe), либо 200 → считаем удачно записано
            resp.raise_for_status()
            state["registered"] = True
            return jsonify(answer(
                "Сон записан! Хорошего вам дня 🤍\n"
                "Посмотреть свои сны можно на сайте: <https://dreamember.onrender.com/>",
                data
            ))
        except Exception:
            state["awaiting"] = True
            print("❌ Ошибка при отправке сна:", traceback.format_exc())
            return jsonify(answer(
                "Сервер временно недоступен. Повторите попытку через минуту.",
                data
            ))

    # если сказали «запиши сон» — входим в режим ожидания текста
    if text in activation_phrases:
        state["awaiting"] = True
        return jsonify(answer("Готов записать сон. Начинайте.", data))

    # fallback
    return jsonify(answer(
        "Я не расслышал команду. Скажите «Запиши сон» или «Помощь».",
        data
    ))


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
    return answer(
        "Произошла техническая ошибка. Попробуйте ещё раз позже.",
        data,
        end_session=True
    )


if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
