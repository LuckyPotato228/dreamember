from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

# пока что хардкодим deviceID
DEVICE_ID = "SberBoomSerialNumber"

@app.route('/webhook', methods=['POST'])
def handle_smartapp():
    data = request.json
    text = data["request"]["original_utterance"]
    print(f"[SmartApp] Получено: {text}")

    # Отправка на backend Димы
    payload = {
        "text": text,
        "deviceID": DEVICE_ID
    }
    response = requests.post("http://our_server_name.ru/api/dream", json=payload)
    print(f"[Backend] Ответ от сервера Димы: {response.status_code}")

    return jsonify({
        "version": data["version"],
        "session": data["session"],
        "response": {
            "text": "Я записал твой сон!",
            "end_session": False
        }
    })

if __name__ == "__main__":
    app.run(port=8080)