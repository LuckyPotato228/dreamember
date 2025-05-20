from __future__ import annotations
from flask import Flask, request, jsonify, Response
import requests
import traceback
import time

app = Flask(__name__)

# ------------------ состояние: ключ — userId ------------------
user_state: dict[str, dict[str, bool]] = {}
activation_phrases = {
    "запусти dreamember",
    "включи запись сна",
    "запиши сон",
    "запиши мой сон",
}

@app.route("/health", methods=["GET", "HEAD"])
def health() -> Response:
    """Лёгкий энд-пойнт для Render/Sber мониторинга."""
    payload = {"status": "ok"}
    # небольшая доп-инфа по запросу ?verbose=1  (не нужна модерации)
    if request.method == "GET" and request.args.get("verbose") == "1":
        payload["ts"] = int(time.time())
        payload["users_in_mem"] = len(user_state)
    return jsonify(payload)


# ——— (опц.) быстрое REST-проверка, зарегистрировано ли устройство ———
def is_registered(device_id: str) -> bool:
    """
    True  → бэк знает такой deviceID
    False → не зарегистрирован или бэк недоступен
    Если у тебя нет такого эндпойнта — оставь функцию как есть,
    она всегда вернёт False и логика будет работать за счёт кэша.
    """
    try:
        r = requests.get(
            f"https://dreamember.onrender.com/api/device/{device_id}/exists",
            timeout=3,
        )
        return r.status_code == 200
    except requests.RequestException:
        return False


@app.route("/webhook", methods=["POST"])
def handle_smartapp():
    data = request.json
    print("🌐 Входящий JSON:", data)

    user_id = data.get("uuid", {}).get("userId")
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

    # -------- получаем/создаём state пользователя -------------
    state = user_state.setdefault(
        user_id, {"awaiting": False, "registered": False}
    )

    # однократная ленивая проверка регистрации через бэк (см. функцию выше)
    if not state["registered"]:
        state["registered"] = is_registered(user_id)

    # ── 1. Активационная фраза ───────────────────────────────
    if text in activation_phrases:
        if state["awaiting"]:
            return jsonify(
                answer("Я уже готова! Просто расскажи свой сон.", data)
            )

        state["awaiting"] = True

        if state["registered"]:
            # пользователь ранее зарегистрировался → сразу ждём сон
            return jsonify(
                answer("Готова записать! Расскажи, что тебе снилось.", data)
            )

        # регистрация ещё не пройдена — выводим инструкцию
        msg = (
            f"Привет! Чтобы я могла записать твой сон, зарегистрируйся на сайте "
            f"<https://dreamember.onrender.com/>.\n\n"
            f"Твой идентификатор: **{user_id}**.\n"
            f"Когда закончишь регистрацию, расскажи свой сон."
        )
        print(f"🆔 Пользователь {user_id}: ждём регистрацию")
        return jsonify(answer(msg, data))

    # ── 2. Пришёл текст сна, если ждём ────────────────────────
    if state["awaiting"]:
        state["awaiting"] = False            # сбрасываем флаг
        payload = {"text": text, "deviceID": user_id}

        try:
            print(f"📤 Отправляем сон пользователя {user_id}")
            resp = requests.post(
                "https://dreamember.onrender.com/api/dream", json=payload, timeout=5
            )

            if resp.status_code in (401, 403):
                # не зарегистрирован → просим пройти регистрацию, ждём повторно
                print("⚠️ Пользователь ещё не зарегистрирован")
                state["awaiting"] = True
                return jsonify(
                    answer(
                        f"Ты ещё не зарегистрирован. Твой идентификатор: {user_id}. "
                        "Введи его на сайте и затем снова расскажи сон.",
                        data,
                    )
                )

            resp.raise_for_status()
            print("✅ Сон сохранён")
            state["registered"] = True       # кэшируем факт регистрации
            return jsonify(answer("Сон записан! Хорошего вам дня 🤍", data))

        except Exception:
            # ошибка сети / сервера — даём шанс повторить
            state["awaiting"] = True
            print("❌ Ошибка при отправке сна:", traceback.format_exc())
            return jsonify(
                answer(
                    "Что-то пошло не так при записи сна. Попробуй повторить позже.",
                    data,
                )
            )

    # ── 3. Остальное — непонятный ввод ───────────────────────
    print(f"🤷 Не распознано (user {user_id}): {text}")
    return jsonify(
        answer(
            "Я тебя не поняла. Скажи «Запусти dreamember» или сразу «Запиши мой сон».",
            data,
        )
    )


# ---------------- сервисные функции ---------------------------
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
            "items": [{"bubble": {"text": text, "markdown": True}}],
            "auto_listening": False,
            "finished": end_session,
        },
    }


def default_error(data: dict) -> dict:
    return answer("Ошибка в запросе. Повтори ещё раз.", data, end_session=True)


if __name__ == "__main__":
    app.run(port=8080)
