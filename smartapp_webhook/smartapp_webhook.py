from __future__ import annotations

import time
import traceback
from typing import Final

import requests
from flask import Flask, jsonify, request, Response

app = Flask(__name__)

# ---------- состояние пользователя --------------------------------
user_state: dict[str, dict[str, bool]] = {}

# --- АКТИВАЦИОННЫЕ ФРАЗЫ (как в Studio) ---------------------------
activation_phrases: Final = {
    "записать сон",
    "запиши сон",
    "запись сна",
    "запись снов",
    "дневник снов",
}
help_phrases: Final = {"помощь", "help", "что ты умеешь", "как пользоваться"}

# ------------------------------------------------------------------
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


# ----------------------------- WEBHOOK ----------------------------
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
        return jsonify(default_error(data))

    state = user_state.setdefault(user_id, {"awaiting": False, "registered": False})
    if not state["registered"]:
        state["registered"] = is_registered(user_id)

    # ---------- help ------------------------------------------------
    if text in help_phrases:
        return jsonify(
            answer(
                "Я «Дримембер» — дневник ваших снов. Чтобы сохранить сон:\n"
                "1) Скажите «Запиши сон» или «Дневник снов».\n"
                "2) Расскажите сон до 90 секунд.\n"
                "3) Посмотрите запись на dreamember.onrender.com.",
                data,
            )
        )

    # ---------- активация ------------------------------------------
    if text in activation_phrases:
        if state["awaiting"]:
            return jsonify(answer("Я слушаю. Расскажите ваш сон.", data))

        state["awaiting"] = True

        if state["registered"]:
            return jsonify(answer("Готов записать сон. Начинайте рассказывать.", data))

        # ссылка на корень сайта
        register_url = "<https://dreamember.onrender.com/>"

        msg_main = (
            "Привет! Я «Дримембер» — дневник снов.\n\n"
            "Перед первой записью нужно связать колонку с личным кабинетом.\n"
            "Откройте ссылку и нажмите «Регистрация»:\n"
            f"{register_url}\n\n"
            "После регистрации вернитесь и скажите «Запиши сон»."
        )

        # ответ: два bubble, второй — только ID
        payload = {
            "pronounceText": msg_main,
            "pronounceTextType": "application/text",
            "items": [
                {"bubble": {"text": msg_main, "markdown": True}},
                {"bubble": {"text": user_id, "markdown": False}},  # только ID
            ],
            "auto_listening": False,
        }

        return jsonify({
            "messageName": "ANSWER_TO_USER",
            "sessionId": data["sessionId"],
            "messageId": data["messageId"],
            "uuid": data["uuid"],
            "payload": payload,
        })

    # ---------- пришёл текст сна -----------------------------------
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
                        "Перейдите по ссылке из сообщения и попробуйте снова.",
                        data,
                    )
                )

            resp.raise_for_status()
            state["registered"] = True
            return jsonify(
                answer(
                    "Сон записан!\n\n"
                    "Посмотреть его можно на сайте: https://dreamember.onrender.com/",
                    data,
                )
            )

        except Exception:
            state["awaiting"] = True
            print("❌ Ошибка при отправке сна:", traceback.format_exc())
            return jsonify(
                answer(
                    "Сервер сейчас недоступен. Повторите попытку через минуту.",
                    data,
                )
            )

    # ---------- fallback -------------------------------------------
    return jsonify(
        answer(
            "Не расслышал. Скажите «Дневник снов» или «Помощь», чтобы узнать команды.",
            data,
        )
    )


# ----------------- утилиты ----------------------------------------
def answer(
    text: str,
    data: dict,
    suggestions: list[str] | None = None,
    end_session: bool = False,
) -> dict:
    payload = {
        "pronounceText": text,
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
        "Произошла техническая ошибка. Попробуйте ещё раз позже.", data, end_session=True
    )


if __name__ == "__main__":
    import os

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
