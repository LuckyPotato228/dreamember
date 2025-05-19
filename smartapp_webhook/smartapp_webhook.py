from flask import Flask, request, jsonify
import requests
import traceback

app = Flask(__name__)
sessions = {}

activation_phrases = {
    "запусти dreamember",
    "включи запись сна",
    "запиши сон",
    "запиши мой сон"
}


@app.route('/webhook', methods=['POST'])
def handle_smartapp():
    data = request.json
    print("🌐 Входящий JSON:", data)

    session_id = data.get("sessionId")
    user_id = data.get("uuid", {}).get("userId")
    text = data.get("payload", {}).get("message", {}).get("original_text", "").strip().lower()

    if not session_id or not text:
        print("⚠️ Некорректный запрос: нет sessionId или текста")
        return jsonify(default_error(data))

    # Активационная команда
    if text in activation_phrases:
        # сохраняем ожидание текста сна
        sessions[session_id] = {"awaiting_dream_text": True, "device_id": user_id}

        # сообщаем deviceID и просим зарегистрироваться
        msg = (
            f"Привет! Чтобы я могла записать твой сон, тебе нужно сначала зарегистрироваться на сайте. "
            f"Твой идентификатор: {user_id}. Введи его на странице регистрации и скажи свой сон снова."
        )
        print(f"🆔 Новый пользователь: {user_id}")
        return jsonify(answer(msg, data))

    # Сон, следующий после активации
    if session_id in sessions and sessions[session_id].get("awaiting_dream_text"):
        sessions[session_id]["awaiting_dream_text"] = False
        device_id = sessions[session_id]["device_id"]

        payload = {
            "text": text,
            "deviceID": device_id
        }

        try:
            print(f"📤 Пытаемся записать сон: '{text}' | deviceID = {device_id}")
            response = requests.post("https://dreamember.onrender.com/api/dream", json=payload)

            if response.status_code == 401 or response.status_code == 403:
                print("⚠️ Пользователь не зарегистрирован")
                return jsonify(answer(
                    f"Ты ещё не зарегистрирован. Твой идентификатор: {device_id}. Введи его на сайте и повтори команду.",
                    data
                ))

            response.raise_for_status()
            print("✅ Сон успешно записан")
            return jsonify(answer("Сон записан!", data))

        except Exception as e:
            print("❌ Ошибка при отправке сна:", traceback.format_exc())
            return jsonify(answer("Произошла ошибка при записи сна.", data))

    print("🤷 Нераспознанный ввод:", text)
    return jsonify(answer("Я не поняла. Скажи: 'Запиши мой сон'", data))


def answer(text, data):
    return {
        "messageName": "ANSWER_TO_USER",
        "sessionId": data.get("sessionId"),
        "messageId": data.get("messageId"),
        "payload": {
            "items": [{"bubble": {"text": text}}],
            "pronounceText": text,
            "end_session": False
        }
    }

def default_error(data):
    return answer("Ошибка в запросе. Повтори ещё раз.", data)


if __name__ == "__main__":
    app.run(port=8080)
