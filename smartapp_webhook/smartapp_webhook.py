from flask import Flask, request, jsonify
import requests
import traceback

app = Flask(__name__)
sessions = {}

activation_phrases = {
    "запусти dreamember",
    "включи запись сна",
    "запиши сон",
    "запиши мой сон",
}


@app.route("/webhook", methods=["POST"])
def handle_smartapp():
    data = request.json
    print("🌐 Входящий JSON:", data)

    session_id = data.get("sessionId")
    user_id = data.get("uuid", {}).get("userId")
    text = (
        data.get("payload", {})
        .get("message", {})
        .get("original_text", "")
        .strip()
        .lower()
    )

    # если нет sessionId или текста → шлём спец-ошибку ассистенту
    if not session_id or not text:
        print("⚠️ Некорректный запрос: нет sessionId или текста")
        return jsonify(default_error(data))

    # ── 1. Активационная команда ────────────────────────────────
    if text in activation_phrases:
        # помечаем сессию, что ждём следующий текст (сам сон)
        sessions[session_id] = {"awaiting_dream_text": True, "device_id": user_id}

        msg = (
            f"Привет! Чтобы я могла записать твой сон, зарегистрируйся на сайте "
            f"<https://dreamember.onrender.com/>.\n\n"
            f"Твой идентификатор: **{user_id}**.\n"
            f"Когда закончишь регистрацию, снова скажи: «Запиши мой сон»."
        )
        print(f"🆔 Новый пользователь: {user_id}")
        return jsonify(answer(msg, data))

    # ── 2. Пришёл текст сна после активации ──────────────────────
    if sessions.get(session_id, {}).get("awaiting_dream_text"):
        sessions[session_id]["awaiting_dream_text"] = False
        device_id = sessions[session_id]["device_id"]

        payload = {"text": text, "deviceID": device_id}

        try:
            print(f"📤 Записываем сон: '{text}' | deviceID = {device_id}")
            resp = requests.post(
                "https://dreamember.onrender.com/api/dream", json=payload, timeout=5
            )

            # пользователь ещё не зарегистрирован на сайте → просим его сделать это
            if resp.status_code in (401, 403):
                print("⚠️ Пользователь не зарегистрирован")
                return jsonify(
                    answer(
                        f"Ты ещё не зарегистрирован. Твой идентификатор: {device_id}. "
                        "Введи его на сайте и повтори команду.",
                        data,
                    )
                )

            resp.raise_for_status()
            print("✅ Сон успешно записан")
            return jsonify(answer("Сон записан! Хороших снов 🤍", data))

        except Exception:
            print("❌ Ошибка при отправке сна:", traceback.format_exc())
            return jsonify(answer("Произошла ошибка при записи сна.", data))

    # ── 3. Всё остальное — непонятный ввод ───────────────────────
    print("🤷 Нераспознанный ввод:", text)
    return jsonify(answer("Я не поняла. Скажи: «Запиши мой сон»", data))


# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ -----------------------------------------
def answer(text: str, data: dict, end_session: bool = False) -> dict:
    """
    Формирует валидный для SmartApp API ANSWER_TO_USER.
    Главное:
      • в корне есть uuid
      • в payload.items[0].bubble лежит текст
    """
    return {
        "messageName": "ANSWER_TO_USER",
        "sessionId": data["sessionId"],
        "messageId": data["messageId"],
        "uuid": data["uuid"],  # <-- ЭТО поле раньше отсутствовало
        "payload": {
            "pronounceText": text,
            "pronounceTextType": "application/text",
            "items": [
                {
                    "bubble": {
                        "text": text,
                        # markdown-разметку включаем, чтобы ссылка подсветилась
                        "markdown": True,
                    }
                }
            ],
            # если хочешь, добавь кнопки-подсказки
            # "suggestions": {"buttons": [{"title": "Запиши сон"}]},
            "auto_listening": False,
            "finished": end_session,
        },
    }


def default_error(data: dict) -> dict:
    return answer("Ошибка в запросе. Повтори ещё раз.", data, end_session=True)


if __name__ == "__main__":
    # в проде — gunicorn, а не встроенный сервер Flask
    app.run(port=8080)
