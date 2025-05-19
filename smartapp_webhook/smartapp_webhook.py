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
    print("🌐 Входящий JSON:", data)  # Лог входящего запроса

    session_id = data.get("sessionId")
    user_id = data.get("uuid", {}).get("userId")
    text = data.get("payload", {}).get("message", {}).get("original_text", "").strip().lower()

    if not session_id or not text:
        print("⚠️ Некорректный запрос: отсутствует sessionId или текст")
        return jsonify(default_error(data))

    # Если активационная фраза — запоминаем сессию
    if text in activation_phrases:
        sessions[session_id] = {"awaiting_dream_text": True, "device_id": user_id}
        print(f"📌 Активационная фраза: '{text}', user_id = {user_id}")
        return jsonify(answer("Хорошо, я готова записать сон. Говори!", data))

    # Если ждем текст сна
    if session_id in sessions and sessions[session_id].get("awaiting_dream_text"):
        sessions[session_id]["awaiting_dream_text"] = False
        device_id = sessions[session_id]["device_id"]

        payload = {
            "text": text,
            "deviceID": device_id
        }

        try:
            print(f"📤 Отправка сна: '{text}' | deviceID = {device_id}")
            response = requests.post("https://dreamember.onrender.com/api/dream", json=payload)
            response.raise_for_status()
            print(f"✅ Сон успешно записан (status {response.status_code})")
            return jsonify(answer("Сон записан!", data))
        except Exception as e:
            print("❌ Ошибка при отправке сна:", traceback.format_exc())
            return jsonify(answer("Произошла ошибка при записи сна.", data))

    print(f"🤷 Нераспознанный ввод: '{text}'")
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
