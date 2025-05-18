import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def handle_smartapp():
    data = request.json
    text = data["request"]["original_utterance"]
    device_id = data["meta"]["client_id"]  # или временно: device_id = "91"

    payload = {
        "text": text,
        "deviceID": device_id
    }

    try:
        response = requests.post("https://dreamember.onrender.com/api/dream", json=payload)
        response.raise_for_status()

        return jsonify({
            "version": data["version"],
            "session": data["session"],
            "response": {
                "text": "Сон записан! Посмотри его на сайте dreamember точка onrender точка ком.",
                "end_session": False
            }
        })

    except Exception as e:
        print("Ошибка при записи сна:", e)

        return jsonify({
            "version": data["version"],
            "session": data["session"],
            "response": {
                "text": "Произошла ошибка при записи сна. Попробуй позже.",
                "end_session": False
            }
        })

if __name__ == "__main__":
    app.run(port=8080)
