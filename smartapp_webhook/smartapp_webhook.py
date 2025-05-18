import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def handle_chatapp():
    data = request.json
    print("🌐 Входящий JSON:", data)

    try:
        text = data["payload"]["message"]["original_text"]
        device_id = data["uuid"].get("userId", "chatapp-test")

        print(f"📥 Текст: '{text}', device_id: {device_id}")

        payload = {
            "text": text,
            "deviceID": device_id
        }

        response = requests.post("https://dreamember.onrender.com/api/dream", json=payload)
        response.raise_for_status()

        print("✅ Сон записан:", payload)

        return jsonify({
            "messageName": "ANSWER_TO_USER",
            "sessionId": data["sessionId"],
            "messageId": data["messageId"],
            "uuid": data["uuid"],
            "payload": {
                "items": [
                    {
                        "bubble": {
                            "text": "Сон записан! Посмотри его на сайте dreamember.onrender.com"
                        }
                    }
                ],
                "end_session": False
            }
        })

    except Exception as e:
        print("❌ Ошибка обработки запроса:", e)
        return jsonify({
            "messageName": "ANSWER_TO_USER",
            "sessionId": data.get("sessionId", ""),
            "messageId": data.get("messageId", ""),
            "uuid": data.get("uuid", {}),
            "payload": {
                "items": [
                    {
                        "bubble": {
                            "text": "Произошла ошибка при записи сна."
                        }
                    }
                ],
                "end_session": False
            }
        })

if __name__ == "__main__":
    app.run(port=8080)
