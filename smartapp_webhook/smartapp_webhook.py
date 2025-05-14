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

    response = requests.post("https://dreamember.onrender.com/api/dream", json=payload)

    return jsonify({
        "version": data["version"],
        "session": data["session"],
        "response": {
            "text": "Сон записан!",
            "end_session": False
        }
    })

if __name__ == "__main__":
    app.run(port=8080)
