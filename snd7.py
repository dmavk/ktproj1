# app.py
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

def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    # 회전 로깅 (5MB x 5)
    file_handler = RotatingFileHandler("app.log", maxBytes=5_000_000, backupCount=5)
    file_handler.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    file_handler.setFormatter(formatter)
    # 중복 추가 방지
    if not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
        logger.addHandler(file_handler)
    return logger

# ===================== Flask Core =====================
app = Flask(__name__)
setup_logging()
app.logger.setLevel(logging.INFO)

validator = SNSMessageValidator() if HAS_VALIDATOR else None

# ===== 설정 (환경변수) =====
ALLOWED_TOPICS = {t.strip() for t in os.getenv("ALLOWED_SNS_TOPICS", "").split(",") if t.strip()}
BYPASS = os.getenv("BYPASS_SNS_SIGNATURE_FOR_TEST", "0") == "1"  # 테스트시만 1
NOTI_MAX = int(os.getenv("SNS_UI_BUFFER", "2000"))

#BYPASS_SNS_SIGNATURE_FOR_TEST=1


HOST = os.getenv("APP_HOST", "0.0.0.0")
PORT = int(os.getenv("APP_PORT", "8445"))
SSL_CERT = os.getenv("SSL_CERT_PATH", "./ssl/fullchain.pem")
SSL_KEY  = os.getenv("SSL_KEY_PATH", "./ssl/privkey.pem")

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
@app.get("/health")
def health():
    return jsonify(
        ok=True,
        dash="/dash/", map="/map", inject="/inject",
        bypass=BYPASS,
        has_validator=HAS_VALIDATOR
    )



# ---- SNS 수신 핸들러
@app.post("/sns")
def sns_handler():
    print('----------------------- /sns receive')
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
            if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
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
                app.logger.warning("⚠️ Could not extract lat/lon from message: %s", record["message"])

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
  html, body { height: 100%; margin: 0; background:#DCDCDC;}
  #controls { padding: 8px; font-family: system-ui, Arial; }
  #map { width: 100%; height: calc(100% - 56px); }
  .badge { display:inline-block; padding:2px 8px; border-radius:10px; background:#eee; margin-left:6px; }

  /* (선택) 아이콘에 클래스 달아 강제 크기 지정 */
  .leaflet-marker-icon.fire-icon {
    width: 36px !important;
    height: 36px !important;
  }
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
    const map = L.map('map', { worldCopyJump: true }).setView([37.475301, 127.005183], 14);

    // 타일 레이어
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

    // 🔥 불 아이콘 전역 정의
    const fireIcon = L.icon({
      iconUrl: '/static/icons/fire.png?v=1',  // 캐시버스터
      iconRetinaUrl: '/static/icons/fire.png?v=1',
      iconSize: [36, 36],
      iconAnchor: [18, 36],
      popupAnchor: [0, -36],
      className: 'fire-icon'
    });

    let markers = L.layerGroup().addTo(map);

    async function pullAndRender() {
      const rows = document.getElementById('rows').value || 200;
      try {
        const resp = await fetch('/api/geo?limit=' + rows, { cache: 'no-store' });
        const payload = await resp.json();
        const data = payload.data || [];
        markers.clearLayers();

        const bounds = [];
        data.forEach(p => {
          const lat = Number(p.lat), lon = Number(p.lon);
          if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
          if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return;

          // ✅ 불 아이콘 적용
          const m = L.marker([lat, lon], { icon: fireIcon, zIndexOffset: 1000 });

          const msg = p.message ? String(p.message) : '';
          const dev = p.deviceId ? `Device: ${p.deviceId}<br/>` : '';
          const act = (p.activity !== undefined && p.activity !== null) ? `Activity: ${p.activity}<br/>` : '';
          const top = p.topic ? `Topic: ${p.topic}<br/>` : '';
          const ts  = p.ts ? `Time: ${p.ts}<br/>` : '';
          m.bindPopup(`${dev}${act}${top}${ts}<b>(${lat.toFixed(6)}, ${lon.toFixed(6)})</b><br/>${msg}`);

          markers.addLayer(m);
          bounds.push([lat, lon]);
        });

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

# ---- 테스트 인젝터 화면 (/inject): 지도 클릭 → curl 생성/복사/서버 전송
@app.get("/inject")
def inject_page():
    html = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>  <!-- ✅ 모바일 뷰포트 -->
<title>SNS Test Injector</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
  integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin=""/>
<style>
  :root{
    /* 데스크톱/모바일 지도 높이 변수 */
    --map-h-desktop: 800px;
    --map-h-mobile: 62vh;
  }
  
  /* --- 다크 테마 전체 색상 --- */
  html, body { height: 100%; 
  margin: 0; 
  font-family: system-ui, -apple-system, Segoe UI, Roboto, "Noto Sans KR", Arial, sans-serif; 
  #background:#DCDCDC;
  background: #0f172a;     /* 짙은 곤색 (Deep Navy Blue) */
  color: #ffffff;           /* 텍스트를 흰색으로 */
  }
  
  
/* 라벨 (TopicArn, DeviceID 등) */
label {
  display: block;
  font-size: 12px;
  color: #ffffff;        /* 흰색으로 변경 */
  margin-top: 10px;
}
  
  
  
  
  
  
  #top { padding: 12px 14px; border-bottom: 1px solid #ddd; font-size: 15px; }

  /* 🔹 기본: 데스크톱 2열(지도 2, 폼 1) */
  #grid { display: grid; grid-template-columns: 2fr 1fr; gap: 12px; padding: 12px; }
  /* #map  { width: 100%; height: var(--map-h-desktop); border:1px solid #cfcfcf; border-radius: 10px; overflow: hidden; }*/
  #map { height: clamp(420px, 80vh, 900px); }

  /* 폼 요소 */
  /*
  label { display:block; font-size: 12px; color:#444; margin-top: 10px; }
  */
  
  input, select, textarea { width: 100%; padding: 10px 12px; border-radius: 10px; border:1px solid #cfcfcf; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }
  textarea { height: 220px; }
  .row { display:flex; gap:10px; align-items:flex-end; margin-top:10px; flex-wrap: wrap; }
  button { padding: 10px 14px; border-radius: 10px; border:1px solid #bbb; background:#f8f8f8; cursor:pointer; font-weight:700; }
  button.primary { background:#0ea5e9; color:white; border:none; }
  button:active { transform: translateY(1px); }

  /* ✅ 모바일 최적화 */
  @media (max-width: 900px){
    #grid { grid-template-columns: 1fr; } /* 1열로 전환 */
    #map  { height: var(--map-h-mobile); } /* 화면 높이 기반 */
    .row  { gap:8px; }
    textarea { height: 180px; }            /* 텍스트영역 높이 축소 */
    button, input, select { font-size: 16px; } /* iOS 확대 방지 */
  }

  /* iOS에서 100vh 버그 완화 + 스크롤/제스처 자연스럽게 */
  .leaflet-container { touch-action: pan-x pan-y; }
  
  /* 🔽 폼 줄간 간격을 줄이는 콤팩트 스타일 */
  .form-compact label{
    margin-top:6px;           /* 기존 10px → 6px */
    margin-bottom:2px;        /* 라벨과 인풋 사이도 살짝 붙이기 */
    font-size:12px;
  }
  .form-compact .row{
    margin-top:6px;           /* 기존 10px → 6px */
    gap:8px;                  /* 가로 간격도 살짝 줄임(원래 10px) */
  }
  .form-compact input,
  .form-compact select{
    padding:8px 10px;         /* 인풋 세로 패딩도 살짝 줄여 전체 높이 감소 */
  }

</style>
</head>
<body>
  <div id="top">
    <b>Notification injector</b> : 지도에서 한 점을 탭/클릭하여 좌표를 선택하세요.
    <span style="margin-left:8px"><a href="/map"  style="color:#ffffff; text-decoration:none;">/map</a></span>
  </div>

  <div id="grid">
    <!-- 좌측: 지도 -->
    <div>
      <div id="map"></div>
      <div class="row">
      
        <div style="flex:0 0 220px;">
          <label>위도(lat)</label>
          <input id="lat" placeholder="37.5665"
                 inputmode="decimal" pattern="[0-9.-]*"
                 style="width:200px;" />
        </div>
        
        <div style="flex:0 0 220px;">
          <label>경도(lon)</label>
          <input id="lon" placeholder="126.9780"
                 inputmode="decimal" pattern="[0-9.-]*"
                 style="width:200px;" />
        </div>
        
        <div style="flex:0 0 auto; min-width:140px;">
          <button id="gen" class="primary" style="width:140px;">curl 생성</button>
        </div>
      </div>
    </div>

    <!-- 우측: 폼 -->
    <div class="form-compact">
      <label>TopicArn</label>
      <input id="topic" value="arn:aws:sns:us-west-2:123456789012:MyTopic" />
      
        <div class="row">
          <div style="flex:1; margin-right:20px;">
            <label>DeviceID</label>
            <input id="device" value="demo-01" />
          </div>
          <div style="flex:1">
            <label>ActivityType</label>
            <input id="activity" value="1" inputmode="numeric" pattern="[0-9]*" />
          </div>
        </div>
        
        <div class="row">
          <div style="flex:1; margin-right:20px;">
          <label>Message</label>
          <input id="msg" value="Fire Alert" />
          </div>
          <div style="flex:1">
          <label>etc</label>
          <input id="etc" value="reserve" />
          </div>
        </div>
        
        <div class="row">
          <div style="flex:1; margin-right:20px;">
          <label>Inner Timestamp (payload)</label>
          <input id="inner_ts" value="2012-05-02T00:54:06.655Z" />
          </div>
          <div style="flex:1">
          <label>Outer Timestamp</label>
          <input id="outer_ts" value="2025-09-03T12:00:00.000Z" />
          </div>
        </div>
        
        




      <div class="row">
        <button id="copy">curl 복사</button>
        <button id="send" class="primary">서버에서 전송(POST /sns)</button>
      </div>

      <label style="margin-top:12px;">생성된 curl</label>
      <textarea id="curlbox" spellcheck="false"></textarea>
      <label>응답/로그</label>
      <textarea id="log" spellcheck="false" style="height:180px;"></textarea>
    </div>
  </div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
    integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
  <script>
    // 지도 생성
    const map = L.map('map').setView([37.5665, 126.9780], 10);
    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      {maxZoom: 19, detectRetina:true}).addTo(map);

    // 🔥 불 아이콘 (모바일에서 살짝 키움)
    const fireIcon = L.icon({
      iconUrl: '/static/icons/fire.png?v=2',
      iconSize: window.matchMedia('(max-width: 900px)').matches ? [36, 36] : [32, 32],
      iconAnchor: [16, 32],
      popupAnchor: [0, -32]
    });

    let marker = null;

    function setMarker(lat, lon) {
      if (marker) {
        marker.setLatLng([lat, lon]).setIcon(fireIcon);
      } else {
        marker = L.marker([lat, lon], { icon: fireIcon }).addTo(map);
      }
    }

    map.on('click', (e) => {
      const lat = +e.latlng.lat.toFixed(6);
      const lon = +e.latlng.lng.toFixed(6);
      document.getElementById('lat').value = lat;
      document.getElementById('lon').value = lon;
      setMarker(lat, lon);
    });

    // ✅ 화면 회전/리사이즈 시 타일 재계산 (모바일 필수)
    const reflow = () => setTimeout(() => map.invalidateSize(), 50);
    window.addEventListener('resize', reflow);
    window.addEventListener('orientationchange', reflow);

    function buildCurl() {
      const lat = document.getElementById('lat').value.trim();
      const lon = document.getElementById('lon').value.trim();
      const topic = document.getElementById('topic').value.trim();
      const device = document.getElementById('device').value.trim();
      const activity = document.getElementById('activity').value.trim();
      const msg = document.getElementById('msg').value.trim();
      const inner_ts = document.getElementById('inner_ts').value.trim();
      const outer_ts = document.getElementById('outer_ts').value.trim();

      const inner = JSON.stringify({
        DeviceID: device,
        ActivityType: Number(activity),
        Message: msg,
        Timestamp: inner_ts,
        Latitude: Number(lat),
        Longitude: Number(lon)
      });

      const outer = {
        Type: "Notification",
        MessageId: "msg-123",
        TopicArn: topic,
        Message: inner,
        Timestamp: outer_ts,
        SignatureVersion: "1",
        Signature: "FAKE",
        SigningCertURL: "https://example.com/fake-cert.pem"
      };

      const outerStr = JSON.stringify(outer);

      const curl =
`curl -k -X POST https://localhost:8443/sns \\
  -H "Content-Type: text/plain" \\
  -H "x-amz-sns-message-type: Notification" \\
  -d '${outerStr.replace(/'/g, "'\\''")}'`;

      return {curl, outerStr};
    }

    document.getElementById('gen').addEventListener('click', () => {
      const {curl} = buildCurl();
      document.getElementById('curlbox').value = curl;
    });

    document.getElementById('copy').addEventListener('click', async () => {
      const {curl} = buildCurl();
      try{
        await navigator.clipboard.writeText(curl);
        document.getElementById('log').value = "[copied] curl이 클립보드에 복사되었습니다.";
      }catch(e){
        document.getElementById('log').value = "클립보드 권한 거부됨. 텍스트를 직접 길게 눌러 복사하세요.\\n" + e;
      }
    });

    document.getElementById('send').addEventListener('click', async () => {
      const {outerStr} = buildCurl();
      const res = await fetch('/api/inject', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ outer: outerStr })
      });
      const text = await res.text();
      document.getElementById('log').value = "HTTP " + res.status + "\\n" + text;
    });
  </script>
</body>
</html>
"""
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp


# ---- 서버 포워더: /api/inject -> /sns 로 그대로 POST
@app.post("/api/inject")
def api_inject():
    try:
        data = request.get_json(force=True) or {}
        outer_str = data.get("outer", "")
        if not outer_str:
            return Response("missing outer", status=400)

        app.logger.info("[inject] relaying to /sns with headers=text/plain, len=%d", len(outer_str))
        app.logger.debug("[inject] body: %s", outer_str[:500])
        """
        headers = {
            "Content-Type": "text/plain",
            "x-amz-sns-message-type": "Notification",
        }
        """
        headers = {
            "Content-Type": "text/plain",
            "x-amz-sns-message-type": "Notification",
        }

        # ✅ 전송할 내용 로깅
        print("=== [inject] outgoing request ===")
        print("URL: https://127.0.0.1:%d/sns" % PORT)
        print("Headers: %s" % headers)
        print("Body(len=%d): %s" % ( len(outer_str), outer_str[:500]) )  # 앞부분만


        DPORT = 8443
        r = requests.post(
            f"https://127.0.0.1:{DPORT}/sns",
            data=outer_str.encode("utf-8"),
            headers=headers,
            timeout=10,
            verify=False,
        )

        print("=== [inject] response ===")
        print(f"Status: %d", r.status_code)
        print(f"Text: %s", r.text[:500])





        app.logger.info("[inject] /sns responded %d %s", r.status_code, r.text[:200])
        return Response("proxied to /sns, status={}\n{}".format(r.status_code, r.text), status=200)
    except Exception as e:
        app.logger.exception("inject error")
        return Response(f"error: {e}", status=500)




# ===================== Dash 대시보드 (/dash/) =====================
from dash import Dash, dcc, html, Input, Output, dash_table
import dash

dash_app = Dash(
    __name__,
    server=app,
    url_base_pathname="/dash/",
    suppress_callback_exceptions=True,
)

dash_app.layout = html.Div(
    style={"maxWidth": "1200px",
           "margin": "0 auto",
           "fontFamily": "system-ui, Arial",
           #"backgroundColor": "#f7fafc",  # ← 추가
           #"minHeight": "100vh",  # ← 페이지 전체 높이 채움
           #"paddingBottom": "24px"  # ← 하단 여백(선택)
           },
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
            html.Span("  |  "),
            html.A("Inject", href="/inject", target="_blank"),
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
    limit = rows or 100
    with _lock:
        data = list(_notifications)[:limit]
    return data

@dash_app.callback(
    [Output("table", "data"), Output("table", "active_cell")],
    Input("store", "data"),
)
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
    # 데이터가 바뀔 때마다 선택 해제해서 stale index 방지
    return out, None

@dash_app.callback(
    Output("raw-json", "children"),
    Input("table", "active_cell"),
    Input("table", "data"),
)
def _show_raw(active_cell, data):
    if not data:
        return "No data yet"
    if not active_cell or not isinstance(active_cell, dict) or "row" not in active_cell:
        return "Select a row to view raw JSON"
    idx = active_cell.get("row")
    if not isinstance(idx, int) or idx < 0 or idx >= len(data):
        return "Selected row is no longer available (data updated). Please select again."
    r = data[idx]
    raw = r.get("_raw")
    try:
        return json.dumps(raw, indent=2, ensure_ascii=False)
    except Exception:
        return str(raw)





# ---- 홈 화면 (/) ----

@app.get("/")
def home_page():
    html = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>드론 연계 서비스 · 홈</title>
<style>
  :root{
    --bg1:#0f172a; --bg2:#1e293b; --card:#111827cc;
    --brand:#38bdf8; --brand2:#22c55e;
    --text:#e5e7eb; --muted:#a5b4fc;
    --border:rgba(255,255,255,.12);
    --radius:16px; --shadow:0 18px 40px rgba(0,0,0,.35);
  }
  *{box-sizing:border-box}
  html,body{height:100%; margin:0; padding:0;}
  body{
    color:var(--text); font-family:system-ui,Segoe UI,Roboto,"Noto Sans KR",Arial,sans-serif;
    background:
      radial-gradient(1000px 600px at 10% -5%, rgba(56,189,248,.25), transparent 60%),
      radial-gradient(900px 700px at 110% 20%, rgba(34,197,94,.20), transparent 55%),
      linear-gradient(180deg, var(--bg1), var(--bg2));
  }
  .wrap{width:100%; max-width:1600px; margin:0 auto; padding:28px 20px 56px;}
  .top{display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:18px;}
  .brand{display:flex; align-items:center; gap:12px; font-weight:800; letter-spacing:.2px;}
  .brand-badge{
    width:40px;height:40px;border-radius:12px;
    background:linear-gradient(135deg,var(--brand),#60a5fa);
    display:flex;align-items:center;justify-content:center;color:#04243a;font-weight:900;
    box-shadow:var(--shadow);
  }
  .card{
    background:var(--card); border:1px solid var(--border); border-radius:var(--radius);
    box-shadow:var(--shadow); padding:22px;
  }
  .hero h1{margin:0 0 8px;font-size:28px}

  /* ✅ 이미지 박스 수정 — 여러 이미지 가로배치 + 높이 고정 */
  .imgbox{
    background:#0b1220;
    border:1px solid var(--border);
    border-radius:14px;
    display:flex;
    justify-content:center;
    align-items:center;
    gap:20px;
    overflow:hidden;
    height:300px;
    padding:10px;
    margin-top:12px;
  }
  .imgbox img{
    height:100%;
    width:auto;
    object-fit:contain;
    border-radius:10px;
    transition:transform .3s;
  }
  .imgbox img:hover{
    transform:scale(1.05);
  }

  /* ✅ 메뉴 4열·최대 8개 확장 (반응형) */
  .menu-grid{
    display:grid;
    grid-template-columns:repeat(auto-fit, minmax(260px, 1fr));
    gap:18px; margin-top:22px;
  }
  .menu-item{
    background:#0b1220cc;
    border:1px solid var(--border);
    border-radius:12px;
    padding:16px;
    display:flex; flex-direction:column; align-items:stretch;
    text-align:left; transition:background .18s ease, transform .08s ease;
  }
  .menu-item:hover{background:#1e293bcc; transform:translateY(-1px);}
  .menu-item .btn{
    display:inline-flex; align-items:center; justify-content:center;
    padding:11px 14px; border-radius:10px; font-weight:800;
    text-decoration:none; margin-bottom:10px; color:#07131e;
    box-shadow:0 6px 14px rgba(2,6,23,.35); border:none;
  }
  .menu-item .btn.green{background:linear-gradient(135deg,var(--brand2),#34d399);}
  .menu-item .btn.blue {background:linear-gradient(135deg,var(--brand), #60a5fa);}
  .menu-item p{font-size:14px; color:#cbd5e1; margin:0;}

  .top-links a{color:var(--muted); text-decoration:none; font-weight:700; margin-left:18px;}
  .top-links a:hover{opacity:.9; text-decoration:underline;}
</style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div class="brand">
        <div class="brand-badge">
          <img src="/static/img/simulation.jpg" alt="" style="width:28px; height:28px; border-radius:6px;"/>
        </div>
        <div style="font-size:22px;">Event Simulator</div>
      </div>
      <div class="top-links">
        <a href="/dash/" target="_blank">대시보드</a>
        <a href="/map"   target="_blank">지도 시각화</a>
        <a href="/inject" target="_blank">이벤트 Inject</a>
      </div>
    </div>

    <div class="card">
      <section class="hero">
        <h1>실시간으로 입자, 미세먼지, 열, 가스를 감지함으로써 산불 조기감지와 신속 대응</h1>
      </section>

      <!-- ✅ 여러 이미지 가로 배치 (6개까지 예시) -->
      <figure class="imgbox">
        <img src="/static/img/sns.png" alt="센서1">
        <img src="/static/img/sensor.jpg" alt="센서2">
        <img src="/static/img/gcs.jpg" alt="센서3">
        <img src="/static/img/sensor.jpg" alt="센서4">
        <img src="/static/img/gcs.jpg" alt="센서5">
        <img src="/static/img/drone-drop.jpg" alt="센서6">
      </figure>

      <div class="menu-grid">
        <div class="menu-item">
          <a class="btn green" href="/inject">⚡ 산불경보 inject</a>
          <p>지도에서 좌표를 클릭해 SNS Notification 샘플을 생성하여 <code>/sns</code> 엔드포인트로 전송합니다.</p>
        </div>
        <div class="menu-item">
          <a class="btn blue" href="/map">🗺️ 이벤트 지도</a>
          <p>수신 메시지의 위·경도를 추출해 커스텀 불 아이콘으로 마킹하고 <em>Fit Bounds</em>로 영역을 자동 맞춤합니다.</p>
        </div>
        <div class="menu-item">
          <a class="btn blue" href="/dash/">📊 실시간 대시보드</a>
          <p>SNS Notification 로그를 표로 확인하고 행 선택 시 Raw JSON을 볼 수 있습니다.</p>
        </div>
        <div class="menu-item">
          <a class="btn green" href="/health" target="_blank">🩺 서버 상태</a>
          <p>핵심 엔드포인트 상태를 점검합니다. 운영 환경에서는 민감 정보 노출을 제한하세요.</p>
        </div>
        <div class="menu-item">
          <a class="btn blue" href="#">📁 데이터 로그</a>
          <p>수집된 이벤트와 위치 데이터를 CSV/JSON으로 내보내는 기능과 연계합니다.</p>
        </div>
        <div class="menu-item">
          <a class="btn green" href="#">🌡️ 환경 센서</a>
          <p>온도/습도/VOC/미세먼지 센서의 최근 값을 조회합니다.</p>
        </div>
        <div class="menu-item">
          <a class="btn blue" href="#">📍 GPS 트래커</a>
          <p>드론/차량의 실시간 위치 스트림을 맵에 표시하고 경로를 기록합니다.</p>
        </div>
        <div class="menu-item">
          <a class="btn green" href="#">⚙️ 시스템 설정</a>
          <p>인증키, 토픽 화이트리스트, 버퍼 크기 등 서버 환경을 관리합니다.</p>
        </div>
      </div>
    </div>

    <footer style="margin-top:20px; padding-top:12px; border-top:1px solid var(--border); color:#a3a3a3; text-align:center; font-size:13px;">
      © 드론 연계 서비스 · Amazon SNS Demo
    </footer>
  </div>
</body>
</html>"""
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp




# ===================== main() & Entrypoint =====================
def main1():
    """
    로컬 개발:
      export BYPASS_SNS_SIGNATURE_FOR_TEST=1
      mkdir -p ssl && (self-signed cert 생성)
        openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
          -keyout ssl/privkey.pem -out ssl/fullchain.pem -subj "/CN=localhost"
      python app.py

    운영:
      - BYPASS=0 (기본값)
      - 공인 도메인 + 공인 인증서 + 443
      - Nginx → Gunicorn(내부) 프록시 권장
    """
    ssl_context = None
    if os.path.exists(SSL_CERT) and os.path.exists(SSL_KEY):
        ssl_context = (SSL_CERT, SSL_KEY)
    else:
        app.logger.warning("SSL cert/key not found. Running without SSL (dev only).")

    print(f"Starting app on {HOST}:{PORT}, BYPASS={BYPASS}")
    app.logger.info("Starting app on %s:%d, BYPASS=%s", HOST, PORT, BYPASS)
    app.run(host=HOST, port=PORT, ssl_context=ssl_context)
def main():
    ssl_context = (SSL_CERT, SSL_KEY) if os.path.exists(SSL_CERT) and os.path.exists(SSL_KEY) else None

    # 콘솔에도 로그 보이게 (파일 핸들러 외에 콘솔 핸들러 추가)
    logger = logging.getLogger()
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.handlers.RotatingFileHandler)
               for h in logger.handlers):
        sh = logging.StreamHandler()
        sh.setLevel(logging.INFO)
        sh.setFormatter(formatter)
        logger.addHandler(sh)

    # 리로더로 두 번 실행되는 것 방지하고 배너 한 번만 출력
    app.run(host=HOST, port=PORT, ssl_context=ssl_context,
            debug=True, use_reloader=False)

if __name__ == "__main__":
    print("127.0.0.1:8445/inject")
    main()
