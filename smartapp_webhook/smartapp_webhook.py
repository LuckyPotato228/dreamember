from __future__ import annotations

import time
import traceback
from typing import Final

import requests
from flask import Flask, jsonify, request, Response

app = Flask(__name__)

# ------------- состояние пользователя (id → dict) -----------------
user_state: dict[str, dict[str, bool]] = {}

activation_phrases: Final = {
    "запусти dreamember",
    "включи запись сна",
    "запиши сон",
    "запиши мой сон",
}
help_phrases: Final = {"помощь", "help", "что ты умеешь", "как пользоваться"}

# -------------------------------------------------------------------
@app.route("/health", methods=["GET", "HEAD"])
def health() -> Response:
    """Пинг для Render/Sber."""
    payload = {"status": "ok"}
    if request.method == "GET" and request.args.get("verbose") == "1":
        payload["ts"] = int(time.time())
        payload["users_in_mem"] = len(user_state)
    return jsonify(payload)


def is_registered(device_id: str) -> bool:
    """
    Проверка, есть ли колонка в БД сайта.
    """
    try:
        r = requests.get(
            f"https://dreamember.onrender.com/api/device/{device_id}/exists",
            timeout=3,
        )
        return r.status_code == 200
    except requests.RequestException:
        return False


# ------------------------- WEBHOOK ---------------------------------
@app.route("/webhook", methods=["POST"])
def handle_smartapp():
    data = request.json
    print("🌐 Входящий JSON:", data)

    user_id = data.get("uuid", {}).get("userId")
    text = (
        data.get("payload", {})
        .get("message", {})
        .get("original_text", "")
        .strip()
        .lower()
    )

    if not user_id or not text:
        print("⚠️ Нет userId или текста")
        return jsonify(default_error(data))

    state = user_state.setdefault(user_id, {"awaiting": False, "registered": False})

    # разовая проверка регистрации
    if not state["registered"]:
        state["registered"] = is_registered(user_id)

    # ---------- команда ПОМОЩЬ ------------------------------------
    if text in help_phrases:
        return jsonify(
            answer(
                "Я дневник снов Dreamember. Чтобы сохранить сон: \n"
                "1) Скажите «Запиши мой сон».\n"
                "2) Расскажите сон голосом (до 90 с).\n"
                "3) Посмотрите запись на сайте dreamember.onrender.com.",
                data,
            )
        )

    # ---------- активационные фразы --------------------------------
    if text in activation_phrases:
        # если уже ждём сон
        if state["awaiting"]:
            return jsonify(
                answer(
                    "Я могу записать ваш сон. Просто расскажите его.",
                    data,
                )
            )

        state["awaiting"] = True

        if state["registered"]:
            # зарегистрирован ⇒ сразу ждём текст сна
            return jsonify(
                answer(
                    "Могу записать ваш сон. Расскажите, что вам снилось.",
                    data,
                )
            )

        # регистрация ещё не выполнена
        msg = (
            "Привет! Я Dreamember — дневник снов.\n\n"
            "Чтобы сохранить ваш сон, сначала зарегистрируйтесь на сайте "
            "<https://dreamember.onrender.com/>.\n"
            f"Ваш идентификатор устройства: **{user_id}**.\n"
            "Введите его на сайте, затем вернитесь и скажите: «Запиши мой сон»."
        )
        return jsonify(answer(msg, data))

    # ---------- получили текст сна ---------------------------------
    if state["awaiting"]:
        state["awaiting"] = False
        payload = {"text": text, "deviceID": user_id}

        try:
            resp = requests.post(
                "https://dreamember.onrender.com/api/dream", json=payload, timeout=5
            )

            if resp.status_code in (401, 403):
                state["awaiting"] = True
                return jsonify(
                    answer(
                        "Похоже, вы ещё не завершили регистрацию. "
                        f"Идентификатор устройства: {user_id}. "
                        "Введите его на сайте и повторите команду.",
                        data,
                    )
                )

            resp.raise_for_status()
            state["registered"] = True
            return jsonify(
                answer(
                    "Сон записан! \n\n"
                    "Посмотреть запись можно на сайте: <https://dreamember.onrender.com/>",
                    data,
                )
            )

        except Exception:
            state["awaiting"] = True
            print("❌ Ошибка при отправке сна:", traceback.format_exc())
            return jsonify(
                answer(
                    "Сервер временно недоступен. Повторите попытку через минуту.",
                    data,
                )
            )

    # ---------- fallback -------------------------------------------
    return jsonify(
        answer(
            "Я не расслышал команду. "
            "Скажите «Запиши мой сон» или «Помощь», чтобы узнать подробности.",
            data,
        )
    )


# ---------------- вспомогательные функции -------------------------
def answer(text: str, data: dict, end_session: bool = False) -> dict:
    """Корректный ANSWER_TO_USER для SmartApp"""
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
        "Произошла техническая ошибка. Попробуйте ещё раз позже.", data, end_session=True
    )


if __name__ == "__main__":
    import os

    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)