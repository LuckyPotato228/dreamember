from __future__ import annotations
import os, json, time, traceback, re
from typing import Final
import psycopg2
from psycopg2 import sql
import requests
from flask import Flask, jsonify, request, Response

app = Flask(__name__)

# ---------- Postgres ---------------------------------------------
DB_HOST, DB_PORT = os.getenv("DB_HOST"), os.getenv("DB_PORT")
DB_NAME, DB_USER = os.getenv("DB_NAME"), os.getenv("DB_USER")
DB_PASSWORD      = os.getenv("DB_PASSWORD")

db_conn = psycopg2.connect(
    host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
    user=DB_USER, password=DB_PASSWORD
)
db_conn.autocommit = True

def check_device_registered(device_id: str) -> bool:
    with db_conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT 1 FROM {t} WHERE {c}=%s LIMIT 1")
               .format(t=sql.Identifier("users"), c=sql.Identifier("deviceID")),
            (device_id,))
        return cur.fetchone() is not None

# ---------- helpers: voice & placeholders -------------------------
def voice(data: dict) -> str:                     # joy / athena / sber
    return data.get("payload", {}).get("character", {}).get("id", "sber")

PH = {                       # «ты»-форма , «вы»-форма
    "{say}"     : ("Скажи"   , "Скажите"),
    "{tell}"    : ("Запиши"  , "Запишите"),
    "{create}"  : ("Придумай", "Придумайте"),
    "{repeat}"  : ("Повтори" , "Повторите"),
    "{describe}": ("Расскажи", "Расскажите"),
    "{start}"   : ("Начинай" , "Начинайте"),
    "{ready}"   : ("готова"  , "готова/готов"),
    "{you}"     : ("тебе"    , "вам"),
    "{your}"    : ("твой"    , "ваш"),
    "{help}"    : ("помогла" , "помогла/помог"),
}

def adapt(text: str, v: str) -> str:
    is_ty = v == "joy"
    rep   = {k:(ty if is_ty else vy) for k,(ty,vy) in PH.items()}

    # род для ready / help
    if not is_ty and v == "sber":
        rep["{ready}"] = "готов"
    elif not is_ty and v == "athena":
        rep["{ready}"] = "готова"

    rep["{help}"] = "помогла" if v in ("joy","athena") else "помог"

    for k,val in rep.items():
        text = text.replace(k,val)
    return text

URL_RE = re.compile(r'https?://\S+|<[^>]+>')

# ---------- runtime-state -----------------------------------------
user_state: dict[str, dict[str, bool | str]] = {}

# ---------- phrases ----------------------------------------------
activation_phrases: Final = {
    "записать сон","запиши сон","запиши мой сон",
    "запись сна","запись снов","дневник снов",
    "запусти dreamember","включи запись сна",
    "запусти дневник снов","запусти запись сна",
    "запусти запись снов","открой дневник снов",
}
help_phrases: Final = {"помощь","help","что ты умеешь","как пользоваться"}

# ---------- health ------------------------------------------------
@app.route("/health", methods=["GET","HEAD"])
def health() -> Response:
    return jsonify({"status":"ok","users":len(user_state)})

# ---------- webhook ----------------------------------------------
@app.route("/webhook", methods=["POST"])
def handle_smartapp():
    data    = request.json or {}
    pl      = data.get("payload", {})
    user_id = data.get("uuid", {}).get("userId")
    text    = pl.get("message", {}).get("original_text", "").strip().lower()
    v       = voice(data)

    if not user_id:
        return jsonify(default_error(data))

    st = user_state.setdefault(user_id,{
        "welcomed":False,"awaiting":False,
        "awaiting_login":False,"awaiting_password":False,
        "temp_login":"","registered":None
    })
    if st["registered"] is None:
        st["registered"] = check_device_registered(user_id)

    # ---------- welcome -------------------------------------------
    if pl.get("intent")=="run_app" and not st["welcomed"]:
        st["welcomed"]=True
        return jsonify(answer(
            adapt("Привет! Я «Дримембер» — {your} дневник снов.\n"
                  "{say} «Запиши сон» или «Помощь», чтобы я {help} {you}.", v),
            data,
            suggestions=["Запиши сон","Помощь"]
        ))

    if text=="":
        return jsonify(answer(
            adapt("Привет! Я «Дримембер». {say} «Запиши сон».", v),
            data,
            suggestions=["Запиши сон","Помощь"]
        ))

    # ---------- help ----------------------------------------------
    if text in help_phrases:
        return jsonify(answer(
            adapt("Я «Дримембер».\n1) {say} «Запиши сон».\n"
                  "2) {describe} сон (до 90 сек.).", v),
            data,
            suggestions=["Запиши сон"]
        ))

    # ---------- REGISTRATION --------------------------------------
    if (not st["registered"] and not st["awaiting_login"]
        and not st["awaiting_password"] and text in activation_phrases):
        st["awaiting_login"]=True
        return jsonify(answer(
            adapt("{tell} логин латиницей (строчные буквы/цифры).", v),
            data,
            suggestions=["Отмена"]
        ))

    if st["awaiting_login"]:
        if len(text)<3 or not re.fullmatch(r'[a-z0-9_-]+',text):
            return jsonify(answer(
                adapt("Логин ≥3 символов, буквы, цифры, «-», «_». {repeat} логин.", v),
                data,
                suggestions=["Отмена"]
            ))
        st["temp_login"]=text
        st["awaiting_login"]=False
        st["awaiting_password"]=True
        return jsonify(answer(
            adapt("{create} пароль (≥8 символов латиницы/цифр).", v),
            data,
            suggestions=["Отмена"]
        ))

    if st["awaiting_password"]:
        if len(text)<8 or not re.fullmatch(r'[A-Za-z0-9_-]+',text):
            return jsonify(answer(
                adapt("Пароль ≥8 символов, латиница/цифры. {repeat} пароль.", v),
                data,
                suggestions=["Отмена"]
            ))
        try:
            resp=requests.post(
                "https://dreamember.onrender.com/api/user/registration",
                json={"login":st["temp_login"],"password":text,"deviceID":user_id},
                timeout=5)
            if resp.ok:
                st["registered"]=True
                return jsonify(answer(
                    adapt("Регистрация успешна! {say} «Запиши сон».", v),
                    data,
                    suggestions=["Запиши сон"]
                ))
            st["awaiting_login"]=True
            return jsonify(answer(
                adapt("Логин занят. {tell} другой логин.", v),
                data,
                suggestions=["Отмена"]
            ))
        except Exception:
            st["awaiting_login"]=True
            return jsonify(answer(
                adapt("Сервер регистрации недоступен, {repeat} позже.", v),
                data,
                suggestions=["Отмена"]
            ))

    # ---------- RECORD DREAM --------------------------------------
    if text in activation_phrases:
        if not st["registered"]:
            st["awaiting_login"]=True
            return jsonify(answer(
                adapt("Устройство не привязано. {tell} логин.", v),
                data,
                suggestions=["Отмена"]
            ))
        if st["awaiting"]:
            return jsonify(answer(
                adapt("Я слушаю, {describe} сон.", v),
                data
            ))
        st["awaiting"]=True
        return jsonify(answer(
            adapt("{ready} записать {your} сон. {start}.", v),
            data,
            suggestions=["Отмена"]
        ))

    if st["awaiting"]:
        st["awaiting"]=False
        try:
            resp=requests.post(
                "https://dreamember.onrender.com/api/dream",
                json={"text":text,"deviceID":user_id},
                timeout=5)
            resp.raise_for_status()
            return jsonify(answer(
                adapt("Сон записан! Хорошего дня 🤍\n"
                      "Записи на сайте: <https://dreamember.onrender.com/>", v),
                data,
                suggestions=["Запиши сон"]
            ))
        except Exception:
            st["awaiting"]=True
            return jsonify(answer(
                adapt("Сервер недоступен, {repeat} позже.", v),
                data,
                suggestions=["Отмена"]
            ))

    # ---------- fallback ------------------------------------------
    return jsonify(answer(
        adapt("Не расслышал. {say} «Запиши сон».", v),
        data,
        suggestions=["Запиши сон","Помощь"]
    ))

# ---------- answer / default --------------------------------------
def answer(text:str, data:dict, suggestions:list[str]|None=None,
           end_session:bool=False) -> dict:
    pronounce=URL_RE.sub("сайте",text)   # ссылки ≠ озвучиваем
    payload={
        "pronounceText":pronounce,
        "pronounceTextType":"application/text",
        "items":[{"bubble":{"text":text,"markdown":True}}],
        "auto_listening":False,
        "finished":end_session,
    }
    if suggestions:
        payload["suggestions"]={
            "buttons":[{"title":s,"action":{"text":s}} for s in suggestions]
        }

    return {
        "messageName":"ANSWER_TO_USER",
        "sessionId":data["sessionId"],
        "messageId":data["messageId"],
        "uuid":data["uuid"],
        "payload":payload,
    }

def default_error(data:dict) -> dict:
    return answer(adapt("Техническая ошибка. {repeat} позже.", voice(data)), data, suggestions=["Запиши сон"], end_session=True)

if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
