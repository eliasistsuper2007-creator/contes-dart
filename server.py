import os
import json
import time
import hmac
import hashlib
import threading
import requests
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

CLIENT_ID     = os.environ.get("TUYA_CLIENT_ID", "ds3yc9quh7vjvcwexqdm")
CLIENT_SECRET = os.environ.get("TUYA_CLIENT_SECRET", "c607a2a4402e42a6ad5d9bdbc39b810e")
BASE_URL      = os.environ.get("TUYA_BASE_URL", "https://openapi.tuyaeu.com")
SOCKET_DEVICE_ID = os.environ.get("SOCKET_DEVICE_ID", "bf0f3d3c500b530a54gane")
LED_DEVICE_ID    = os.environ.get("LED_DEVICE_ID", "bf5298c175e7812b7ecxrb")

_game_state = {
    "active": False,
    "mode": None,
    "round": 1,
    "currentPlayer": 0,
    "gameOver": False,
    "winner": None,
    "bust": False,
    "bustPlayer": None,
    "players": [],
    "updatedAt": 0
}
_state_lock = threading.Lock()

def calc_sign(message: str) -> str:
    return hmac.new(
        CLIENT_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256
    ).hexdigest().upper()

def get_token():
    t = str(int(time.time() * 1000))
    path = "/v1.0/token?grant_type=1"
    empty_sha = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    string_to_sign = f"GET\n{empty_sha}\n\n{path}"
    message = CLIENT_ID + t + string_to_sign
    sign = calc_sign(message)
    headers = {
        "client_id": CLIENT_ID,
        "t": t,
        "sign_method": "HMAC-SHA256",
        "sign": sign,
    }
    r = requests.get(BASE_URL + path, headers=headers, timeout=15)
    data = r.json()
    if not data.get("success"):
        raise Exception(json.dumps(data, indent=2))
    return data["result"]["access_token"]

def send_commands(token: str, device_id: str, commands: list):
    t = str(int(time.time() * 1000))
    path = f"/v1.0/devices/{device_id}/commands"
    body = json.dumps({"commands": commands}, separators=(",", ":"))
    content_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    string_to_sign = f"POST\n{content_sha}\n\n{path}"
    message = CLIENT_ID + token + t + string_to_sign
    sign = calc_sign(message)
    headers = {
        "client_id": CLIENT_ID,
        "access_token": token,
        "t": t,
        "sign_method": "HMAC-SHA256",
        "sign": sign,
        "Content-Type": "application/json",
    }
    r = requests.post(BASE_URL + path, headers=headers, data=body, timeout=15)
    return r.status_code, r.text

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/display")
def display():
    return render_template("display.html")

@app.route("/api/state", methods=["GET", "POST"])
def api_state():
    global _game_state
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        with _state_lock:
            _game_state = {
                "active": bool(data.get("active", False)),
                "mode": data.get("mode"),
                "round": data.get("round", 1),
                "currentPlayer": data.get("currentPlayer", 0),
                "gameOver": bool(data.get("gameOver", False)),
                "winner": data.get("winner"),
                "bust": bool(data.get("bust", False)),
                "bustPlayer": data.get("bustPlayer"),
                "players": data.get("players") or [],
                "updatedAt": int(time.time() * 1000),
            }
        return jsonify({"ok": True})
    with _state_lock:
        return jsonify(_game_state)

@app.route("/command", methods=["POST", "OPTIONS"])
def command():
    if request.method == "OPTIONS":
        return ("", 204)

    data = request.get_json(force=True, silent=True) or {}
    action = data.get("action")

    try:
        token = get_token()

        if action == "socket_on":
            status, text = send_commands(token, SOCKET_DEVICE_ID, [
                {"code": "switch_1", "value": True}
            ])
        elif action == "socket_off":
            status, text = send_commands(token, SOCKET_DEVICE_ID, [
                {"code": "switch_1", "value": False}
            ])
        elif action == "led_red":
            status, text = send_commands(token, LED_DEVICE_ID, [
                {"code": "switch_led", "value": True},
                {"code": "work_mode", "value": "colour"},
                {"code": "colour_data", "value": {"h": 0, "s": 1000, "v": 1000}}
            ])
        elif action == "led_green":
            status, text = send_commands(token, LED_DEVICE_ID, [
                {"code": "switch_led", "value": True},
                {"code": "work_mode", "value": "colour"},
                {"code": "colour_data", "value": {"h": 120, "s": 1000, "v": 1000}}
            ])
        elif action == "led_off":
            status, text = send_commands(token, LED_DEVICE_ID, [
                {"code": "switch_led", "value": False}
            ])
        else:
            value = data.get("value", True)
            status, text = send_commands(token, SOCKET_DEVICE_ID, [
                {"code": "switch_1", "value": bool(value)}
            ])

        return text, status, {"Content-Type": "application/json"}
    except Exception as e:
        return str(e), 500, {"Content-Type": "text/plain"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8765))
    app.run(host="0.0.0.0", port=port, debug=False)
