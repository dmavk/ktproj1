import os
import json
import threading
from collections import deque
import requests
from flask import Flask, request, jsonify, Response

# ------------------------------------------------------
# 권장 패키지: pip install aws-sns-message-validator
try:
    from aws_sns_message_validator import SNSMessageValidator
    HAS_VALIDATOR = True
except Exception:
    HAS_VALIDATOR = False

# ------------------------------------------------------
import logging

file_handler = logging.FileHandler("app.log")
file_handler.setLevel(logging.INFO)

formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
file_handler.setFormatter(formatter)


# ===================== Flask Core =====================
app = Flask(__name__)

app.logger.setLevel(logging.INFO)
app.logger.addHandler(file_handler)


validator = SNSMessageValidator() if HAS_VALIDATOR else None

ALLOWED_TOPICS = {t.strip() for t in os.getenv("ALLOWED_SNS_TOPICS", "").split(",") if t.strip()}
BYPASS = os.getenv("BYPASS_SNS_SIGNATURE_FOR_TEST", "0") == "1"
BYPASS=True
# 최근 알림 메모리 저장소 (Dash에서 읽어 표시)
NOTI_MAX = int(os.getenv("SNS_UI_BUFFER", "500"))
_notifications = deque(maxlen=NOTI_MAX)
_lock = threading.Lock()

def safe_json_loads(s: str):
    try:
        return json.loads(s)
    except Exception:
        return None

@app.get("/health")
def health():
    return jsonify(ok=True, dash="/dash/")

@app.post("/sns")
def sns_handler():
    print("----------------------")
    raw = request.get_data(as_text=True)
    msg = safe_json_loads(raw)
    if not isinstance(msg, dict) or "Type" not in msg:
        app.logger.error("Invalid SNS body: %s", (raw or "")[:500])
        return Response("bad request", status=400)

    mtype = request.headers.get("x-amz-sns-message-type", msg.get("Type", ""))
    topic = msg.get("TopicArn")

    # 1) 서명 검증
    if not BYPASS:
        if not HAS_VALIDATOR:
            app.logger.error("Validator not available. Install aws-sns-message-validator or set BYPASS=1 for local test.")
            return Response("server not ready", status=500)
        try:
            if not validator.validate_message(msg):
                raise ValueError("Invalid SNS signature")
        except Exception as e:
            app.logger.warning("SNS signature validation failed: %s", str(e))
            return Response("forbidden", status=403)

    # 2) TopicArn 화이트리스트
    if ALLOWED_TOPICS and topic not in ALLOWED_TOPICS:
        app.logger.error("Unexpected TopicArn: %s", topic)
        return Response("forbidden", status=403)

    # 3) 타입 처리
    if mtype == "SubscriptionConfirmation":
        subscribe_url = msg.get("SubscribeURL")
        if not subscribe_url:
            return Response("missing SubscribeURL", status=400)
        try:
            r = requests.get(subscribe_url, timeout=10)
            r.raise_for_status()
            app.logger.info("✅ Subscription confirmed for topic: %s", topic)
            with _lock:
                _notifications.appendleft({
                    "ts": msg.get("Timestamp"),
                    "type": mtype,
                    "topic": topic,
                    "subject": msg.get("Subject"),
                    "message": {"info": "Subscription confirmed"},
                    "raw": msg
                })
            return "ok"
        except Exception as e:
            app.logger.error("Subscription confirmation failed: %s", e)
            return Response("subscription failed", status=500)

    elif mtype == "Notification":
        print("Notification -------------------------")
        payload = msg.get("Message", "")
        print('payload - ', payload)
        parsed = safe_json_loads(payload) if isinstance(payload, str) else payload
        record = {
            "ts": msg.get("Timestamp"),
            "type": mtype,
            "topic": topic,
            "subject": msg.get("Subject"),
            "message": parsed if parsed is not None else payload,
            "raw": msg
        }
        app.logger.info("📩 SNS Notification: %s", str(record.get("message"))[:300])

        with _lock:
            _notifications.appendleft(record)
        return "ok"

    else:
        app.logger.info("Other SNS type: %s", mtype)
        with _lock:
            _notifications.appendleft({
                "ts": msg.get("Timestamp"),
                "type": mtype,
                "topic": topic,
                "subject": msg.get("Subject"),
                "message": {"info": f"Unhandled type {mtype}"},
                "raw": msg
            })
        return "ok"

# Dash가 폴링해서 가져갈 JSON API (동일 오리진이라 CORS 불필요)
@app.get("/api/notifications")
def api_notifications():
    limit = int(request.args.get("limit", "100"))
    with _lock:
        data = list(_notifications)[:limit]
    return jsonify(data=data, count=len(data))

# ===================== Dash UI =====================
from dash import Dash, dcc, html, Input, Output, dash_table

dash_app = Dash(
    __name__,
    server=app,                 # Flask에 붙이기
    url_base_pathname="/dash/", # 대시보드 경로
    suppress_callback_exceptions=True,
)

dash_app.layout = html.Div(
    style={"maxWidth": "1200px", "margin": "0 auto", "fontFamily": "system-ui, Arial"},
    children=[
        html.H1("SNS Webhook Dashboard", style={"marginTop": "24px"}),
        html.Div([
            html.Div([
                html.Label("Rows"),
                dcc.Dropdown(
                    id="rows-dropdown",
                    options=[{"label": str(n), "value": n} for n in (50, 100, 200, 500)],
                    value=100,
                    clearable=False,
                    style={"width": "120px"}
                ),
                html.Span("  |  "),
                html.Label("Auto Refresh (sec)"),
                dcc.Dropdown(
                    id="refresh-sec",
                    options=[{"label": str(n), "value": n*1000} for n in (2, 5, 10, 30)],
                    value=5000,
                    clearable=False,
                    style={"width": "120px", "display": "inline-block"}
                ),
                html.Span("  |  "),
                html.A("Health", href="/health", target="_blank"),
            ], style={"marginBottom": "8px"}),
            dcc.Interval(id="tick", interval=5000, n_intervals=0),
            dcc.Store(id="store"),
            dash_table.DataTable(
                id="table",
                columns=[
                    {"name": "Time", "id": "ts"},
                    {"name": "Type", "id": "type"},
                    {"name": "Topic", "id": "topic"},
                    {"name": "Subject", "id": "subject"},
                    {"name": "Message", "id": "message"},
                ],
                data=[],
                page_size=20,
                style_cell={"textAlign": "left", "fontSize": "14px"},
                style_header={"fontWeight": "bold"},
                style_table={"overflowX": "auto"},
            ),
        ]),
        html.Hr(),
        html.H3("Raw (selected)"),
        html.Div(id="raw-json", style={"whiteSpace": "pre-wrap", "fontFamily": "monospace", "fontSize": "13px"}),
    ]
)

@dash_app.callback(
    Output("store", "data"),
    Input("tick", "n_intervals"),
    Input("rows-dropdown", "value"),
)
def _pull(n, rows):
    limit = rows or 100
    with _lock:
        # 최신순으로 쌓으셨으니 앞에서부터 잘라 리턴
        data = list(_notifications)[:limit]
    return data


@dash_app.callback(
    Output("table", "data"),
    Input("store", "data"),
)
def _feed_table(data):
    # message 필드는 dict일 수도, 문자열일 수도 → 보기 좋게 문자열로
    out = []
    for row in (data or []):
        msg = row.get("message")
        if isinstance(msg, dict):
            msg_str = json.dumps(msg, ensure_ascii=False)
        else:
            msg_str = str(msg)
        out.append({
            "ts": row.get("ts"),
            "type": row.get("type"),
            "topic": row.get("topic"),
            "subject": row.get("subject"),
            "message": msg_str[:2000],  # 너무 긴 경우 테이블에서는 컷
            "_raw": row.get("raw"),
        })
    return out

@dash_app.callback(
    Output("raw-json", "children"),
    Input("table", "active_cell"),
    Input("table", "data"),
)
def _show_raw(active_cell, data):
    if not active_cell or not data:
        return "Select a row to view raw JSON"
    r = data[active_cell["row"]]
    raw = r.get("_raw")
    try:
        return json.dumps(raw, indent=2, ensure_ascii=False)
    except Exception:
        return str(raw)

# ===================== Entrypoint =====================
if __name__ == "__main__":
    ssl_cert = "./ssl/fullchain.pem"
    ssl_key = "./ssl/privkey.pem"
    # 로컬 자체서명일 때 curl 테스트는 -k 필요
    app.run(host="0.0.0.0", port=8443, ssl_context=(ssl_cert, ssl_key))
