from __future__ import annotations
import json, time, traceback
from typing import Final

import requests
from flask import Flask, jsonify, request, Response

app = Flask(__name__)

# ---------- состояние пользователя --------------------------------
user_state: dict[str, dict[str, bool | str]] = {}

# ---------- фразы -------------------------------------------------
activation_phrases: Final = {
    "записать сон", "запиши сон", "запиши мой сон",
    "запись сна", "запись снов", "дневник снов",
    "запусти dreamember", "включи запись сна",
    "запусти дневник снов", "запусти запись сна",
    "запусти запись снов", "открой дневник снов",
}
help_phrases: Final = {"помощь", "help", "что ты умеешь", "как пользоваться"}

# ---------- /health -----------------------------------------------
@app.route("/health", methods=["GET", "HEAD"])
def health() -> Response:
    payload = {"status": "ok"}
    if request.method == "GET" and request.args.get("verbose") == "1":
        payload |= {"ts": int(time.time()), "users_in_mem": len(user_state)}
    return jsonify(payload)

def is_registered(device_id: str) -> bool:
    try:
        r = requests.get(
            f"https://dreamember.onrender.com/api/device/{device_id}/exists", timeout=3
        )
        return r.status_code == 200
    except requests.RequestException:
        return False

# ----------------------------- WEBHOOK ----------------------------
@app.route("/webhook", methods=["POST"])
def handle_smartapp():
    data = request.json
    print("=== Incoming webhook ===")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    print("========================")

    pl = data.get("payload", {})
    user_id = data.get("uuid", {}).get("userId")

    # ─── welcome только один раз за сессию ──────────────────────
    if pl.get("intent") == "run_app":
        st = user_state.setdefault(user_id, {"welcomed": False})
        if not st.get("welcomed"):
            st["welcomed"] = True
            return jsonify(answer(
                "Привет! Я «Дримембер» — ваш личный дневник снов.\n"
                "Скажите «Запиши сон» или «Помощь», чтобы узнать команды.",
                data
            ))

    # ─── извлекаем текст ─────────────────────────────────────────
    text = pl.get("message", {}).get("original_text", "").strip().lower()
    if not user_id or not text:
        return jsonify(default_error(data))

    # ─── состояние пользователя ─────────────────────────────────
    state = user_state.setdefault(user_id, {
        "awaiting": False,
        "registered": False,
        "awaiting_login": False,
        "awaiting_password": False,
        "temp_login": ""
    })
    if not state["registered"]:
        state["registered"] = is_registered(user_id)

    # ... остальная логика (help, регистрация, запись сна) без изменений ...

    # помощь
    if text in help_phrases:
        return jsonify(answer(
            "Я «Дримембер» — ваш дневник снов.\n"
            "1) Скажите «Запиши сон».\n"
            "2) Расскажите сон до 90 секунд.\n"
            "3) Позже посмотрите запись на сайте.",
            data
        ))

    # регистрация
    if (not state["registered"]
        and not state["awaiting_login"]
        and not state["awaiting_password"]
        and text in activation_phrases):
        state["awaiting_login"] = True
        return jsonify(answer("Назовите логин (e-mail или любое слово).", data))

    if state["awaiting_login"]:
        state["temp_login"] = text
        state["awaiting_login"] = False
        state["awaiting_password"] = True
        return jsonify(answer("Отлично! Теперь придумайте и скажите пароль.", data))

    if state["awaiting_password"]:
        login, password = state["temp_login"], text
        state["awaiting_password"] = False
        try:
            resp = requests.post(
                "https://dreamember.onrender.com/api/user/registration",
                json={"login": login, "password": password, "deviceID": user_id},
                timeout=5)
            print("Registration response:", resp.status_code, resp.text)
            if resp.ok:
                state["registered"] = True
                return jsonify(answer("Регистрация успешна! Скажите «Запиши сон».", data))
            else:
                state["awaiting_login"] = True
                return jsonify(answer("Логин занят. Назовите другой логин.", data))
        except Exception:
            print("Registration error:", traceback.format_exc())
            state["awaiting_login"] = True
            return jsonify(answer("Сервер регистрации недоступен, повторите позже.", data))

    # запись сна
    if text in activation_phrases:
        if state["awaiting"]:
            return jsonify(answer("Я слушаю. Расскажите сон.", data))
        state["awaiting"] = True
        return jsonify(answer("Готов записать сон. Начинайте.", data))

    if state["awaiting"]:
        state["awaiting"] = False
        try:
            resp = requests.post(
                "https://dreamember.onrender.com/api/dream",
                json={"text": text, "deviceID": user_id},
                timeout=5)
            print("Dream save response:", resp.status_code, resp.text)
            resp.raise_for_status()
            return jsonify(answer("Сон записан! Хороших снов 🤍", data))
        except Exception:
            print("Dream save error:", traceback.format_exc())
            state["awaiting"] = True
            return jsonify(answer("Не смог сохранить сон. Повторите позже.", data))

    # fallback
    return jsonify(answer("Не расслышал. Скажите «Запиши сон» или «Помощь».", data))


# ----------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ------------------------
def answer(text: str, data: dict, end_session: bool = False) -> dict:
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
    return answer("Ошибка в запросе. Повторите ещё раз.", data, end_session=True)


if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
