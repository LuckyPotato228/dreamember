from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def handle_smartapp():
    data = request.json
    print("🌐 Входящий JSON:", data)

    try:
        text = data["request"]["original_utterance"]
        device_id = data["meta"]["client_id"]
    except KeyError as e:
        print("❌ Ключ не найден:", e)
        return jsonify({
            "version": data.get("version", "1.0"),
            "session": data.get("session", {}),
            "response": {
                "text": "Не могу обработать запрос, повтори пожалуйста ещё раз.",
                "end_session": False
            }
        })

    payload = {
        "text": text,
        "deviceID": device_id
    }

    try:
        response = requests.post("https://dreamember.onrender.com/api/dream", json=payload)
        response.raise_for_status()
        print("✅ Сон успешно отправлен:", payload)

        return jsonify({
            "version": data["version"],
            "session": data["session"],
            "response": {
                "text": "Сон записан! Посмотри его на сайте dreamember точка onrender точка ком.",
                "end_session": False
            }
        })

    except Exception as e:
        print("🔥 Ошибка при отправке сна:", e)
        return jsonify({
            "version": data["version"],
            "session": data["session"],
            "response": {
                "text": "Произошла ошибка при записи сна.",
                "end_session": False
            }
        })

if __name__ == "__main__":
    app.run(port=8080)
