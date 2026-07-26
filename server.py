import os
import io
import json
import time
import uuid
import hmac
import hashlib
import threading
from datetime import datetime
import requests
from flask import Flask, request, jsonify, render_template, send_file, Response
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib.colors import Color, HexColor, white, black
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import qrcode

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
    "players": [],
    "updatedAt": 0,
    "summaryId": None,
}
_state_lock = threading.Lock()

_pdf_store = {}
_pdf_lock = threading.Lock()
PDF_TTL_SECONDS = 5 * 60  # 5 minutes


def cleanup_expired_pdfs():
    now = time.time()
    with _pdf_lock:
        expired = [k for k, v in _pdf_store.items() if v["expires"] < now]
        for k in expired:
            del _pdf_store[k]


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


NEON_CYAN = HexColor("#00f0ff")
NEON_MAGENTA = HexColor("#ff00aa")
NEON_GREEN = HexColor("#00ff88")
DARK_BG = HexColor("#0a0a12")
MUTED = HexColor("#8a8aa0")
TEXT = HexColor("#e8e8f0")
PANEL = HexColor("#12121c")


def generate_game_pdf(data: dict) -> bytes:
    buffer = io.BytesIO()
    page_w, page_h = A4
    c = canvas.Canvas(buffer, pagesize=A4)

    c.setFillColor(HexColor("#07070c"))
    c.rect(0, 0, page_w, page_h, fill=1, stroke=0)

    c.setStrokeColor(NEON_CYAN)
    c.setLineWidth(3)
    c.line(30, page_h - 25, page_w - 30, page_h - 25)

    c.setFillColor(NEON_CYAN)
    c.setFont("Helvetica-Bold", 28)
    c.drawCentredString(page_w / 2, page_h - 60, "CONTES DART")

    c.setFillColor(MUTED)
    c.setFont("Helvetica", 12)
    c.drawCentredString(page_w / 2, page_h - 80, "Spielübersicht")

    mode = data.get("mode") or "—"
    rounds = data.get("round") or 1
    winner = data.get("winner") or "—"
    created = data.get("created") or datetime.now().strftime("%d.%m.%Y %H:%M")

    y = page_h - 120
    c.setFillColor(TEXT)
    c.setFont("Helvetica", 11)
    c.drawString(50, y, f"Modus:  {mode}")
    c.drawString(200, y, f"Runden:  {rounds}")
    c.drawString(350, y, f"Datum:  {created}")

    y -= 50
    c.setFillColor(HexColor("#0d1a14"))
    c.roundRect(50, y - 55, page_w - 100, 70, 12, fill=1, stroke=0)
    c.setStrokeColor(NEON_GREEN)
    c.setLineWidth(1.5)
    c.roundRect(50, y - 55, page_w - 100, 70, 12, fill=0, stroke=1)

    c.setFillColor(NEON_GREEN)
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(page_w / 2, y, "GEWINNER")
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(page_w / 2, y - 28, str(winner))

    y -= 100
    c.setFillColor(NEON_CYAN)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(50, y, "SPIELER")

    y -= 20
    c.setFillColor(PANEL)
    c.roundRect(50, y - 18, page_w - 100, 28, 6, fill=1, stroke=0)
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(60, y - 8, "NAME")
    c.drawString(220, y - 8, "ENDSTAND")
    c.drawString(320, y - 8, "DARTS")
    c.drawString(400, y - 8, "Ø / WURF")

    players = data.get("players") or []
    y -= 35
    for i, p in enumerate(players):
        name = p.get("name", f"Spieler {i+1}")
        score = p.get("score", 0)
        darts = p.get("dartsThrown", 0)
        avg = round((mode - score) / darts, 1) if darts and mode else "—"
        is_winner = name == winner

        if is_winner:
            c.setFillColor(HexColor("#0d1a14"))
            c.roundRect(50, y - 12, page_w - 100, 26, 4, fill=1, stroke=0)

        c.setFillColor(NEON_GREEN if is_winner else TEXT)
        c.setFont("Helvetica-Bold" if is_winner else "Helvetica", 11)
        c.drawString(60, y, name)
        c.drawString(220, y, str(score))
        c.drawString(320, y, str(darts))
        c.drawString(400, y, str(avg))
        y -= 28

    throws = data.get("throwHistory") or []
    if throws:
        y -= 20
        c.setFillColor(NEON_CYAN)
        c.setFont("Helvetica-Bold", 13)
        c.drawString(50, y, "LETZTE WÜRFE")
        y -= 25

        c.setFillColor(MUTED)
        c.setFont("Helvetica", 9)
        for entry in throws[:25]:
            if y < 60:
                break
            player = entry.get("player", "")
            score_val = entry.get("score", 0)
            label = entry.get("label", "")
            remaining = entry.get("remaining", "")
            line = f"{player}  ·  {label}  ·  −{score_val}  →  Rest {remaining}"
            c.drawString(60, y, line)
            y -= 16

    c.setStrokeColor(HexColor("#222233"))
    c.setLineWidth(0.5)
    c.line(50, 45, page_w - 50, 45)
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 8)
    c.drawCentredString(page_w / 2, 30, "Contes Dart  ·  PDF gültig für 5 Minuten  ·  Gespielt mit Leidenschaft")

    c.save()
    buffer.seek(0)
    return buffer.read()


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
                "players": data.get("players") or [],
                "updatedAt": int(time.time() * 1000),
                "summaryId": data.get("summaryId"),
            }
        return jsonify({"ok": True})
    with _state_lock:
        return jsonify(_game_state)


@app.route("/api/summary", methods=["POST"])
def api_summary():
    cleanup_expired_pdfs()
    data = request.get_json(force=True, silent=True) or {}
    data["created"] = datetime.now().strftime("%d.%m.%Y %H:%M")

    try:
        pdf_bytes = generate_game_pdf(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    pdf_id = str(uuid.uuid4())[:12]
    expires = time.time() + PDF_TTL_SECONDS

    with _pdf_lock:
        _pdf_store[pdf_id] = {
            "pdf": pdf_bytes,
            "expires": expires,
            "created": data["created"],
            "winner": data.get("winner"),
        }

    return jsonify({
        "id": pdf_id,
        "url": f"/pdf/{pdf_id}",
        "qrUrl": f"/qr/{pdf_id}",
        "expiresIn": PDF_TTL_SECONDS,
        "expiresAt": int(expires * 1000),
    })


@app.route("/pdf/<pdf_id>")
def serve_pdf(pdf_id):
    cleanup_expired_pdfs()
    with _pdf_lock:
        entry = _pdf_store.get(pdf_id)
    if not entry or entry["expires"] < time.time():
        return "PDF abgelaufen oder nicht gefunden. Gültigkeit: 5 Minuten.", 410
    return send_file(
        io.BytesIO(entry["pdf"]),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"contes-dart-{pdf_id}.pdf",
    )


@app.route("/qr/<pdf_id>")
def serve_qr(pdf_id):
    cleanup_expired_pdfs()
    with _pdf_lock:
        entry = _pdf_store.get(pdf_id)
    if not entry or entry["expires"] < time.time():
        return "QR abgelaufen", 410

    base = request.host_url.rstrip("/")
    pdf_url = f"{base}/pdf/{pdf_id}"

    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(pdf_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#00ff88", back_color="#0a0a12")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


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
