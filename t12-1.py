import os
import re
import json
import threading
from collections import deque

import requests
from flask import Flask, request, jsonify, Response, make_response

# ===================== 옵션: SNS 서명 검증 =====================
# pip install aws-sns-message-validator
try:
    from aws_sns_message_validator import SNSMessageValidator
    HAS_VALIDATOR = True
except Exception:
    HAS_VALIDATOR = False
    SNSMessageValidator = None  # type: ignore

# ===================== 로깅 설정 =====================
import logging
from logging.handlers import RotatingFileHandler

file_handler = RotatingFileHandler("app.log", maxBytes=5_000_000, backupCount=5)
file_handler.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
file_handler.setFormatter(formatter)

# ===================== Flask Core =====================
app = Flask(__name__)
app.logger.setLevel(logging.INFO)
app.logger.addHandler(file_handler)

validator = SNSMessageValidator() if HAS_VALIDATOR else None

# ===== 설정 =====
ALLOWED_TOPICS = {t.strip() for t in os.getenv("ALLOWED_SNS_TOPICS", "").split(",") if t.strip()}
BYPASS = os.getenv("BYPASS_SNS_SIGNATURE_FOR_TEST", "0") == "1"  # 테스트시만 1로
BYPASS = True
print("BYPASS = ", BYPASS )
NOTI_MAX = int(os.getenv("SNS_UI_BUFFER", "2000"))

# ===== 메모리 버퍼 =====
_notifications = deque(maxlen=NOTI_MAX)
_geo_points = deque(maxlen=NOTI_MAX)  # 지도용 좌표 버퍼
_lock = threading.Lock()

# ===================== 유틸 =====================
def safe_json_loads(s: str):
    try:
        return json.loads(s)
    except Exception:
        return None

def _parse_coord(v):
    """
    '37.5', '37.5N', '127.0E', '-8.2', ' 127,000 ' 등 느슨 파싱 -> float
    방위표기(N/E=+, S/W=-) 지원
    """
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().upper().replace(",", "")  # '127,000' -> '127000'
    sign = 1.0
    if s.endswith("N") or s.endswith("E"):
        s = s[:-1]
    elif s.endswith("S") or s.endswith("W"):
        sign = -1.0
        s = s[:-1]
    m = re.search(r"[-+]?\d+(\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0)) * sign
    except Exception:
        return None

def _norm_pair(lat, lon):
    """범위 검사 후 적절히 스왑"""
    def ok(a, b):
        return (-90.0 <= a <= 90.0) and (-180.0 <= b <= 180.0)
    if lat is None or lon is None:
        return (None, None)
    if ok(lat, lon):
        return (lat, lon)
    if ok(lon, lat):  # 흔한 반대 입력(lon, lat)
        return (lon, lat)
    return (None, None)

def _extract_latlon(payload: dict):
    """
    다양한 형태를 위도(lat)·경도(lon)로 정규화.
    허용:
      - 키: lat/latitude/Lat, lon/lng/longitude/Long
      - 배열/객체: coordinates/coord/location/point/geo -> [lon, lat] or [lat, lon]
      - 문자열 방위표기: '37.5N', '126.97E' 등
    """
    if not isinstance(payload, dict):
        return (None, None)

    lat_keys = ("lat", "latitude", "Latitude", "LAT", "Lat", "LatDeg")
    lon_keys = ("lon", "lng", "longitude", "Longitude", "LON", "Long", "Lng", "LongDeg")

    lat = None
    lon = None
    for k in lat_keys:
        if k in payload:
            lat = _parse_coord(payload.get(k))
            break
    for k in lon_keys:
        if k in payload:
            lon = _parse_coord(payload.get(k))
            break

    if lat is None or lon is None:
        for key in ("coordinates", "coord", "location", "point", "geo"):
            if key in payload:
                arr = payload.get(key)
                if isinstance(arr, dict):
                    la = _parse_coord(arr.get("lat") or arr.get("latitude"))
                    lo = _parse_coord(arr.get("lon") or arr.get("lng") or arr.get("longitude"))
                    if la is not None and lo is not None:
                        lat, lon = la, lo
                        break
                if isinstance(arr, (list, tuple)) and len(arr) >= 2:
                    a = _parse_coord(arr[0])
                    b = _parse_coord(arr[1])
                    lat, lon = _norm_pair(b, a)  # [lon, lat] 가정 후 정규화에서 스왑 판단
                    break

    lat, lon = _norm_pair(lat, lon)
    return (lat, lon)

# ===================== 라우트 =====================

# ---- 홈페이지 (/) ----
# ---- 홈페이지 (/) ----
@app.get("/")
def home():
    html = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>드론연계 산불 화재 지원</title>
<style>
  :root{
    --bg:#0b1220;            /* 배경 어두운 남청색 */
    --card:#111a2e;          /* 카드 배경 */
    --accent:#5cc3ff;        /* 포인트(하늘색) */
    --accent-2:#9dff8a;      /* 보조 포인트(라임) */
    --text:#e6f0ff;          /* 본문 텍스트 */
    --muted:#a8b3c7;         /* 보조 텍스트 */
    --border:rgba(255,255,255,.08);
    --shadow:0 10px 30px rgba(0,0,0,.35);
    --radius:16px;
  }
  *{box-sizing:border-box}
  html,body{height:100%}
  body{
    margin:0;
    font-family: system-ui, -apple-system, Segoe UI, Roboto, "Noto Sans KR", Arial, sans-serif;
    color:var(--text);
    background:
      radial-gradient(1200px 800px at 10% -10%, #123057 0%, transparent 60%),
      radial-gradient(900px 700px at 110% 20%, #1a3a6a 0%, transparent 55%),
      linear-gradient(180deg, #0b1220 0%, #0b1220 100%);
  }
  .wrap{
    max-width:1100px;
    margin:0 auto;
    padding:28px 20px 56px;
  }
  .topbar{
    display:flex; align-items:center; justify-content:space-between;
    gap:12px; margin-bottom:24px;
  }
  .brand{
    display:flex; align-items:center; gap:12px;
    font-weight:800; letter-spacing:.2px;
  }
  .brand-badge{
    width:38px; height:38px; border-radius:12px;
    background:linear-gradient(135deg, var(--accent), var(--accent-2));
    box-shadow: var(--shadow);
    display:flex; align-items:center; justify-content:center;
    color:#00101a; font-size:20px; font-weight:900;
  }
  .status{
    font-size:14px; color:var(--muted);
    border:1px solid var(--border); padding:6px 10px; border-radius:999px;
    display:flex; align-items:center; gap:8px; backdrop-filter: blur(6px);
  }
  .dot{width:8px; height:8px; border-radius:50%; background:#ffb54c; box-shadow:0 0 0 2px rgba(255,181,76,.15)}
  .dot.ok{ background:#3cff89; box-shadow:0 0 0 2px rgba(60,255,137,.15) }
  .dot.err{ background:#ff5d62; box-shadow:0 0 0 2px rgba(255,93,98,.15) }

  .hero{
    border:1px solid var(--border);
    background:linear-gradient(180deg, rgba(255,255,255,.03), rgba(255,255,255,.01));
    border-radius:calc(var(--radius) + 6px);
    padding:28px 24px;
    box-shadow: var(--shadow);
  }
  .hero h1{
    margin:0 0 10px; font-size:30px; line-height:1.2; letter-spacing:.2px;
  }
  .hero p{ margin:0; color:var(--muted) }
  .cta{
    margin-top:18px; display:flex; flex-wrap:wrap; gap:10px;
  }
  .btn{
    display:inline-flex; align-items:center; gap:10px;
    padding:12px 16px; border-radius:12px; text-decoration:none; font-weight:700;
    border:1px solid var(--border); color:var(--text);
    background:#0f1a30;
    transition: transform .08s ease, box-shadow .2s ease, background .2s ease;
  }
  .btn:hover{ transform: translateY(-1px); box-shadow:0 8px 20px rgba(0,0,0,.25) }
  .btn.primary{
    background:linear-gradient(135deg, var(--accent), #2aa4ff);
    color:#001018; border-color:transparent;
  }

  .grid{
    display:grid; gap:16px; margin-top:18px;
    grid-template-columns: repeat(12, 1fr);
  }
  .card{
    grid-column: span 12;
    border:1px solid var(--border);
    background:var(--card);
    border-radius:var(--radius); padding:18px;
    box-shadow: var(--shadow);
  }
  .card h3{ margin:0 0 6px; font-size:18px }
  .card p{ margin:0; color:var(--muted); font-size:14.5px }
  .card a{ color:var(--accent); text-decoration:none; font-weight:700 }
  .card a:hover{ text-decoration:underline }
  .card .go{
    margin-top:12px; display:inline-flex; align-items:center; gap:8px;
    padding:10px 12px; border-radius:10px; border:1px solid var(--border);
    background:#0f1a30; color:var(--text); text-decoration:none; font-weight:700;
  }
  .card .go:hover{ transform: translateY(-1px) }

  .col-4{ grid-column: span 12 }
  @media(min-width:720px){
    .col-4{ grid-column: span 4 }
  }

  footer{
    margin-top:28px; padding-top:16px; border-top:1px solid var(--border);
    color:var(--muted); font-size:13.5px; text-align:center;
  }
</style>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
      <div class="brand">
        <div class="brand-badge">🔥</div>
        <div>드론연계 산불 화재 지원</div>
      </div>
      <div class="status"><span class="dot" id="dot"></span><span id="stxt">상태 확인 중…</span></div>
    </div>

    <section class="hero">
      <h1>드론연계 산불 화재 지원</h1>
      <p>
        현장의 드론/센서 이벤트를 실시간으로 수집·검증하고, 알림 대시보드와 지도로 가시화합니다.<br/>
        아래 메뉴에서 필요 기능으로 이동하세요.
      </p>
      <div class="cta">
        <a class="btn primary" href="/dash">📊 대시보드 바로가기</a>
        <a class="btn" href="/map">🗺️ 지도 보기</a>
        <a class="btn" href="/health">✅ 상태 확인</a>
      </div>
    </section>

    <div class="grid">
      <div class="card col-4">
        <h3>📊 SNS Dashboard (/dash)</h3>
        <p>수신된 SNS 알림을 표로 확인하고, 선택한 항목의 Raw JSON을 즉시 분석합니다.</p>
        <a class="go" href="/dash">대시보드 열기 →</a>
      </div>
      <div class="card col-4">
        <h3>🗺️ Geo Map (/map)</h3>
        <p>메시지에서 추출한 위·경도 좌표를 지도에 표시합니다. 새 좌표가 들어오면 자동으로 갱신됩니다.</p>
        <a class="go" href="/map">지도 열기 →</a>
      </div>
      <div class="card col-4">
        <h3>✅ Health (/health)</h3>
        <p>API 응답으로 서버 상태를 반환합니다. 모니터링/프로브 구성에 활용할 수 있습니다.</p>
        <a class="go" href="/health">상태 보기 →</a>
      </div>
    </div>

    <footer>
      © 드론연계 산불 화재 지원 · 실시간 SNS 수신 · 대시보드 · 지도 시각화
    </footer>
  </div>

  <script>
    // /health 상태를 읽어와 상단 상태 뱃지 갱신
    async function ping(){
      const dot = document.getElementById('dot');
      const stx = document.getElementById('stxt');
      try{
        const r = await fetch('/health', {cache:'no-store'});
        if(!r.ok) throw new Error(r.status);
        const j = await r.json();
        dot.classList.add('ok'); dot.classList.remove('err');
        stx.textContent = '정상 동작 중';
      }catch(e){
        dot.classList.add('err'); dot.classList.remove('ok');
        stx.textContent = '오프라인 또는 오류';
      }
    }
    ping();
    setInterval(ping, 5000);
  </script>
</body>
</html>"""
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp


@app.get("/health")
def health():
    return jsonify(ok=True, dash="/dash/", map="/map")

# ---- SNS 수신 핸들러
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
                    "raw": msg,
                })
            return "ok"
        except Exception as e:
            app.logger.error("Subscription confirmation failed: %s", e)
            return Response("subscription failed", status=500)

    elif mtype == "Notification":
        payload_field = msg.get("Message", "")
        print("payload_field = ", payload_field )
        parsed = safe_json_loads(payload_field) if isinstance(payload_field, str) else payload_field

        record = {
            "ts": msg.get("Timestamp"),
            "type": mtype,
            "topic": topic,
            "subject": msg.get("Subject"),
            "message": parsed if parsed is not None else payload_field,
            "raw": msg,
        }

        with _lock:
            _notifications.appendleft(record)

            # 지도용 lat/lon 추출 + 유효성 검증
            lat, lon = _extract_latlon(record["message"] if isinstance(record["message"], dict) else {})
            #if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
            if lat is not None and lon is not None:
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
            else:
                app.logger.warning("Drop invalid coords: lat=%s lon=%s", lat, lon)

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
                "raw": msg,
            })
        return "ok"

# ---- JSON API
@app.get("/api/notifications")
def api_notifications():
    limit = int(request.args.get("limit", "100"))
    with _lock:
        data = list(_notifications)[:limit]
    return jsonify(data=data, count=len(data))

@app.get("/api/geo")
def api_geo():
    limit = int(request.args.get("limit", "200"))
    with _lock:
        data = list(_geo_points)[:limit]
    return jsonify(data=data, count=len(data))

@app.get("/api/geo/last")
def api_geo_last():
    with _lock:
        pt = _geo_points[0] if _geo_points else None
    return jsonify(data=pt, ok=pt is not None)

# ---- 지도 화면 (/map)
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
    // 초기 중심/줌
    const map = L.map('map', { worldCopyJump: true }).setView([37.454717, 126.978020], 16);

    // 타일 레이어: 위성(기본) + 스트리트 + 라벨
    const sat = L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      { maxZoom: 19, detectRetina: true,
        attribution: 'Imagery © Esri, Maxar, Earthstar Geographics, USDA, USGS, AeroGRID, IGN, and the GIS User Community'
      }
    ).addTo(map);

    const streets = L.tileLayer(
      'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
      { maxZoom: 19, detectRetina: true, attribution: '&copy; OpenStreetMap contributors' }
    );

    const labels = L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
      { maxZoom: 19, opacity: 0.8 }
    ).addTo(map);

    L.control.layers({ 'Satellite (Esri)': sat, 'Streets (OSM)': streets }, { 'Labels': labels }).addTo(map);

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
          if (typeof p.lat !== 'number' || typeof p.lon !== 'number') {
            console.warn("invalid lat/lon", p);
            return; 
          }
          if (p.lat < -90 || p.lat > 90 || p.lon < -180 || p.lon > 180) {
            console.warn("out of range", p);
            return;
        }
          const m = L.marker([p.lat, p.lon]);
          const msg = p.message ? String(p.message) : '';
          const dev = p.deviceId ? `Device: ${p.deviceId}<br/>` : '';
          const act = (p.activity !== undefined && p.activity !== null) ? `Activity: ${p.activity}<br/>` : '';
          const top = p.topic ? `Topic: ${p.topic}<br/>` : '';
          const ts  = p.ts ? `Time: ${p.ts}<br/>` : '';
          m.bindPopup(`${dev}${act}${top}${ts}<b>(${p.lat.toFixed(6)}, ${p.lon.toFixed(6)})</b><br/>${msg}`);
          // 마커 클릭 시 확대를 원하면 다음 라인 주석 해제:
          // m.on('click', () => map.setView([p.lat, p.lon], Math.min(map.getZoom()+2, 18)));
          markers.addLayer(m);
          bounds.push([p.lat, p.lon]);
        });

        // 필요시에만 Fit
        if (bounds.length > 0 && _shouldFit) {
          const b = L.latLngBounds(bounds);
          map.fitBounds(b.pad(0.2));
          _shouldFit = false;
        }
      } catch (e) {
        console.error(e);
      }
    }

    let _shouldFit = false; // 초기엔 자동 맞춤 끔
    document.getElementById('fit').addEventListener('click', () => { _shouldFit = true; pullAndRender(); });

    function schedule() {
      const sec = Number(document.getElementById('refresh').value || 5);
      clearInterval(window.__timer);
      window.__timer = setInterval(pullAndRender, sec * 1000);
    }
    document.getElementById('refresh').addEventListener('change', schedule);
    document.getElementById('rows').addEventListener('change', pullAndRender);

    pullAndRender();
    schedule();
  </script>
</body>
</html>
"""
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp

# ===================== Dash 대시보드 (/dash/) =====================
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
"""
@dash_app.callback(
    Output("store", "data"),
    Input("tick", "n_intervals"),
    Input("rows-dropdown", "value"),
)
def _pull(n, rows):
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
"""
# 1) store(data) 채우기: /api/notifications에서 바로 읽음
dash_app.clientside_callback(
    """
    async function(n, rows){
      const limit = rows || 100;
      try{
        const resp = await fetch('/api/notifications?limit=' + limit, {cache:'no-store'});
        const j = await resp.json();
        return j.data || [];
      }catch(e){
        console.error('fetch /api/notifications failed', e);
        return [];
      }
    }
    """,
    Output("store", "data"),
    Input("tick", "n_intervals"),
    Input("rows-dropdown", "value"),
)

# 2) 표에 바인딩
dash_app.clientside_callback(
    """
    function(data){
      const out = [];
      (data || []).forEach(row => {
        const msg = row && row.message;
        const msg_str = (msg && typeof msg === 'object') ? JSON.stringify(msg) : String(msg || '');
        out.push({
          ts: row.ts || '',
          type: row.type || '',
          topic: row.topic || '',
          subject: row.subject || '',
          message: msg_str.slice(0, 2000),
          _raw: row.raw || null
        });
      });
      return out;
    }
    """,
    Output("table", "data"),
    Input("store", "data"),
)



@dash_app.callback(
    Output("raw-json", "children"),
    Input("table", "active_cell"),
    Input("table", "data"),
)
def _show_raw(active_cell, data):
    # 데이터 없음
    if not data:
        return "No data yet"

    # active_cell이 없거나 형식이 이상하면 안내만
    if not active_cell or not isinstance(active_cell, dict) or "row" not in active_cell:
        return "Select a row to view raw JSON"

    idx = active_cell.get("row")
    # 인덱스 유효성 검사
    if not isinstance(idx, int) or idx < 0 or idx >= len(data):
        return "Selected row is no longer available (data updated). Please select again."

    r = data[idx]
    raw = r.get("_raw")
    try:
        return json.dumps(raw, indent=2, ensure_ascii=False)
    except Exception:
        return str(raw)

# ===================== Entrypoint =====================
if __name__ == "__main__":
    # 로컬 자체서명 TLS라면 curl 테스트에 -k 필요
    ssl_cert = "./ssl/fullchain.pem"
    ssl_key = "./ssl/privkey.pem"
    app.run(host="0.0.0.0", port=8443, ssl_context=(ssl_cert, ssl_key))
