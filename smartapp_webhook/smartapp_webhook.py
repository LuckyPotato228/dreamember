from flask import Flask, request, jsonify
import requests
import traceback

app = Flask(__name__)

# ------------------ состояние: ключом делаем userId -------------------------
user_state = {}  # user_state[user_id] = {"awaiting": bool}

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

    user_id = data.get("uuid", {}).get("userId")
    session_id = data.get("sessionId")  # логируем, но не привязываемся к нему
    text = (
        data.get("payload", {})
        .get("message", {})
        .get("original_text", "")
        .strip()
        .lower()
    )

    if not user_id or not text:
        print("⚠️ Нет userId или текста")
        return jsonify(default_error(data))

    state = user_state.setdefault(user_id, {"awaiting": False})

    # ── 1. Активационная фраза ──────────────────────────────────────────────
    if text in activation_phrases:
        if state["awaiting"]:
            # уже ждём сон → не сбрасываем, а напоминаем
            return jsonify(
                answer(
                    "Я уже готова! Просто расскажи свой сон текстом.",
                    data,
                )
            )

        state["awaiting"] = True
        msg = (
            f"Привет! Чтобы я могла записать твой сон, зарегистрируйся на сайте "
            f"<https://dreamember.onrender.com/>.\n\n"
            f"Твой идентификатор: **{user_id}**.\n"
            f"Когда закончишь регистрацию, просто расскажи свой сон."
        )
        print(f"🆔 Пользователь {user_id}: перевела в режим ожидания сна")
        return jsonify(answer(msg, data))

    # ── 2. Получили текст сна, если мы его ждём ─────────────────────────────
    if state["awaiting"]:
        state["awaiting"] = False  # сбрасываем сразу, чтобы не дублировалось
        payload = {"text": text, "deviceID": user_id}

        try:
            print(f"📤 Отправляем сон пользователя {user_id!s}")
            resp = requests.post(
                "https://dreamember.onrender.com/api/dream", json=payload, timeout=5
            )

            if resp.status_code in (401, 403):
                print("⚠️ Пользователь ещё не зарегистрирован")
                state["awaiting"] = True  # ждём повторно после регистрации
                return jsonify(
                    answer(
                        f"Ты ещё не зарегистрирован. Твой идентификатор: {user_id}. "
                        "Введи его на сайте и затем снова расскажи сон.",
                        data,
                    )
                )

            resp.raise_for_status()
            print("✅ Сон сохранён")
            return jsonify(answer("Сон записан! Хороших снов 🤍", data))

        except Exception:
            # при ошибке даём шанс повторить сон
            state["awaiting"] = True
            print("❌ Ошибка при отправке сна:", traceback.format_exc())
            return jsonify(
                answer(
                    "Что-то пошло не так при записи сна. Попробуй повторить чуть позже.",
                    data,
                )
            )

    # ── 3. Непонятный ввод ──────────────────────────────────────────────────
    print(f"🤷 Не распознано (user {user_id}): {text}")
    return jsonify(
        answer(
            "Я тебя не поняла. Скажи «Запусти dreamember» или сразу «Запиши мой сон».",
            data,
        )
    )


# -------------------------- сервисные функции -------------------------------
def answer(text: str, data: dict, end_session: bool = False) -> dict:
    """формирует корректный ANSWER_TO_USER для SmartApp"""
    return {
        "messageName": "ANSWER_TO_USER",
        "sessionId": data["sessionId"],
        "messageId": data["messageId"],
        "uuid": data["uuid"],
        "payload": {
            "pronounceText": text,
            "pronounceTextType": "application/text",
            "items": [
                {
                    "bubble": {
                        "text": text,
                        "markdown": True,
                    }
                }
            ],
            "auto_listening": False,
            "finished": end_session,
        },
    }


def default_error(data: dict) -> dict:
    return answer("Ошибка в запросе. Повтори ещё раз.", data, end_session=True)


if __name__ == "__main__":
    app.run(port=8080)
