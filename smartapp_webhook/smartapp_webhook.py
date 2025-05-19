from flask import Flask, request, jsonify
import requests

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
    session_id = data.get("sessionId")
    user_id = data.get("uuid", {}).get("userId")
    text = data.get("payload", {}).get("message", {}).get("original_text", "").strip().lower()

    if not session_id or not text:
        return jsonify(default_error(data))

    # Если это активационная фраза — запоминаем ожидание сна
    if text in activation_phrases:
        sessions[session_id] = {"awaiting_dream_text": True, "device_id": user_id}
        return jsonify(answer("Хорошо, я готова записать сон. Говори!", data))

    # Если это следующее сообщение — сон
    if session_id in sessions and sessions[session_id].get("awaiting_dream_text"):
        sessions[session_id]["awaiting_dream_text"] = False
        device_id = sessions[session_id]["device_id"]

        payload = {
            "text": text,
            "deviceID": device_id
        }

        try:
            requests.post("https://dreamember.onrender.com/api/dream", json=payload)
        except Exception as e:
            return jsonify(answer("Произошла ошибка при записи сна.", data))

        return jsonify(answer("Сон записан!", data))

    # Всё остальное — нераспознанное
    return jsonify(answer("Я не поняла. Скажи: 'Запиши мой сон'", data))


# Функция генерации ответа
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

# Функция дефолтной ошибки
def default_error(data):
    return answer("Ошибка в запросе. Повтори ещё раз.", data)


if __name__ == "__main__":
    app.run(port=8080)
