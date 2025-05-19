from flask import Flask, request, jsonify
import requests

app = Flask(__name__)
sessions = {}

@app.route('/webhook', methods=['POST'])
def handle_smartapp():
    data = request.json
    session_id = data.get("sessionId")
    user_id = data.get("uuid", {}).get("userId")
    text = data.get("payload", {}).get("message", {}).get("original_text", "")

    # Проверка: если это активационная фраза
    if text.lower() in ["запусти dreamember", "открой dreamember"]:
        sessions[session_id] = {"awaiting_dream_text": True, "device_id": user_id}
        return jsonify({
            "messageName": "ANSWER_TO_USER",
            "sessionId": session_id,
            "messageId": data["messageId"],
            "payload": {
                "items": [{"bubble": {"text": "Говори, что тебе приснилось"}}],
                "pronounceText": "Говори, что тебе приснилось",
                "end_session": False
            }
        })

    # Если ожидается сон от пользователя
    if session_id in sessions and sessions[session_id].get("awaiting_dream_text"):
        sessions[session_id]["awaiting_dream_text"] = False
        device_id = sessions[session_id]["device_id"]

        payload = {
            "text": text,
            "deviceID": device_id
        }
        try:
            requests.post("https://dreamember.onrender.com/api/dream", json=payload)
        except Exception:
            pass  # можно логировать

        return jsonify({
            "messageName": "ANSWER_TO_USER",
            "sessionId": session_id,
            "messageId": data["messageId"],
            "payload": {
                "items": [{"bubble": {"text": "Сон записан!"}}],
                "pronounceText": "Сон записан!",
                "end_session": True
            }
        })

    # По умолчанию — ответ ни о чём
    return jsonify({
        "messageName": "ANSWER_TO_USER",
        "sessionId": session_id,
        "messageId": data["messageId"],
        "payload": {
            "items": [{"bubble": {"text": "Скажи 'запусти Dreamember'"}}],
            "pronounceText": "Скажи 'запусти Dreamember'",
            "end_session": False
        }
    })

if __name__ == "__main__":
    app.run(port=8080)
