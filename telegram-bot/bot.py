#!/usr/bin/env python3
"""
AKRO GOXI Telegram Bot — button-driven menu (no typed commands needed),
mirrors the "Create License" flow on the website: pick game -> pick
duration -> pick device count -> confirm -> get key.

Env vars (see workflow / README):
    TELEGRAM_BOT_TOKEN, SITE_API_URL, BOT_API_SECRET, POLL_SECONDS

State that must survive between GitHub Actions runs is kept in small
JSON files under telegram-bot/ and committed back by the workflow:
    .offset        -> last processed Telegram update_id
    .sessions.json -> per-chat conversation state + remembered username
"""

import os
import sys
import time
import json
import urllib.request
import urllib.parse
import urllib.error

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
API_URL = os.environ.get("SITE_API_URL", "").rstrip("/")
API_SECRET = os.environ.get("BOT_API_SECRET", "")
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "270"))

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
OFFSET_FILE = os.environ.get("OFFSET_FILE", "telegram-bot/.offset")
SESSIONS_FILE = os.environ.get("SESSIONS_FILE", "telegram-bot/.sessions.json")

GAMES = [("PUBG", "🎯 PUBG Mobile"), ("8BallPool", "🎱 8 Ball Pool")]
DURATIONS = [
    (2, "2 Hours — ₹10/device"),
    (5, "5 Hours — ₹20/device"),
    (24, "1 Day — ₹80/device"),
    (72, "3 Days — ₹150/device"),
    (168, "7 Days — ₹250/device"),
    (336, "14 Days — ₹350/device"),
    (720, "30 Days — ₹500/device"),
    (240000000000000, "♾️ Unlimited — Free"),
]
DEVICE_OPTIONS = [1, 2, 5, 10, 20, 50]


# ---------------------------------------------------------------- transport

def tg_call(method, params=None, timeout=35):
    url = f"{TELEGRAM_API}/{method}"
    body = json.dumps(params or {}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def api_call(path, method="GET", data=None):
    url = f"{API_URL}/bot-api/{path}"
    headers = {"X-BOT-SECRET": API_SECRET}
    if method == "GET" and data:
        url += "?" + urllib.parse.urlencode(data)
        req = urllib.request.Request(url, headers=headers)
    else:
        body = urllib.parse.urlencode(data or {}).encode()
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"success": False, "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def send(chat_id, text, keyboard=None, edit_message_id=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if keyboard is not None:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    if edit_message_id:
        payload["message_id"] = edit_message_id
        return tg_call("editMessageText", payload)
    return tg_call("sendMessage", payload)


def answer_callback(callback_id, text=None):
    p = {"callback_query_id": callback_id}
    if text:
        p["text"] = text
    tg_call("answerCallbackQuery", p)


# ------------------------------------------------------------ persistence

def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f)


# ------------------------------------------------------------------ menus

def main_menu(chat_id, edit_message_id=None):
    kb = [
        [{"text": "🔑 Generate License", "callback_data": "menu:generate"}],
        [{"text": "📊 Check Key Status", "callback_data": "menu:status"}],
        [{"text": "💰 Check Balance", "callback_data": "menu:balance"}],
    ]
    text = "🏆 *AKRO GOXI* — اختار من تحت:"
    send(chat_id, text, kb, edit_message_id)


def game_menu(chat_id, edit_message_id=None):
    kb = [[{"text": label, "callback_data": f"g:{code}"}] for code, label in GAMES]
    kb.append([{"text": "⬅️ رجوع", "callback_data": "menu:main"}])
    send(chat_id, "اختار اللعبة:", kb, edit_message_id)


def duration_menu(chat_id, edit_message_id=None):
    kb = [[{"text": label, "callback_data": f"d:{hrs}"}] for hrs, label in DURATIONS]
    kb.append([{"text": "⬅️ رجوع", "callback_data": "flow:game"}])
    send(chat_id, "اختار المدة:", kb, edit_message_id)


def devices_menu(chat_id, edit_message_id=None):
    row = [{"text": str(n), "callback_data": f"dev:{n}"} for n in DEVICE_OPTIONS]
    kb = [row[:3], row[3:]]
    kb.append([{"text": "✏️ رقم تاني (اكتبه)", "callback_data": "dev:custom"}])
    kb.append([{"text": "⬅️ رجوع", "callback_data": "flow:duration"}])
    send(chat_id, "كام جهاز (Max Devices)؟", kb, edit_message_id)


def confirm_menu(chat_id, sess, edit_message_id=None):
    game_label = dict(GAMES).get(sess["game"], sess["game"])
    dur_label = dict(DURATIONS).get(sess["duration"], str(sess["duration"]))
    text = (
        "📝 *تأكيد الطلب*\n\n"
        f"👤 اليوزر: `{sess['username']}`\n"
        f"🎮 اللعبة: {game_label}\n"
        f"⏱ المدة: {dur_label}\n"
        f"📱 الأجهزة: {sess['devices']}\n\n"
        "متأكد؟"
    )
    kb = [
        [{"text": "✅ توليد الـ Key", "callback_data": "confirm:yes"}],
        [{"text": "❌ إلغاء", "callback_data": "confirm:no"}],
    ]
    send(chat_id, text, kb, edit_message_id)


# --------------------------------------------------------------- handlers

def do_generate(chat_id, sess):
    res = api_call("generate", "POST", {
        "username": sess["username"],
        "game": sess["game"],
        "duration": sess["duration"],
        "max_devices": sess["devices"],
    })
    if res.get("success"):
        send(chat_id, (
            "✅ *تم التوليد*\n\n"
            f"`{res['key']}`\n"
            f"🎮 {res['game']}\n"
            f"⏱ {res['duration_h']}h\n"
            f"📱 {res['max_devices']} device(s)"
        ))
    else:
        send(chat_id, f"❌ {res.get('error', 'حصل خطأ غير معروف')}")
    main_menu(chat_id)


def handle_callback(update, sessions):
    cq = update["callback_query"]
    chat_id = cq["message"]["chat"]["id"]
    msg_id = cq["message"]["message_id"]
    data = cq.get("data", "")
    answer_callback(cq["id"])

    sess = sessions.setdefault(str(chat_id), {})

    if data == "menu:main":
        main_menu(chat_id, msg_id)
        return

    if data == "menu:generate":
        if not sess.get("username"):
            sess["step"] = "await_username_for_generate"
            send(chat_id, "اكتب اليوزر بتاعك في الموقع:", edit_message_id=msg_id)
        else:
            sess["step"] = None
            game_menu(chat_id, msg_id)
        return

    if data == "menu:status":
        sess["step"] = "await_status_key"
        send(chat_id, "ابعت الـ Key اللي عايز تفحصه:", edit_message_id=msg_id)
        return

    if data == "menu:balance":
        if sess.get("username"):
            res = api_call("balance", "GET", {"username": sess["username"]})
            if res.get("success"):
                send(chat_id, f"💰 {res['username']}: ₹{res['saldo']}", edit_message_id=msg_id)
            else:
                send(chat_id, f"❌ {res.get('error')}", edit_message_id=msg_id)
            main_menu(chat_id)
        else:
            sess["step"] = "await_username_for_balance"
            send(chat_id, "اكتب اليوزر بتاعك في الموقع:", edit_message_id=msg_id)
        return

    if data.startswith("g:"):
        sess["game"] = data.split(":", 1)[1]
        duration_menu(chat_id, msg_id)
        return

    if data == "flow:game":
        game_menu(chat_id, msg_id)
        return

    if data.startswith("d:"):
        sess["duration"] = int(data.split(":", 1)[1])
        devices_menu(chat_id, msg_id)
        return

    if data == "flow:duration":
        duration_menu(chat_id, msg_id)
        return

    if data.startswith("dev:"):
        val = data.split(":", 1)[1]
        if val == "custom":
            sess["step"] = "await_devices_custom"
            send(chat_id, "اكتب عدد الأجهزة (رقم):", edit_message_id=msg_id)
        else:
            sess["devices"] = int(val)
            confirm_menu(chat_id, sess, msg_id)
        return

    if data == "confirm:yes":
        do_generate(chat_id, sess)
        return

    if data == "confirm:no":
        main_menu(chat_id, msg_id)
        return


def handle_message(update, sessions):
    msg = update["message"]
    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()
    sess = sessions.setdefault(str(chat_id), {})

    if text in ("/start", "/menu", "/help"):
        sess["step"] = None
        main_menu(chat_id)
        return

    step = sess.get("step")

    if step == "await_username_for_generate":
        sess["username"] = text
        sess["step"] = None
        game_menu(chat_id)
        return

    if step == "await_username_for_balance":
        sess["username"] = text
        sess["step"] = None
        res = api_call("balance", "GET", {"username": text})
        if res.get("success"):
            send(chat_id, f"💰 {res['username']}: ₹{res['saldo']}")
        else:
            send(chat_id, f"❌ {res.get('error')}")
        main_menu(chat_id)
        return

    if step == "await_status_key":
        sess["step"] = None
        res = api_call("status", "GET", {"key": text})
        if res.get("success"):
            send(chat_id, (
                f"🔑 `{res['key']}`\n"
                f"🎮 {res['game']}\n"
                f"⏱ {res['duration_h']}h\n"
                f"📱 {res['devices_used']}/{res['max_devices']}\n"
                f"📅 expires: {res.get('expired_date') or 'not activated yet'}\n"
                f"👤 owner: {res['registrator']}"
            ))
        else:
            send(chat_id, f"❌ {res.get('error')}")
        main_menu(chat_id)
        return

    if step == "await_devices_custom":
        if text.isdigit() and int(text) > 0:
            sess["devices"] = int(text)
            sess["step"] = None
            confirm_menu(chat_id, sess)
        else:
            send(chat_id, "لازم رقم صحيح أكبر من صفر. جرب تاني:")
        return

    # no active step -> show the menu
    main_menu(chat_id)


# ---------------------------------------------------------------------- main

def main():
    if not BOT_TOKEN or not API_URL or not API_SECRET:
        print("Missing TELEGRAM_BOT_TOKEN / SITE_API_URL / BOT_API_SECRET env vars.")
        sys.exit(1)

    try:
        with open(OFFSET_FILE) as f:
            offset = int(f.read().strip())
    except Exception:
        offset = 0

    sessions = load_json(SESSIONS_FILE, {})

    deadline = time.time() + POLL_SECONDS
    print(f"Bot polling started, offset={offset}, for {POLL_SECONDS}s")

    while time.time() < deadline:
        try:
            resp = tg_call("getUpdates", {"offset": offset, "timeout": 25}, timeout=35)
        except Exception as e:
            print("poll error:", e)
            time.sleep(3)
            continue

        for update in resp.get("result", []):
            offset = update["update_id"] + 1
            try:
                if "callback_query" in update:
                    handle_callback(update, sessions)
                elif "message" in update and "text" in update["message"]:
                    handle_message(update, sessions)
            except Exception as e:
                chat_id = None
                if "callback_query" in update:
                    chat_id = update["callback_query"]["message"]["chat"]["id"]
                elif "message" in update:
                    chat_id = update["message"]["chat"]["id"]
                if chat_id:
                    send(chat_id, f"⚠️ internal error: {e}")

        os.makedirs(os.path.dirname(OFFSET_FILE), exist_ok=True)
        with open(OFFSET_FILE, "w") as f:
            f.write(str(offset))
        save_json(SESSIONS_FILE, sessions)

    print(f"Bot polling window done, offset saved={offset}")


if __name__ == "__main__":
    main()
