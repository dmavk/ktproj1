import os
import json
import threading
from collections import deque
import requests
from flask import Flask, request, jsonify, Response, make_response

# ------------------------------------------------------
# (권장) pip install aws-sns-message-validator
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

# ===== 설정 =====
ALLOWED_TOPICS = {t.strip() for t in os.getenv("ALLOWED_SNS_TOPICS", "").split(",") if t.strip()}
BYPASS = os.getenv("BYPASS_SNS_SIGNATURE_FOR_TEST", "0") == "1"
BYPASS=True

NOTI_MAX = int(os.getenv("SNS_UI_BUFFER", "500"))

# ===== 메모리 버퍼 =====
_notifications = deque(maxlen=NOTI_MAX)
_geo_points   = deque(maxlen=NOTI_MAX)   # 지도용 좌표 버퍼
_lock = threading.Lock()

def safe_json_loads(s: str):
    try:
        return json.loads(s)
    except Exception:
        return None

def _extract_latlon(payload: dict):
    """
    다양한 키를 허용해서 (lat, latitude, Latitude / lon, lng, longitude, Longitude)
    float(lat), float(lon) 를 반환. 없으면 (None, None)
    """
    if not isinstance(payload, dict):
        return (None, None)

    # 키 정규화
    # lat 후보
    for k in ("lat", "latitude", "Latitude", "LAT", "Lat"):
        if k in payload:
            try:
                lat = float(payload[k])
                break
            except Exception:
                lat = None
    else:
        lat = None

    # lon 후보
    for k in ("lon", "lng", "longitude", "Longitude", "LON", "Long"):
        if k in payload:
            try:
                lon = float(payload[k])
                break
            except Exception:
                lon = None
    else:
        lon = None

    return (lat, lon)

@app.get("/health")
def health():
    return jsonify(ok=True, dash="/dash/", map="/map")

# ===== SNS 수신 핸들러 =====
@app.post("/sns")
def sns_handler():
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
            app.logger.error("Validator missing. Install aws-sns-message-validator or set BYPASS=1 for local test.")
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
        payload_field = msg.get("Message", "")
        parsed = safe_json_loads(payload_field) if isinstance(payload_field, str) else payload_field

        record = {
            "ts": msg.get("Timestamp"),
            "type": mtype,
            "topic": topic,
            "subject": msg.get("Subject"),
            "message": parsed if parsed is not None else payload_field,
            "raw": msg
        }

        # 버퍼에 저장
        with _lock:
            _notifications.appendleft(record)

            # 지도용 lat/lon 추출
            lat, lon = _extract_latlon(record["message"] if isinstance(record["message"], dict) else {})
            if lat is not None and lon is not None:
                # 식별자/정보 추출(있으면)
                m = record["message"] if isinstance(record["message"], dict) else {}
                point = {
                    "ts": record["ts"],
                    "lat": lat,
                    "lon": lon,
                    "deviceId": m.get("DeviceID") or m.get("deviceId") or m.get("id"),
                    "activity": m.get("ActivityType") or m.get("activity") or m.get("type"),
                    "message": m.get("Message") or m.get("message"),
                    "topic": record["topic"],
                }
                _geo_points.appendleft(point)

            app.logger.info("📩 Notification stored: noti=%d, geo=%d", len(_notifications), len(_geo_points))
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

# ===== JSON API (Dash/Map에서 사용) =====
@app.get("/api/notifications")
def api_notifications():
    limit = int(request.args.get("limit", "100"))
    with _lock:
        data = list(_notifications)[:limit]
    return jsonify(data=data, count=len(data))

@app.get("/api/geo")
def api_geo():
    """
    지도용 단순 좌표 리스트 제공
    GET /api/geo?limit=200
    """
    limit = int(request.args.get("limit", "200"))
    with _lock:
        data = list(_geo_points)[:limit]
    return jsonify(data=data, count=len(data))

# ====== 지도 전용 페이지 (/map) ======
# Leaflet을 이용해 /api/geo 를 폴링하여 마커 표시
@app.get("/map")
def map_page():
    html = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>SNS Geo Map</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
  integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin=""/>
<style>
  html, body { height: 100%; margin: 0; }
  #controls { padding: 8px; font-family: system-ui, Arial; }
  #map { width: 100%; height: calc(100% - 56px); }
  .badge { display:inline-block; padding:2px 8px; border-radius:10px; background:#eee; margin-left:6px; }
</style>
</head>
<body>
  <div id="controls">
    <span><b>SNS Geo Map</b></span>
    <span class="badge"><a href="/health" target="_blank">/health</a></span>
    <label style="margin-left:12px;">Rows:</label>
    <select id="rows">
      <option>50</option><option selected>200</option><option>500</option><option>1000</option>
    </select>
    <label style="margin-left:12px;">Refresh(s):</label>
    <select id="refresh">
      <option>2</option><option selected>5</option><option>10</option><option>30</option>
    </select>
    <button id="fit" style="margin-left:12px;">Fit Bounds</button>
  </div>
  <div id="map"></div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
    integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
  <script>
    const map = L.map('map', { worldCopyJump: true }).setView([37.454717, 126.978020], 16); // 서울 중심
    //const map = L.map('map', { worldCopyJump: true }).setView([37.5665, 126.9780], 16); // 서울 중심
    // 타일: OSM (인터넷 필요). 오프라인 맵 서버가 있으면 URL을 교체하세요.
    //const tiles = L.tileLayer(
    //  'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    //  { maxZoom: 19, attribution: '&copy; OpenStreetMap contributors' }
    //).addTo(map);
    // 위성(기본)
const sat = L.tileLayer(
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  {
    maxZoom: 19,
    detectRetina: true,
    attribution: 'Imagery © Esri, Maxar, Earthstar Geographics, USDA, USGS, AeroGRID, IGN, and the GIS User Community'
  }
).addTo(map);

// 스트리트(대체용)
const streets = L.tileLayer(
  'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
  {
    maxZoom: 19,
    detectRetina: true,
    attribution: '&copy; OpenStreetMap contributors'
  }
);

// 라벨(경계/지명) 오버레이
const labels = L.tileLayer(
  'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
  { maxZoom: 19, opacity: 0.8 }
).addTo(map); // 기본 켜짐

// 레이어 스위처
L.control.layers(
  { 'Satellite (Esri)': sat, 'Streets (OSM)': streets },
  { 'Labels': labels }
).addTo(map);

    let markers = L.layerGroup().addTo(map);

    async function pullAndRender() {
      const rows = document.getElementById('rows').value || 200;
      try {
        const resp = await fetch('/api/geo?limit=' + rows);
        const payload = await resp.json();
        const data = payload.data || [];
        markers.clearLayers();

        const bounds = [];
        data.forEach(p => {
          if (typeof p.lat !== 'number' || typeof p.lon !== 'number') return;
          const m = L.marker([p.lat, p.lon]);
          const msg = p.message ? String(p.message) : '';
          const dev = p.deviceId ? `Device: ${p.deviceId}<br/>` : '';
          const act = (p.activity !== undefined && p.activity !== null) ? `Activity: ${p.activity}<br/>` : '';
          const top = p.topic ? `Topic: ${p.topic}<br/>` : '';
          const ts  = p.ts ? `Time: ${p.ts}<br/>` : '';
          m.bindPopup(`${dev}${act}${top}${ts}<b>(${p.lat.toFixed(6)}, ${p.lon.toFixed(6)})</b><br/>${msg}`);
          markers.addLayer(m);
          bounds.push([p.lat, p.lon]);
        });

        // bounds가 있으면 지도를 적당히 맞추기
        if (bounds.length > 0 && _shouldFit) {
          const b = L.latLngBounds(bounds);
          map.fitBounds(b.pad(0.2));
          _shouldFit = false;
        }
      } catch (e) {
        console.error(e);
      }
    }

    let _shouldFit = false;
    document.getElementById('fit').addEventListener('click', () => { _shouldFit = true; pullAndRender(); });

    // 주기 갱신
    function schedule() {
      const sec = Number(document.getElementById('refresh').value || 5);
      clearInterval(window.__timer);
      window.__timer = setInterval(pullAndRender, sec * 1000);
    }
    document.getElementById('refresh').addEventListener('change', schedule);
    document.getElementById('rows').addEventListener('change', pullAndRender);

    // 초기 로드
    pullAndRender();
    schedule();
  </script>
</body>
</html>
"""
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp

# ===== Dash (기존 대시보드 /dash/) =====
from dash import Dash, dcc, html, Input, Output, dash_table

dash_app = Dash(
    __name__,
    server=app,
    url_base_pathname="/dash/",
    suppress_callback_exceptions=True,
)

dash_app.layout = html.Div(
    style={"maxWidth": "1200px", "margin": "0 auto", "fontFamily": "system-ui, Arial"},
    children=[
        html.H1("SNS Webhook Dashboard", style={"marginTop": "24px"}),
        html.Div([
            html.Label("Rows"),
            dcc.Dropdown(
                id="rows-dropdown",
                options=[{"label": str(n), "value": n} for n in (50, 100, 200, 500)],
                value=100, clearable=False, style={"width": "120px"}
            ),
            html.Span("  |  "),
            html.Label("Auto Refresh (sec)"),
            dcc.Dropdown(
                id="refresh-sec",
                options=[{"label": str(n), "value": n*1000} for n in (2, 5, 10, 30)],
                value=5000, clearable=False, style={"width": "120px", "display": "inline-block"}
            ),
            html.Span("  |  "),
            html.A("Health", href="/health", target="_blank"),
            html.Span("  |  "),
            html.A("Map", href="/map", target="_blank"),
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
    # 서버 메모리에서 직접 읽기 (상대경로 요청 문제 제거)
    limit = rows or 100
    with _lock:
        data = list(_notifications)[:limit]
    return data

@dash_app.callback(Output("table", "data"), Input("store", "data"))
def _feed_table(data):
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
            "message": msg_str[:2000],
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

# ===== Entrypoint =====
if __name__ == "__main__":
    ssl_cert = "./ssl/fullchain.pem"
    ssl_key  = "./ssl/privkey.pem"
    app.run(host="0.0.0.0", port=8443, ssl_context=(ssl_cert, ssl_key))
