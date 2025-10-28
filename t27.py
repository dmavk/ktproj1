import os
import re
import json
import threading
from collections import deque
import dash_bootstrap_components as dbc
import requests
from flask import Flask, request, jsonify, Response, make_response
# test 1028


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


# ==================== REDIS설정 =====================
import redis
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
rds = redis.Redis.from_url(REDIS_URL, decode_responses=True)
NOTI_KEY, GEO_KEY = "sns:noti", "sns:geo"

# ===================== Flask Core =====================
app = Flask(__name__)
app.logger.setLevel(logging.INFO)
app.logger.addHandler(file_handler)



# 🔗 imgp 블루프린트 등록
"""
from imgp import imgbp
app.register_blueprint(imgbp)
"""
from imgmap import imgbp
app.register_blueprint(imgbp)
from  imginbox import imgsvc
app.register_blueprint(imgsvc)

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

# 앱 시작 직후 한 번만 실행(임시 디버그)
print("=== Registered routes ===")
for rule in app.url_map.iter_rules():
    print(rule, list(rule.methods))




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
@app.post("/sns")
def sns_handler():
    print('jjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjj')
    # 원본 바디/헤더 확인 (디버그에 유용)
    raw = request.get_data(as_text=True) or ""
    app.logger.info("SNS raw body: %s", raw[:1000])
    app.logger.info("SNS headers: %s", dict(request.headers))

    # JSON 파싱
    msg = safe_json_loads(raw) or {}
    mtype = request.headers.get("x-amz-sns-message-type", msg.get("Type", "")) or ""
    topic = msg.get("TopicArn")


    # (선택) Topic 화이트리스트 체크
    if ALLOWED_TOPICS and topic not in ALLOWED_TOPICS:
        app.logger.error("Unexpected TopicArn: %s", topic)
        return Response("forbidden", status=403)

    # 테스트/로컬이면 BYPASS=True 로 서명검증 생략
    # 운영 시에는 has validator 체크 후 검증 로직 추가 권장
    if not BYPASS and validator:
        try:
            if not validator.validate_message(msg):
                app.logger.warning("SNS signature validation failed")
                return Response("forbidden", status=403)
        except Exception as e:
            app.logger.warning("SNS signature validation error: %s", e)
            return Response("forbidden", status=403)

    # 타입 분기
    if mtype == "SubscriptionConfirmation":
        subscribe_url = msg.get("SubscribeURL")
        if not subscribe_url:
            return Response("missing SubscribeURL", status=400)
        try:
            r = requests.get(subscribe_url, timeout=10)
            r.raise_for_status()
            app.logger.info("✅ Subscription confirmed for topic: %s", topic)
            # (원하면 여기서 _notifications 저장)
            return "ok"
        except Exception as e:
            app.logger.error("Subscription confirmation failed: %s", e)
            return Response("subscription failed", status=500)

    elif mtype == "Notification":
        payload = msg.get("Message", "")
        parsed = safe_json_loads(payload) if isinstance(payload, str) else payload

        # 이벤트 값 추출
        event_val = None
        if isinstance(parsed, dict):
            event_val = parsed.get("Message") or parsed.get("message")
        if not event_val and isinstance(payload, str) and payload:
            event_val = payload
        event_val = '산불 발생'
        print("***** event_val = ", event_val )
        record = {
            "ts": msg.get("Timestamp"),
            "type": mtype,
            "event": event_val,  # ← 그대로 저장
            "topic": topic,
            "subject": msg.get("Subject"),
            "message": parsed if parsed is not None else payload,
            "raw": msg,
        }
        # 👉 여기서 기존처럼 저장소(deque/Redis)에 push
        with _lock:
            _notifications.appendleft(record)
            lat, lon = _extract_latlon(record["message"] if isinstance(record["message"], dict) else {})
            if lat is not None and lon is not None:
                m = record["message"] if isinstance(record["message"], dict) else {}
                _geo_points.appendleft({
                    "ts": record["ts"], "lat": lat, "lon": lon,
                    "deviceId": m.get("DeviceID") or m.get("deviceId") or m.get("id"),
                    "activity": m.get("ActivityType") or m.get("activity") or m.get("type"),
                    "message": m.get("Message") or m.get("message"),
                    "topic": record["topic"],
                })
        app.logger.info("📩 SNS Notification stored")
        return "ok"

    else:
        app.logger.info("Unhandled SNS type: %s", mtype)
        return "ok"

# ---- 홈페이지 (/) ----
@app.get("/")
def home():
    html = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>통합 관제 플랫폼 </title>
<style>
  :root{
    /* 기존보다 한 톤 밝은 다크블루 */
    --bg:#16243f; --card:#1d2c4d; --accent:#66c2ff; --accent-2:#9dff9d;
    --text:#f1f6ff; --muted:#c3cde0; --border:rgba(255,255,255,.10);
    --shadow:0 10px 24px rgba(0,0,0,.30); --radius:16px; --vhpad:180px;
  }
  *{box-sizing:border-box}
  html,body{height:100%}
  body{
    margin:0; color:var(--text);
    font-family: system-ui, -apple-system, Segoe UI, Roboto, "Noto Sans KR", Arial, sans-serif;
    background:
      linear-gradient(180deg, rgba(255,255,255,.03), rgba(255,255,255,0)),
      radial-gradient(1200px 800px at 10% -10%, rgba(90,130,200,.30) 0%, transparent 60%),
      radial-gradient(900px 700px at 110% 20%, rgba(80,120,200,.25) 0%, transparent 55%),
      var(--bg);
  }
  .wrap{max-width:none; width:100%; margin:0; padding:24px 24px 56px;}
  .topbar{display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:16px;}
  .brand{display:flex; align-items:center; gap:12px; font-weight:800; letter-spacing:.2px;}
  .brand-badge{width:38px; height:38px; border-radius:12px;
    background:linear-gradient(135deg, var(--accent), var(--accent-2));
    box-shadow:var(--shadow); display:flex; align-items:center; justify-content:center;
    color:#00101a; font-size:20px; font-weight:900;}
  .status{font-size:14px; color:var(--muted); border:1px solid var(--border); padding:6px 10px; border-radius:999px; display:flex; align-items:center; gap:8px;}
  .dot{width:8px; height:8px; border-radius:50%; background:#ffb54c; box-shadow:0 0 0 2px rgba(255,181,76,.12)}
  .dot.ok{ background:#3cff89; box-shadow:0 0 0 2px rgba(60,255,137,.12)}
  .dot.err{ background:#ff5d62; box-shadow:0 0 0 2px rgba(255,93,98,.12)}

  .card{border:1px solid var(--border); background:var(--card); border-radius:calc(var(--radius) + 6px); box-shadow:var(--shadow);}
  #home-card{ padding:24px; min-height: calc(100vh - var(--vhpad)); }

  .hero h1{ margin:0 0 10px; font-size:30px; line-height:1.2; letter-spacing:.2px; }
  .hero p{ margin:0; color:var(--muted) }
  .cta{ margin-top:18px; display:flex; flex-wrap:wrap; gap:10px; }
  .btn{ display:inline-flex; align-items:center; gap:10px; padding:12px 16px; border-radius:12px; text-decoration:none; font-weight:700; border:1px solid var(--border); color:var(--text); background:#20355f; transition: transform .08s ease, box-shadow .2s ease, background .2s ease;}
  .btn:hover{ transform: translateY(-1px); box-shadow:0 8px 18px rgba(0,0,0,.24) }
  .btn.primary{ background:linear-gradient(135deg, var(--accent), #2aa4ff); color:#001018; border-color:transparent; }

  /* 기능 카드 */
  .grid{ display:grid; gap:16px; margin-top:18px; grid-template-columns: repeat(12, 1fr); }
  .card2{ grid-column: span 12; border:1px solid var(--border); background:var(--card); border-radius:var(--radius); padding:18px; box-shadow: var(--shadow);}
  .card2 h3{ margin:0 0 6px; font-size:18px }
  .card2 p{ margin:0; color:var(--muted); font-size:14.5px }
  .card2 a{ color:var(--accent); text-decoration:none; font-weight:700 }
  .card2 a:hover{ text-decoration:underline }
  .card2 .go{ margin-top:12px; display:inline-flex; align-items:center; gap:8px; padding:10px 12px; border-radius:10px; border:1px solid var(--border); background:#20355f; color:var(--text); text-decoration:none; font-weight:700; }
  .card2 .go:hover{ transform: translateY(-1px) }

  /* 🔹 이미지 갤러리 (메뉴 강조를 죽이지 않도록, CTA 아래 배치 + 절제된 높이) */
  .media-grid{ display:grid; gap:12px; margin-top:20px; grid-template-columns:repeat(12,1fr); }
  .media{ position:relative; overflow:hidden; border-radius:14px; border:1px solid var(--border); background:#0f1d37; }
  .media img{ width:100%; height:260px; object-fit:cover; display:block; filter: saturate(1.05) contrast(1.02); }
  .media figcaption{ position:absolute; left:10px; bottom:10px; padding:6px 10px; border-radius:999px; background:rgba(0,0,0,.35); color:#fff; font-size:12.5px; letter-spacing:.2px; }
  .media-lg{ grid-column: span 7; } /* 큰 이미지 1개 */
  .media-md{ grid-column: span 5; } /* 중간 1개  */
  .media-row{ grid-column: span 12; } /* 가로로 넓게 1개 */

  @media (max-width: 900px){
    .media img{ height:220px; }
    .media-lg,.media-md,.media-row{ grid-column: span 12; }
  }

  footer{ margin-top:28px; padding-top:16px; border-top:1px solid var(--border); color:var(--muted); font-size:13.5px; text-align:center; }
</style>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
    
    <div class="brand" style="display:flex; align-items:center; gap:10px; font-weight:800;">
      <div style="width:80px; height:40px; border-radius:10%; 
                  background:#202020; display:flex; align-items:center; justify-content:center;">
        <img src="/static/icons/instech_logo1.png" alt="드론 아이콘"
             style="width:80px; height:40px; display:block;">
      </div>
      <div style="font-size:22px;">통합 관제 플랫폼</div>
    </div>

      
      <div class="status"><span class="dot" id="dot"></span><span id="stxt">상태 확인 중…</span></div>
    </div>

    <div class="card" id="home-card">
      <section class="hero">
      
    <h3 style="display:flex; align-items:center; gap:10px; margin-top:-10px; margin-bottom:0px;">
      <img src="/static/img/adrone.gif" alt="불 아이콘" 
           style="width:28px; height:28px; display:block;">
      드론연계 산불 대응 시스템
    <img src="/static/img/fire.gif" alt="불 아이콘" 
           style="width:28px; height:28px; display:block;">
    </h3>

        
        
              <!-- 🔹 이미지 갤러리: CTA 아래, 기능 카드 위 -->
        <section style="display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-top:-10px;">
          <figure style="position:relative; overflow:hidden; border-radius:14px; border:1px solid rgba(255,255,255,0.1); background:#0f1d37;">
            <img src="/static/img/sensor.jpg" alt="산불 감지 센서와 원격 감시" 
                 style="width:100%; height:150px; object-fit:cover; display:block;">
            <figcaption style="position:absolute; left:10px; bottom:10px; padding:6px 10px;
                               border-radius:999px; background:rgba(0,0,0,.35); color:#fff; font-size:14px; font-weight:bold;">
              조기 감지 센서
            </figcaption>
          </figure>
        
          <figure style="position:relative; overflow:hidden; border-radius:14px; border:1px solid rgba(255,255,255,0.1); background:#0f1d37;">
            <img src="/static/img/gcs.jpg" alt="재난/사고 감시" 
                 style="width:100%; height:150px; object-fit:cover; display:block;">
            <figcaption style="position:absolute; left:10px; bottom:10px; padding:6px 10px;
                               border-radius:999px; background:rgba(0,0,0,.35); color:#fff; font-size:14px; font-weight:bold;">
              재난/사고 감시
            </figcaption>
          </figure>

                 
          <figure style="position:relative; overflow:hidden; border-radius:14px; border:1px solid rgba(255,255,255,0.1); background:#0f1d37;">
            <img src="/static/img/mission.jpg" alt="임무 경로 설정" 
                 style="width:100%; height:150px; object-fit:cover; display:block;">
            <figcaption style="position:absolute; left:10px; bottom:10px; padding:6px 10px;
                               border-radius:999px; background:rgba(0,0,0,.35); color:#fff; font-size:14px; font-weight:bold;">
              임무 경로 설정
            </figcaption>
          </figure>
        
          <figure style="position:relative; overflow:hidden; border-radius:14px; border:1px solid rgba(255,255,255,0.1); background:#0f1d37;">
            <img src="/static/img/drone-drop.jpg" alt="드론 살수(산불 초동대응)" 
                 style="width:100%; height:150px; object-fit:cover; display:block;">
            <figcaption style="position:absolute; left:10px; bottom:10px; padding:6px 10px;
                               border-radius:999px; background:rgba(0,0,0,.35); color:#fff; font-size:14px; font-weight:bold;">
              드론 살수(산불 초동대응)
            </figcaption>
          </figure> 
          
        </section>
        
      <!-- ✅ 여기서 수평선 추가 -->
      <hr style="margin:20px 0; border:0; border-top:1px solid rgba(255,255,255,0.15);">
        
        
      <!-- ✅ 문장 위치를 버튼 아래로, 3줄 간격 -->
      <p style="margin-top: 1em;">
            주요 기능
        </p>
        
        
        
        
        <div class="cta" style="display:flex; gap:100px; flex-wrap:wrap; align-items:center; margin-top:5px;">
        

          <!-- 📊 SNS Message -->
          <a href="/dash/"
             style="display:inline-flex; align-items:center; gap:10px;
                    padding:12px 18px; border-radius:14px; text-decoration:none;
                    background:linear-gradient(135deg,#FFD54F,#FFC107);   /* 노란색 버튼 */
                    color:#000; font-weight:800; box-shadow:0 6px 16px rgba(0,0,0,.25);">
            <img src="/static/icons/SNS.png" alt="SNS Message 아이콘"
                 style="width:30px; height:30px; display:block; border-radius:4px;">
            산불 메세지
          </a>
        
          <!-- 🗺️ 지도 보기 -->
          <a href="/map"
             style="display:inline-flex; align-items:center; gap:10px;
                    padding:12px 18px; border-radius:14px; text-decoration:none;
                    background:linear-gradient(135deg,#36d1dc,#5b86e5);
                    color:#fff; font-weight:800; box-shadow:0 6px 16px rgba(0,0,0,.25);">
            <img src="/static/icons/geomap.png" alt="지도 아이콘"
                 style="width:30px; height:30px; display:block; border-radius:4px;">
            이벤트 지도
          </a>












          <!-- 🧠 드론 지상관제 -->
          <a href="/feature1/"
             style="display:inline-flex; align-items:center; gap:10px;
                    padding:12px 18px; border-radius:14px; text-decoration:none;
                    background:linear-gradient(135deg,#36d1dc,#5b86e5);
                    color:#fff; font-weight:800; box-shadow:0 6px 16px rgba(0,0,0,.25);">
            <img src="/static/icons/gcs.png" alt="기능1 아이콘"
                 style="width:30px; height:30px; display:block; border-radius:4px;">
            드론 지상관제
          </a>
        
          <!-- 📦 리포팅 -->
          <a href="/feature2"
             style="display:inline-flex; align-items:center; gap:10px;
                    padding:12px 18px; border-radius:14px; text-decoration:none;
                    background:linear-gradient(135deg,#36d1dc,#5b86e5);
                    color:#fff; font-weight:800; box-shadow:0 6px 16px rgba(0,0,0,.25);">
            <img src="/static/icons/report.png" alt="리포팅 아이콘"
                 style="width:30px; height:30px; display:block; border-radius:4px;">
            리포팅
          </a>
          
                  
          <!-- ✅ 서버 상태 -->
          <a href="/health"
             style="display:inline-flex; align-items:center; gap:10px;
                    padding:12px 18px; border-radius:14px; text-decoration:none;
                    background:linear-gradient(135deg,#36d1dc,#5b86e5);
                    color:#fff; font-weight:800; box-shadow:0 6px 16px rgba(0,0,0,.25);">
            <img src="/static/img/hb.png" alt="상태 아이콘"
                 style="width:30px; height:30px; display:block; border-radius:4px;">
            서버 상태
          </a>
        </div>
        
        


        
        <!-- ✅ 여기서 수평선 추가 -->
        <hr style="margin:20px 0; border:0; border-top:1px solid rgba(255,255,255,0.15);">

      </section>


 


        <section style="display:grid; grid-template-columns:repeat(3,1fr); gap:16px; margin-top:-10px;">
        
        
        


          
          <!-- 새롭게 이 블록으로 교체 -->
          <div style="border-radius:12px; background:#1c2a45; padding:20px;">
            <!-- 카드 제목 -->
            <h3 style="margin:0 0 6px; font-size:18px; display:flex; align-items:center; gap:10px;">
              <img src="/static/img/fire.png" alt="SNS Message 아이콘"
                   style="width:20px; height:20px; display:inline-block; border-radius:4px;">
              센서 메세지 수신 · 지도표시
            </h3>
            
            <!-- 설명 -->
            <p style="margin:0; color:#c3cde0;">
              수신된 SNS 알림을 표로 확인하고, 선택한 항목의 Raw JSON을 즉시 분석합니다.
              메시지에서 추출한 위·경도 좌표를 지도에 표시합니다. 새 좌표가 들어오면 자동 갱신됩니다.
            </p>
            
            <!-- 액션 버튼 -->
            <div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:10px;">
              <a href="/dash/" style="display:inline-block; padding:10px 14px;
                                          border-radius:8px; background:#0f1d37; color:#fff;
                                          text-decoration:none; font-weight:bold;">
                이벤트 수신함 열기 →
              </a>
              <a href="/map" style="display:inline-block; padding:10px 14px;
                                        border-radius:8px; background:#0f1d37; color:#fff;
                                        text-decoration:none; font-weight:bold;">
                지도표시 열기 →
              </a>
            </div>
          </div>
          
          
          
          <!-- 이미지 이벤트 처리, 이 블록으로 교체 -->
          <div style="border-radius:12px; background:#1c2a45; padding:20px;">
            <!-- 카드 제목 -->
            <h3 style="margin:0 0 6px; font-size:18px; display:flex; align-items:center; gap:10px;">
              <img src="/static/img/image.jpg" alt="이미지 이벤트 아이콘"
                   style="width:20px; height:20px; display:inline-block; border-radius:4px;">
              이미지 파일 수신 · 지도표시
            </h3>
            
            <!-- 설명 -->
            <p style="margin:0; color:#c3cde0;">
              외부 시스템(HTTP/S3/Webhook 등)에서 전송한 이미지 파일을 수신하고,
              수신된 이미지 파일에 포함된 EXIF/메타데이터의 위치 정보를 자동으로 분석해서 이미지 제작된 지점을 지도에 표시합니다.
            </p>
            
            <!-- 액션 버튼 -->
            <div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:10px;">
              <a href="/img-inbox" style="display:inline-block; padding:10px 14px;
                                          border-radius:8px; background:#0f1d37; color:#fff;
                                          text-decoration:none; font-weight:bold;">
                이미지 수신함 열기 →
              </a>
              <a href="/img-map" style="display:inline-block; padding:10px 14px;
                                        border-radius:8px; background:#0f1d37; color:#fff;
                                        text-decoration:none; font-weight:bold;">
                지도표시 열기 →
              </a>
            </div>
          </div>
    
          
          
          
          
          <!-- ✅ 추가: 드론관제 카드 -->
          <div style="border-radius:12px; background:#1c2a45; padding:20px;">
            <h3 style="margin:0 0 6px; font-size:18px; display:flex; align-items:center; gap:10px;">
              <img src="/static/icons/gcs.png" alt="gcs 아이콘"
                   style="width:20px; height:20px; display:inline-block; border-radius:4px;">
              Ground Control System
            </h3>
            <p>드론의 실시간 위치·상태 모니터링 하며, 특정 지역을 정찰하거나 화재 지역을 촬영하는 등, 미리 정의된 임무를 드론에 전송</p>
            <a href="/feature1/" style="display:inline-block; margin-top:10px; padding:10px 14px;
                                       border-radius:8px; background:#0f1d37; color:#fff;
                                       text-decoration:none; font-weight:bold;">드론관제 열기 →</a>
          </div>
        
          <!-- ✅ 추가: 리포트 카드 -->
          <div style="border-radius:12px; background:#1c2a45; padding:20px;">
            <h3 style="margin:0 0 6px; font-size:18px; display:flex; align-items:center; gap:10px;">
              <img src="/static/icons/report.png" alt="report 아이콘"
                   style="width:20px; height:20px; display:inline-block; border-radius:4px;">
              Reporting
            </h3>
            <p>기간·장비별 통계를 리포트로 만들고 CSV/JSON으로 내보내 현장 공유·분석에 활용합니다.</p>
            <a href="/feature2" style="display:inline-block; margin-top:10px; padding:10px 14px;
                                       border-radius:8px; background:#0f1d37; color:#fff;
                                       text-decoration:none; font-weight:bold;">리포트 열기 →</a>
          </div>
                  
          
                  
          <div style="border-radius:12px; background:#1c2a45; padding:20px;">
            <!-- 3) Health 카드 제목 -->
            <h3 style="margin:0 0 6px; font-size:18px; display:flex; align-items:center; gap:10px;">
              <img src="/static/icons/hb.ico" alt="상태 아이콘"
                   style="width:20px; height:20px; display:inline-block; border-radius:4px;">
              Health State
            </h3>
            <p>API 응답으로 서버 상태를 반환합니다. 모니터링/프로브 구성으로 활용합니다.</p>
            <a href="/health" style="display:inline-block; margin-top:10px; padding:10px 14px;
                                     border-radius:8px; background:#0f1d37; color:#fff;
                                     text-decoration:none; font-weight:bold;">상태 보기 →</a>
          </div>
          
                          

            
          
          
          
          
          
          
        </section>

    </div>

    <footer>© 통합 관제 플랫폼 - 실시간 SNS 수신 · 대시보드 · 지도 시각화</footer>
  </div>

  <script>
    async function ping(){
      const dot = document.getElementById('dot'); const stx = document.getElementById('stxt');
      try{
        const r = await fetch('/health', {cache:'no-store'});
        if(!r.ok) throw new Error(r.status);
        await r.json();
        dot.classList.add('ok'); dot.classList.remove('err'); stx.textContent = '정상 동작 중';
      }catch(e){
        dot.classList.add('err'); dot.classList.remove('ok'); stx.textContent = '오프라인 또는 오류';
      }
    }
    ping(); setInterval(ping, 5000);
  </script>
</body>
</html>"""
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp


@app.get("/health")
def health():
    data = {"ok": True, "dash": "/dash/", "map": "/map"}
    accept = (request.headers.get("Accept") or "").lower()
    if "text/html" in accept:
        html = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>통합 관제 플랫폼  · 상태</title>
<style>
  :root{{ --bg:#16243f; --card:#1d2c4d; --accent:#66c2ff; --accent-2:#9dff9d;
         --text:#f1f6ff; --muted:#c3cde0; --border:rgba(255,255,255,.10);
         --shadow:0 10px 24px rgba(0,0,0,.30); --radius:16px; --vhpad:180px; }}
  *{{box-sizing:border-box}} html,body{{height:100%}}
  body{{
    margin:0; color:var(--text);
    font-family: system-ui, -apple-system, Segoe UI, Roboto, "Noto Sans KR", Arial, sans-serif;
    background:
      linear-gradient(180deg, rgba(255,255,255,.03), rgba(255,255,255,0)),
      radial-gradient(1200px 800px at 10% -10%, rgba(90,130,200,.30) 0%, transparent 60%),
      radial-gradient(900px 700px at 110% 20%, rgba(80,120,200,.25) 0%, transparent 55%),
      var(--bg);
  }}
  .wrap{{max-width:none; width:100%; margin:0; padding:24px 24px 56px;}}
  .topbar{{display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:16px;}}
  .brand{{display:flex; align-items:center; gap:12px; font-weight:800;}}
  .brand-badge{{width:38px; height:38px; border-radius:12px; background:linear-gradient(135deg, var(--accent), var(--accent-2)); box-shadow:var(--shadow); display:flex; align-items:center; justify-content:center; color:#00101a; font-size:20px; font-weight:900;}}
  .link{{color:var(--accent); text-decoration:none; font-weight:700}}
  .card{{border:1px solid var(--border); background:var(--card); border-radius:var(--radius); box-shadow:var(--shadow); padding:18px;}}
  #status-card{{min-height: calc(100vh - var(--vhpad)); display:flex; align-items:center; justify-content:center; text-align:center;}}
  .pill{{display:inline-flex; align-items:center; gap:8px; padding:8px 12px; border-radius:999px; background:#20355f; border:1px solid var(--border);}}
  .dot{{width:10px; height:10px; border-radius:50%; background:#3cff89; box-shadow:0 0 0 2px rgba(60,255,137,.12)}}
  table{{margin:16px auto 0; border-collapse:separate; border-spacing:0 8px; color:var(--muted);}}
  td:first-child{{opacity:.8; padding-right:12px; text-align:right}}
  td:last-child{{font-weight:700; color:var(--text)}}
</style>
</head>
<body>
  <div class="wrap">
    <div class="topbar">


        
    <div class="brand" style="display:flex; align-items:center; gap:10px; font-weight:800;">
      <div style="width:40px; height:40px; border-radius:50%; 
                  background:#202020; display:flex; align-items:center; justify-content:center;">
        <img src="/static/icons/instech_logo1.png" alt="드론 아이콘"
             style="width:40px; height:40px; display:block;">
      </div>
      <div style="font-size:22px;">통합 관제 플랫폼</div>
    </div>


      <div>
        <a class="link" href="/">홈</a><span style="opacity:.5"> · </span>
        <a class="link" href="/dash/">대시보드</a><span style="opacity:.5"> · </span>
        <a class="link" href="/map">지도</a>
      </div>
    </div>

    <div class="card" id="status-card">
      <div>
        <div class="pill"><span class="dot"></span> 정상 동작 중</div>
        <table>
          <tr><td>API</td><td>/health</td></tr>
          <tr><td>Dashboard</td><td><a class="link" href="/dash/">/dash</a></td></tr>
          <tr><td>Map</td><td><a class="link" href="/map">/map</a></td></tr>
        </table>
      </div>
    </div>

    <footer style="margin-top:8px; padding-top:8px; border-top:1px solid var(--border); color:var(--muted); font-size:13.5px; text-align:center;">
      © 통합 관제 플랫폼  · 실시간 SNS 수신 · 대시보드 · 지도 시각화
    </footer>
  </div>
</body>
</html>"""
        resp = make_response(html)
        resp.headers["Content-Type"] = "text/html; charset=utf-8"
        return resp
    return jsonify(data)


# ---- feature-1, feature-2


@app.get("/feature2")
def feature2_page():
    html = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>통합 관제 플랫폼 · 기능2</title>
<style>
  :root{
    --bg:#16243f; --card:#1d2c4d; --accent:#66c2ff; --accent-2:#9dff9d;
    --text:#f1f6ff; --muted:#c3cde0; --border:rgba(255,255,255,.10);
    --shadow:0 10px 24px rgba(0,0,0,.30); --radius:16px; --vhpad:180px;
  }
  *{box-sizing:border-box} html,body{height:100%}
  body{
    margin:0; color:var(--text);
    font-family: system-ui, -apple-system, Segoe UI, Roboto, "Noto Sans KR", Arial, sans-serif;
    background:
      linear-gradient(180deg, rgba(255,255,255,.03), rgba(255,255,255,0)),
      radial-gradient(1200px 800px at 10% -10%, rgba(90,130,200,.30) 0%, transparent 60%),
      radial-gradient(900px 700px at 110% 20%, rgba(80,120,200,.25) 0%, transparent 55%),
      var(--bg);
  }
  .wrap{max-width:none; width:100%; margin:0; padding:24px 24px 56px;}
  .topbar{display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:16px;}
  .link{color:var(--accent); text-decoration:none; font-weight:700}
  .card{border:1px solid var(--border); background:var(--card); border-radius:var(--radius); box-shadow:var(--shadow); padding:18px;}
  #feat-card{min-height: calc(100vh - var(--vhpad));}
  .btn{display:inline-block; padding:10px 14px; border-radius:10px; background:#20355f; border:1px solid var(--border); color:var(--text); text-decoration:none; font-weight:700;}
</style>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
      <div class="brand" style="display:flex; align-items:center; gap:10px; font-weight:800;">
        <div style="width:40px; height:40px; border-radius:50%; background:#202020; display:flex; align-items:center; justify-content:center;">
          <img src="/static/icons/instech_logo1.png" alt="드론 아이콘" style="width:40px; height:40px; display:block;">
        </div>
        <div style="font-size:22px;">통합 관제 플랫폼</div>
      </div>
      <div>
        <a class="link" href="/">홈</a><span style="opacity:.5"> · </span>
        <a class="link" href="/dash/">대시보드</a><span style="opacity:.5"> · </span>
        <a class="link" href="/map">지도</a><span style="opacity:.5"> · </span>
        <a class="link" href="/health">상태</a>
      </div>
    </div>

    <div class="card" id="feat-card">
      <h2 style="margin-top:0; display:flex; align-items:center; gap:10px;">
        <span style="font-size:24px;">📦</span> 기능2 · 리포트/데이터 내보내기
      </h2>
      <p style="color:var(--muted); margin-top:6px;">
        기간·토픽·장비 기준으로 집계한 통계를 리포트로 생성하고 CSV/JSON 파일로 다운로드합니다.
        팀 공유와 외부 분석 도구 연계를 위한 표준 포맷을 제공합니다.
      </p>

      <div style="display:grid; gap:12px; grid-template-columns:repeat(2,1fr); margin-top:16px;">
        <div style="background:#20355f; border:1px solid var(--border); border-radius:12px; padding:14px;">
          <h4 style="margin:0 0 6px;">리포트 템플릿</h4>
          <ul style="margin:0; padding-left:18px;">
            <li>일/주/월간 이벤트 요약</li>
            <li>장비별 알림 빈도/가중치</li>
            <li>지오영역(격자/격자열) 통계</li>
          </ul>
        </div>
        <div style="background:#20355f; border:1px solid var(--border); border-radius:12px; padding:14px;">
          <h4 style="margin:0 0 6px;">내보내기</h4>
          <ul style="margin:0; padding-left:18px;">
            <li>CSV / JSON 다운로드</li>
            <li>타 시스템 웹훅 전송</li>
            <li>이메일 첨부 공유</li>
          </ul>
        </div>
      </div>

      <div style="margin-top:16px; display:flex; gap:10px; flex-wrap:wrap;">
        <a class="btn" href="#">CSV 내보내기</a>
        <a class="btn" href="#">JSON 내보내기</a>
      </div>
    </div>

    <footer style="margin-top:8px; padding-top:8px; border-top:1px solid var(--border); color:var(--muted); font-size:13.5px; text-align:center;">
      © 통합 관제 플랫폼 · 실시간 SNS 수신 · 대시보드 · 지도 시각화
    </footer>
  </div>
</body>
</html>"""
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp












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
<title>통합 관제 플랫폼  · 지도</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" crossorigin=""/>
<style>
  :root{
    --bg:#16243f; --card:#1d2c4d; --accent:#66c2ff; --accent-2:#9dff9d;
    --text:#f1f6ff; --muted:#c3cde0; --border:rgba(255,255,255,.10);
    --shadow:0 10px 24px rgba(0,0,0,.30); --radius:16px; --vhpad:180px;
  }
  *{box-sizing:border-box} html,body{height:100%}
  body{
    margin:0; color:var(--text);
    font-family: system-ui, -apple-system, Segoe UI, Roboto, "Noto Sans KR", Arial, sans-serif;
    background:
      linear-gradient(180deg, rgba(255,255,255,.03), rgba(255,255,255,0)),
      radial-gradient(1200px 800px at 10% -10%, rgba(90,130,200,.30) 0%, transparent 60%),
      radial-gradient(900px 700px at 110% 20%, rgba(80,120,200,.25) 0%, transparent 55%),
      var(--bg);
  }
  .wrap{max-width:none; width:100%; margin:0; padding:24px 24px 56px;}
  .topbar{display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:16px;}
  .brand{display:flex; align-items:center; gap:12px; font-weight:800;}
  .link{color:var(--accent); text-decoration:none; font-weight:700}
  .card{border:1px solid var(--border); background:var(--card); border-radius:var(--radius); box-shadow:var(--shadow);}
  #controls{ padding:10px 12px; display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
  #map-card{ padding:0; min-height: calc(100vh - var(--vhpad)); display:flex; flex-direction:column; }
  #map{ width:100%; height:820px; flex:1 1 auto; border-top:1px solid var(--border); border-radius:0 0 var(--radius) var(--radius); }
  .badge{ display:inline-block; padding:2px 8px; border-radius:10px; background:#20355f; color:var(--text); }
  select, button{ background:#20355f; color:var(--text); border:1px solid var(--border); border-radius:8px; padding:6px 8px; }
</style>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
      <div class="brand" style="display:flex; align-items:center; gap:10px; font-weight:800;">
        <div style="width:40px; height:40px; border-radius:50%; background:#202020; display:flex; align-items:center; justify-content:center;">
          <img src="/static/icons/instech_logo1.png" alt="드론 아이콘" style="width:40px; height:40px; display:block;">
        </div>
        <div style="font-size:22px;">통합 관제 플랫폼</div>
      </div>
      <div>
        <a class="link" href="/">홈</a><span style="opacity:.5"> · </span>
        <a class="link" href="/dash/">대시보드</a><span style="opacity:.5"> · </span>
        <a class="link" href="/health">상태</a>
      </div>
    </div>

    <div class="card" id="map-card">
      <div id="controls">
        <span class="badge"> Geo Map</span>
        <label>Rows</label>
        <select id="rows"><option>50</option><option selected>200</option><option>500</option><option>1000</option></select>
        <label>Refresh(s)</label>
        <select id="refresh"><option>2</option><option selected>5</option><option>10</option><option>30</option></select>
        <button id="fit">Fit Bounds</button>
      </div>
      <div id="map"></div>
    </div>

    <footer style="margin-top:8px; padding-top:8px; border-top:1px solid var(--border); color:var(--muted); font-size:13.5px; text-align:center;">
      © 통합 관제 플랫폼  · 실시간 SNS 수신 · 대시보드 · 지도 시각화
    </footer>
  </div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" crossorigin=""></script>
  <script>
    // 1) 지도/레이어
    const map = L.map('map', { worldCopyJump: true }).setView([37.454717, 126.978020], 8);
    const sat = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      { maxZoom: 19, detectRetina: true, attribution: 'Imagery © Esri, Maxar, Earthstar Geographics, USDA, USGS, AeroGRID, IGN, and the GIS User Community' }).addTo(map);
    const streets = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
      { maxZoom: 19, detectRetina: true, attribution: '&copy; OpenStreetMap contributors' });
    const labels = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
      { maxZoom: 19, opacity: 0.8 }).addTo(map);
    L.control.layers({ 'Satellite (Esri)': sat, 'Streets (OSM)': streets }, { 'Labels': labels }).addTo(map);

    // 2) 🔥 커스텀 아이콘 (캐시 버스터로 ?v=3)
    const fireIcon = L.icon({
      iconUrl: '/static/icons/fire.png?v=3',
      iconSize: [32, 32],
      iconAnchor: [16, 32],
      popupAnchor: [0, -28]
    });

    // 3) 마커 레이어
    let markers = L.layerGroup().addTo(map);
    let _shouldFit = false;

    // 4) 그리기
    async function pullAndRender() {
      const rows = document.getElementById('rows').value || 200;
      try {
        await fetch('/api/geo/sync_from_notifications?limit=' + rows, { method: 'POST' });
        const resp = await fetch('/api/geo?limit=' + rows, { cache: 'no-store' });
        const payload = await resp.json();
        const data = payload.data || [];

        markers.clearLayers();
        const bounds = [];

        for (const p of data) {
          if (typeof p.lat !== 'number' || typeof p.lon !== 'number') continue;
          if (p.lat < -90 || p.lat > 90 || p.lon < -180 || p.lon > 180) continue;

          // ✅ 불 아이콘 적용
          const m = L.marker([p.lat, p.lon], { icon: fireIcon, zIndexOffset: 1000 });

          const dev = p.deviceId ? `Device: ${p.deviceId}<br/>` : '';
          const act = (p.activity !== undefined && p.activity !== null) ? `Activity: ${p.activity}<br/>` : '';
          const top = p.topic ? `Topic: ${p.topic}<br/>` : '';
          const ts  = p.ts ? `Time: ${p.ts}<br/>` : '';
          const msg = p.message ? String(p.message) : '';

          m.bindPopup(`${dev}${act}${top}${ts}<b>(${p.lat.toFixed(6)}, ${p.lon.toFixed(6)})</b><br/>${msg}`);
          markers.addLayer(m);
          bounds.push([p.lat, p.lon]);
        }

        if (bounds.length > 0 && _shouldFit) {
          map.fitBounds(L.latLngBounds(bounds).pad(0.2));
          _shouldFit = false;
        }
      } catch (e) {
        console.error(e);
      }
    }

    // 5) 컨트롤
    document.getElementById('fit').addEventListener('click', () => { _shouldFit = true; pullAndRender(); });
    function schedule(){
      const sec = Number(document.getElementById('refresh').value || 5);
      clearInterval(window.__timer);
      window.__timer = setInterval(pullAndRender, sec * 1000);
    }
    document.getElementById('refresh').addEventListener('change', schedule);
    document.getElementById('rows').addEventListener('change', pullAndRender);

    // 6) 초기 호출
    pullAndRender();
    schedule();
  </script>
</body>
</html>"""
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp





# ====================== SNS와  map 정보 연동  ======================

# ---- SNS 알림 -> 지도 좌표 동기화 (중복 방지)
SEEN_GEO_SET = "sns:geo:seen"  # Redis Set: 중복 방지 키 저장

def _dedup_key(ts, lat, lon, device_id):
    di = device_id or ""
    return f"{ts}|{lat:.6f}|{lon:.6f}|{di}"

@app.post("/api/geo/sync_from_notifications")
def api_geo_sync_from_notifications():
    try:
        limit = int(request.args.get("limit", "500"))
    except Exception:
        limit = 500

    added = 0
    seen = 0
    with _lock:
        # 최신부터 limit개만 검사
        src = list(_notifications)[:limit]
        for row in src:
            msg = row.get("message", {})
            if not isinstance(msg, dict):
                continue
            lat, lon = _extract_latlon(msg)
            if lat is None or lon is None:
                continue

            device_id = msg.get("DeviceID") or msg.get("deviceId") or msg.get("id")
            key = _dedup_key(row.get("ts"), lat, lon, device_id)

            # Redis SADD로 중복 방지
            try:
                if rds.sadd(SEEN_GEO_SET, key) == 0:
                    seen += 1
                    continue
            except Exception:
                # Redis가 없으면 통과(중복 가능)
                pass

            _geo_points.appendleft({
                "ts": row.get("ts"),
                "lat": lat,
                "lon": lon,
                "deviceId": device_id,
                "activity": msg.get("ActivityType") or msg.get("activity") or msg.get("type"),
                "message": msg.get("Message") or msg.get("message"),
                "topic": row.get("topic"),
            })
            added += 1

    return jsonify(ok=True, added=added, skipped_seen=seen, buffer=len(_geo_points))


# ==== (기존 import/전역 아래 아무곳) ====

@app.post("/api/notifications/clear")
def clear_notifications():
    """대시보드 표(_notifications)와 지도 버퍼(_geo_points) 비우기"""
    cleared = {"notifications": 0, "geo": 0}
    with _lock:
        _notifications.clear()
        _geo_points.clear()
        cleared["notifications"] = 0
        cleared["geo"] = 0
    # Redis 중복 방지 세트도 초기화(있으면)
    try:
        rds.delete(SEEN_GEO_SET)
    except Exception:
        pass
    return jsonify(ok=True, cleared=cleared)

# ===================== Dash 대시보드 (/dash/) =====================
from dash import Dash, dcc, html, Input, Output, dash_table

dash_app = Dash(
    __name__,
    server=app,
    url_base_pathname="/dash/",
    suppress_callback_exceptions=True,
    external_stylesheets=[dbc.themes.BOOTSTRAP],  # NEW: 스타일 적용
)

dash_app.layout = html.Div(
    style={"maxWidth": "1200px", "margin": "0 auto", "fontFamily": "system-ui, Arial"},
    children=[
        html.H5("SNS Webhook Dashboard", style={"marginTop": "10px"}),
        html.Div([
            dbc.InputGroup([
                html.Label("Rows"),
                dcc.Dropdown(
                    id="rows-dropdown",
                    options=[{"label": str(n), "value": n} for n in (50, 100, 200, 500)],
                    value=100, clearable=False, style={"width": "120px",  "display": "inline-block",
                                                       "margin-left":"10px",
                                                       #"background-color":"gray",
                                                       "color":'black'}
                ),

                html.Span("  |  ", style={"margin-left":"100px"}),
                html.Label("Auto Refresh (sec)", style={"margin-left":"20px"}),
                dcc.Dropdown(
                    id="refresh-sec",
                    options=[{"label": str(n), "value": n * 1000} for n in (2, 5, 10, 30)],
                    value=5000, clearable=False, style={"width": "120px", "display": "inline-block",
                                                        "margin-left":"10px",
                                                        #"background-color":"gray",
                                                        "color":'black'},
                    className="custom-dropdown"
                ),
                html.Span("  |  ", style={"margin-left":"100px"}),
                html.A("Map", href="/map", target="_blank", style={"color": "white","margin-left":"20px" }),

                html.Span("  |  ", style={"margin-left":"100px"}),
                html.A("Health", href="/health", target="_blank", style={"color": "white", "margin-left":"20px"}),

                # ⬇⬇⬇ 여기 추가
                html.Button(
                    "테이블 비우기",
                    id="btn-clear",
                    n_clicks=0,
                    style={
                        "margin-left": "50px",
                        "padding": "8px 14px",
                        "borderRadius": "10px",
                        "background": "#b71c1c",  # 진한 빨강
                        "color": "#fff",
                        "border": "1px solid #8e0000",
                        "fontWeight": "800"
                    }
                ),
                dcc.Store(id="clear-signal"),
                # ⬆⬆⬆ 추가 끝
            ]),




        ], style={"marginBottom": "8px"}),
        dcc.Interval(id="tick", interval=5000, n_intervals=0),
        dcc.Store(id="store"),
        dash_table.DataTable(
            id="table",
            columns=[
                {"name": "시간", "id": "ts"},
                {"name": "유형", "id": "type"},
                {"name": "이벤트", "id": "evt"},   # ✅ 새 컬럼 추가
                {"name": "토픽", "id": "topic"},
                {"name": "Subject", "id": "subject"},
                {"name": "메세지", "id": "message"},
            ],
            data=[],
            page_size=20,
            # ── 여기부터 스타일 조정 ─────────────────────────────
            style_cell={
                "textAlign": "left",
                "fontSize": "14px",
                "border": "1px solid #cccccc",
                "padding": "6px",
                "whiteSpace": "normal",
                "height": "auto",
            },
            style_header={
                "fontWeight": "bold",
                "backgroundColor": "#bfbfbf",  # 헤더는 조금 진한 회색
                "color": "#000000",  # 검은 글자
            },
            style_data={
                "backgroundColor": "#e0e0e0",  # 본문 회색
                "color": "#000000",  # 검은 글자
            },
            style_data_conditional=[
                {"if": {"row_index": "odd"}, "backgroundColor": "#d6d6d6"},  # 홀수 행 살짝 어둡게
            ],
            # ─────────────────────────────────────────────────────
            #style_cell={"textAlign": "left", "fontSize": "14px"},
            #style_header={"fontWeight": "bold"},
            #style_table={"overflowX": "auto"},
        ),
        html.Hr(),
        html.H5("Raw (selected)"),
        html.Div(id="raw-json", style={"whiteSpace": "pre-wrap", "fontFamily": "monospace", "fontSize": "13px"}),
    ]
)


# 페이지 프레임(배경/카드/높이)을 통일
dash_app.index_string = """
<!doctype html>
<html lang="ko">
<head>
  {%metas%}
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>통합 관제 플랫폼  · Dashboard</title>
  {%favicon%}
  {%css%}
  <style>
    :root{
      --bg:#16243f; --card:#1d2c4d; --accent:#66c2ff; --accent-2:#9dff9d;
      --text:#f1f6ff; --muted:#c3cde0; --border:rgba(255,255,255,.10);
      --shadow:0 10px 24px rgba(0,0,0,.30); --radius:16px; --vhpad:180px;
    }
    *{box-sizing:border-box} html,body{height:100%}
    body{
      margin:0; color:var(--text);
      font-family: system-ui, -apple-system, Segoe UI, Roboto, "Noto Sans KR", Arial, sans-serif;
      background:
        linear-gradient(180deg, rgba(255,255,255,.03), rgba(255,255,255,0)),
        radial-gradient(1200px 800px at 10% -10%, rgba(90,130,200,.30) 0%, transparent 60%),
        radial-gradient(900px 700px at 110% 20%, rgba(80,120,200,.25) 0%, transparent 55%),
        var(--bg);
    }
    .wrap{max-width:none; width:100%; margin:0; padding:24px 24px 56px;}
    .topbar{display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:16px;}
    .brand{display:flex; align-items:center; gap:12px; font-weight:800; letter-spacing:.2px;}
    .brand-badge{width:38px; height:38px; border-radius:12px; background:linear-gradient(135deg, var(--accent), var(--accent-2)); box-shadow:var(--shadow); display:flex; align-items:center; justify-content:center; color:#00101a; font-size:20px; font-weight:900;}
    .link{color:var(--accent); text-decoration:none; font-weight:700}
    .card{border:1px solid var(--border); background:var(--card); border-radius:var(--radius); box-shadow:var(--shadow); padding:18px;}
    #app-card{min-height: calc(100vh - var(--vhpad));}
    
    /* === Bootstrap 색상 덮어쓰기 === */
    body, .wrap, .card, .link, label,
    .dash-table-container .row,
    .dash-table-container .previous-next-container {
      color: var(--text) !important;
    }
    h1,h2,h3,h4,h5,h6 { color: var(--text) !important; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="topbar">

    
        <div class="brand" style="display:flex; align-items:center; gap:10px; font-weight:800;">
          <div style="width:40px; height:40px; border-radius:50%; 
                      background:#202020; display:flex; align-items:center; justify-content:center;">
            <img src="/static/icons/instech_logo1.png" alt="드론 아이콘"
                 style="width:40px; height:40px; display:block;">
          </div>
          <div style="font-size:22px;">통합 관제 플랫폼</div>
        </div>


      <div>
        <a class="link" href="/">홈</a><span style="opacity:.5"> · </span>
        <a class="link" href="/map">지도</a><span style="opacity:.5"> · </span>
        <a class="link" href="/health">상태</a>
      </div>
    </div>

    <div class="card" id="app-card">
      {%app_entry%}
    </div>

    <footer style="margin-top:8px; padding-top:8px; border-top:1px solid var(--border); color:var(--muted); font-size:13.5px; text-align:center;">
      © 통합 관제 플랫폼  · 실시간 SNS 수신 · 대시보드 · 지도 시각화
    </footer>
  </div>

  {%config%}
  {%scripts%}
  {%renderer%}
</body>
</html>
"""

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
            "event": row.get("event"),   # ✅ event 필드 매핑
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
    async function(n, rows, clearSignal){
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
    Input("clear-signal", "data"),   #// ⬅ 추가
)

dash_app.clientside_callback(
    """
    async function(n){
      if(!n){ return window.dash_clientside.no_update; }
      try{
        await fetch('/api/notifications/clear', { method: 'POST' });
      }catch(e){
        console.error('clear failed', e);
      }
      // 화면 갱신 트리거: clear-signal에 타임스탬프 기록
      return Date.now();
    }
    """,
    Output("clear-signal", "data"),
    Input("btn-clear", "n_clicks"),
)






# 2) 표에 바인딩
dash_app.clientside_callback(
    """
  function(data){
    console.log("📦 store data:", data);

    const out = [];
    (data || []).forEach((row, idx) => {
      console.log("➡️ row", idx, row);

      const msg = row && row.message;
      const isObj = msg && typeof msg === 'object';
      const msg_str = isObj ? JSON.stringify(msg) : String(msg || '');

      let event_val = (row && row.event != null) ? row.event : '';
      if (String(event_val).trim() === '' && isObj) {
        const cands = ["event","Event","eventType","detail-type","detailType","type","alarmName","Message","message","subject","Subject"];
        for (const k of cands) {
          const v = msg[k];
          if (v != null && String(v).trim() !== '') { 
            event_val = v; 
            console.log("✅ event_val from", k, "=", v);
            break;
          }
        }
      }
      if (String(event_val).trim() === '') {
        event_val = (row && row.subject) ? row.subject : '—';
        console.log("⚠️ fallback to subject:", event_val);
      }

      out.push({
        ts: row.ts || '',
        type: row.type || '',
        evt: String(event_val),         // ← event 가 아니라 evt 로!
        topic: row.topic || '',
        subject: row.subject || '',
        message: msg_str.slice(0, 2000),
        _raw: row.raw || null
      });
    });

    console.log("📊 out table data:", out);
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

# ====================== feature1 ======================
from dash import Dash, dcc, html  # (이미 import 되어 있으면 재import 불필요)

# === Feature1 전용 Dash 앱 ===
feature1_app = Dash(
    __name__ + "_feature1",
    server=app,
    url_base_pathname="/feature1/",            # <-- /feature1/ 로 서브앱 마운트
    suppress_callback_exceptions=True,
    external_stylesheets=[dbc.themes.BOOTSTRAP]
)

# (선택) 메인 대시보드와 동일한 프레임/테마를 쓰고 싶다면 index_string 재사용
feature1_app.index_string = """
<!doctype html>
<html lang="ko">
<head>
  {%metas%}
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>통합 관제 플랫폼 · 기능1</title>
  {%favicon%}
  {%css%}
  <style>
    :root{
      --bg:#16243f; --card:#1d2c4d; --accent:#66c2ff; --accent-2:#9dff9d;
      --text:#f1f6ff; --muted:#c3cde0; --border:rgba(255,255,255,.10);
      --shadow:0 10px 24px rgba(0,0,0,.30); --radius:16px; --vhpad:180px;
    }
    *{box-sizing:border-box} html,body{height:100%}
    body{
      margin:0; color:var(--text);
      font-family: system-ui, -apple-system, Segoe UI, Roboto, "Noto Sans KR", Arial, sans-serif;
      background:
        linear-gradient(180deg, rgba(255,255,255,.03), rgba(255,255,255,0)),
        radial-gradient(1200px 800px at 10% -10%, rgba(90,130,200,.30) 0%, transparent 60%),
        radial-gradient(900px 700px at 110% 20%, rgba(80,120,200,.25) 0%, transparent 55%),
        var(--bg);
    }
    .wrap{max-width:none; width:100%; margin:0; padding:24px 24px 56px;}
    .topbar{display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:16px;}
    .link{color:var(--accent); text-decoration:none; font-weight:700}
    .card{border:1px solid var(--border); background:var(--card); border-radius:var(--radius); box-shadow:var(--shadow); padding:18px;}
    #app-card{min-height: calc(100vh - var(--vhpad));}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
      <div class="brand" style="display:flex; align-items:center; gap:10px; font-weight:800;">
        <div style="width:40px; height:40px; border-radius:50%; background:#202020; display:flex; align-items:center; justify-content:center;">
          <img src="/static/icons/instech_logo1.png" alt="드론 아이콘" style="width:40px; height:40px; display:block;">
        </div>
        <div style="font-size:22px;">통합 관제 플랫폼</div>
      </div>
      <div>
        <a class="link" href="/">홈</a><span style="opacity:.5"> · </span>
        <a class="link" href="/dash/">대시보드</a><span style="opacity:.5"> · </span>
        <a class="link" href="/map">지도</a><span style="opacity:.5"> · </span>
        <a class="link" href="/health">상태</a>
      </div>
    </div>

    <div class="card" id="app-card">
      {%app_entry%}
    </div>

    <footer style="margin-top:8px; padding-top:8px; border-top:1px solid var(--border); color:var(--muted); font-size:13.5px; text-align:center;">
      © 통합 관제 플랫폼 · 실시간 SNS 수신 · 대시보드 · 지도 시각화
    </footer>
  </div>

  {%config%}
  {%scripts%}
  {%renderer%}
</body>
</html>
"""

# === 여기서부터 Dash 컴포넌트로 화면 구성 (기존 HTML을 Dash로 옮김) ===
"""
feature1_app.layout = html.Div(
    style={"maxWidth": "1200px", "margin": "0 auto", "fontFamily": "system-ui, Arial"},
    children=[
        html.H2(["🧠", " 기능1 · 알림 규칙/필터"], style={"marginTop": "0", "display": "flex", "gap": "10px", "alignItems": "center"}),
        html.P(
            "수신되는 SNS/웹훅 이벤트에 규칙을 적용해 중요한 경보만 선별합니다. "
            "기기·토픽·문자패턴(키워드/정규식)·수치 임계값·지오펜싱 등을 조합한 필터셋을 구성할 수 있습니다.",
            style={"color": "#c3cde0", "marginTop": "6px"}
        ),
        # 2열 카드
        html.Div(
            style={"display": "grid", "gap": "12px", "gridTemplateColumns": "repeat(2, 1fr)", "marginTop": "16px"},
            children=[
                html.Div(
                    style={"background": "#20355f", "border": "1px solid rgba(255,255,255,.10)", "borderRadius": "12px", "padding": "14px"},
                    children=[
                        html.H4("규칙 예시", style={"margin": "0 0 6px"}),
                        html.Ul([
                            html.Li("주요 토픽(산불/연막)만 통과"),
                            html.Li("온도 > 65℃ AND 연기센서=ON"),
                            html.Li("텍스트에 'flare|smoke' 포함"),
                        ], style={"margin": "0", "paddingLeft": "18px"})
                    ]
                ),
                html.Div(
                    style={"background": "#20355f", "border": "1px solid rgba(255,255,255,.10)", "borderRadius": "12px", "padding": "14px"},
                    children=[
                        html.H4("출력/액션", style={"margin": "0 0 6px"}),
                        html.Ul([
                            html.Li("대시보드 강조표시"),
                            html.Li("웹훅/슬랙/이메일 알림"),
                            html.Li("지도 마커 강조/자동 맞춤"),
                        ], style={"margin": "0", "paddingLeft": "18px"})
                    ]
                ),
            ]
        ),
        # (필요 시) 이곳에 dcc.Store/dcc.Input/테이블/콜백 등 추가 가능
    ]
)
"""
#"""
MP_URL  = os.getenv("MP_URL",  "https://127.0.0.1:10000/index.html")
QGC_URL = os.getenv("QGC_URL", "https://127.0.0.1:10100/index.html")

# 👉 여기만 바꾸면 됨
MP_URL  = "/mp"
QGC_URL = "/qgc"
#"""
# 변경
MP_URL  = os.getenv("MP_URL",  "https://172.30.1.60:10000/index.html")
QGC_URL = os.getenv("QGC_URL", "https://172.30.1.60:10100/index.html")  # 필요하면 실제 URL로 교체


from urllib.parse import urlparse

def origin_of(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.hostname}{(':'+str(p.port)) if p.port else ''}"

MP_ORIGIN  = origin_of(MP_URL)
QGC_ORIGIN = origin_of(QGC_URL)


#"""
@app.after_request
def add_csp(resp: Response):
    if request.path.startswith("/map") or request.path.startswith("/img-map"):
        """
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self' blob: data:; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://unpkg.com https://cesium.com; "
            "style-src  'self' 'unsafe-inline' https://unpkg.com https://cesium.com; "
            "img-src    'self' data: blob: https://tile.openstreetmap.org "
            "https://a.tile.openstreetmap.org https://b.tile.openstreetmap.org https://c.tile.openstreetmap.org "
            "https://server.arcgisonline.com https://api.cesium.com https://assets.cesium.com; "
            "connect-src 'self' blob: https://unpkg.com https://ion.cesium.com https://api.cesium.com https://assets.cesium.com; "
            "worker-src 'self' blob: https://unpkg.com; "
            "child-src  'self' blob: https://unpkg.com; "  # 일부 브라우저용 폴백
            "font-src  'self' data: https://unpkg.com; "
            "object-src 'none'; frame-ancestors 'self';"
        )
        """
        """
        directives = [
            "default-src 'self' blob: data:",
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://unpkg.com https://cesium.com https://assets.cesium.com",
            "style-src 'self' 'unsafe-inline' https://unpkg.com https://cesium.com https://assets.cesium.com",
            # 이미지: OSM 타일 + ArcGIS + Cesium
            "img-src 'self' data: blob: https://tile.openstreetmap.org https://a.tile.openstreetmap.org "
            "https://b.tile.openstreetmap.org https://c.tile.openstreetmap.org https://server.arcgisonline.com "
            "https://api.cesium.com https://assets.cesium.com",
            # 네트워크: Cesium Ion/Assets, ArcGIS, unpkg
            "connect-src 'self' blob: data: https://unpkg.com https://ion.cesium.com https://api.cesium.com "
            "https://assets.cesium.com https://server.arcgisonline.com",
            "font-src 'self' data: https://unpkg.com https://assets.cesium.com",
            "worker-src 'self' blob: data:",
            "child-src 'self' blob: data:",
            "object-src 'none'",
            "frame-ancestors 'self'",
        ]
        """
        directives = [
        # 기본
                  "default-src 'self' blob: data:",
        # 스크립트/스타일
                  "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://maps.googleapis.com https://maps.gstatic.com https://unpkg.com",
                  "style-src  'self' 'unsafe-inline' https://maps.gstatic.com https://fonts.googleapis.com https://unpkg.com",
        # 이미지(한 줄)
                  "img-src 'self' data: blob: https://maps.googleapis.com https://maps.gstatic.com https://lh3.googleusercontent.com https://unpkg.com https://tile.openstreetmap.org https://a.tile.openstreetmap.org https://b.tile.openstreetmap.org https://c.tile.openstreetmap.org https://*.arcgisonline.com https://server.arcgisonline.com https://services.arcgisonline.com",
        # 네트워크(한 줄)
                  "connect-src 'self' blob: data: https://maps.googleapis.com https://maps.gstatic.com https://unpkg.com https://tile.openstreetmap.org https://*.arcgisonline.com https://server.arcgisonline.com https://services.arcgisonline.com",
        # 폰트/워커/차일드
                  "font-src 'self' data: https://maps.gstatic.com https://fonts.gstatic.com https://unpkg.com",
                  "worker-src 'self' blob: data:",
                  "child-src  'self' blob: data:",
                  "object-src 'none'",
                  "frame-ancestors 'self'",
        ]

        # 한 줄 문자열로 보장 + 방어적 개행 제거
        csp = '; '.join(directives).replace('\n', ' ').replace('\r', ' ')
        resp.headers['Content-Security-Policy'] = csp

    else:
        # 임베드/네트워크를 자기 자신과 Xpra origin들로 제한
        csp = (
            "default-src 'self'; "
            f"frame-src 'self' {MP_ORIGIN} {QGC_ORIGIN}; "
            f"connect-src 'self' {MP_ORIGIN} {QGC_ORIGIN}; "
            f"img-src 'self' data: {MP_ORIGIN} {QGC_ORIGIN}; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; "
            "base-uri 'self'; object-src 'none'; "
            "frame-ancestors 'self';"
        )
        resp.headers["Content-Security-Policy"] = csp
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    return resp
#"""





feature1_app.layout = html.Div([
    html.H3("GCS 화면 통합 ", style={"color": "white"}),
    dcc.Tabs([
        dcc.Tab(
            label="Mission Planner",
            children=[
                html.Iframe(
                    src=MP_URL,
                    style={
                        "border": "none", "width": "100%", "height": "75vh",
                        "color": "black", "backgroundColor": "#bfbfbf"
                    }
                )
            ],
            style={  # 기본 탭 스타일
                "backgroundColor": "#1d2c4d",   # 평상시 배경
                "color": "white",              # 평상시 글자
                "padding": "10px",
                "fontWeight": "bold"
            },
            selected_style={  # 선택된 탭 스타일
                "backgroundColor": "#66c2ff",  # 선택 배경
                "color": "black",             # 선택 글자
                "padding": "10px",
                "fontWeight": "bold"
            }
        ),
        dcc.Tab(
            label="QGroundControl",
            children=[
                html.Iframe(
                    src=QGC_URL,
                    style={
                        "border": "none", "width": "100%", "height": "75vh",
                        "color": "black", "backgroundColor": "#bfbfbf"
                    }
                )
            ],
            style={
                "backgroundColor": "#1d2c4d",
                "color": "white",
                "padding": "10px",
                "fontWeight": "bold"
            },
            selected_style={
                "backgroundColor": "#66c2ff",
                "color": "black",
                "padding": "10px",
                "fontWeight": "bold"
            }
        ),
    ])
])




# t24.py (다른 Dash 앱들 근처에 추가)
from dash import Dash, dcc, html, Input, Output, dash_table

img_inbox_app = Dash(
    __name__ + "_imginbox",
    server=app,
    url_base_pathname="/img-inbox/",           # ✅ /img-inbox/ 로 마운트
    suppress_callback_exceptions=True,
    external_stylesheets=[dbc.themes.BOOTSTRAP],
)

# /dash 와 같은 프레임 재사용(원하시면 그대로 복붙)
img_inbox_app.index_string = dash_app.index_string

img_inbox_app.layout = html.Div(
    style={"maxWidth": "1200px", "margin": "0 auto", "fontFamily": "system-ui, Arial"},
    children=[
        html.H5("Image Inbox (Webhook / Upload)", style={"marginTop": "10px"}),


        html.Div([
            html.Div([
                dbc.InputGroup([
                    html.Label("Rows"),
                    dcc.Dropdown(
                        id="img-rows",
                        options=[{"label": str(n), "value": n} for n in (50, 100, 200, 500)],
                        value=100, clearable=False,
                        style={"width": "120px", "display": "inline-block", "margin-left": "10px", "color": "black"}
                    ),
                    html.Span("  |  ", style={"margin-left": "100px"}),
                    html.Label("Auto Refresh (sec)", style={"margin-left": "20px"}),
                    dcc.Dropdown(
                        id="img-refresh",
                        options=[{"label": str(n), "value": n * 1000} for n in (2, 5, 10, 30)],
                        value=5000, clearable=False,
                        style={"width": "120px", "display": "inline-block", "margin-left": "10px", "color": "black"}
                    ),
                    html.Span("  |  ", style={"margin-left": "100px"}),
                    html.A("Minimal Uploader", href="/img-inbox/minimal", target="_blank",
                           style={"color": "white", "margin-left": "20px"}),
                ]),
            ], style={"flex": "1 1 auto"}),

            html.Div([
                html.Button(
                    "모두 지우기",
                    id="img-btn-clear",
                    n_clicks=0,
                    style={
                        "padding": "8px 14px",
                        "borderRadius": "10px",
                        "background": "#b71c1c",
                        "color": "#fff",
                        "border": "1px solid #8e0000",
                        "fontWeight": "800",
                    }
                ),
                dcc.Store(id="img-clear-signal"),
            ], style={"marginLeft": "auto"}),
        ], style={"display": "flex", "alignItems": "center", "gap": "12px", "marginBottom": "8px"}),





        dcc.Interval(id="img-tick", interval=5000, n_intervals=0),
        dcc.Store(id="img-store"),

        dash_table.DataTable(
            id="img-table",
            columns=[
                {"name": "시간",       "id": "ts"},
                {"name": "소스",       "id": "src"},
                {"name": "파일명",     "id": "name"},
                {"name": "위도",       "id": "lat"},
                {"name": "경도",       "id": "lon"},
                {"name": "비고",       "id": "note"},
                {"name": "미리보기",   "id": "preview", "presentation": "markdown"},
            ],
            data=[],
            page_size=20,
            style_cell={
                "textAlign": "left", "fontSize": "14px", "border": "1px solid #cccccc",
                "padding": "6px", "whiteSpace": "normal", "height": "auto",
            },
            style_header={"fontWeight": "bold", "backgroundColor": "#bfbfbf", "color": "#000"},
            style_data={"backgroundColor": "#e0e0e0", "color": "#000"},
            style_data_conditional=[{"if": {"row_index": "odd"}, "backgroundColor": "#d6d6d6"}],
        ),
    ]
)

# 1) 데이터 가져오기: /img/api/inbox 에서 읽음
img_inbox_app.clientside_callback(
    """
    async function(n, rows){
      const limit = rows || 100;
      try{
        const resp = await fetch('/img/api/inbox?limit=' + limit, {cache:'no-store'});
        const j = await resp.json();
        return j.data || [];
      }catch(e){
        console.error('fetch /img/api/inbox failed', e);
        return [];
      }
    }
    """,
    Output("img-store", "data"),
    Input("img-tick", "n_intervals"),
    Input("img-rows", "value"),
    Input("img-clear-signal", "data"),
)

img_inbox_app.clientside_callback(
    """
    async function(n){
      if(!n){ return window.dash_clientside.no_update; }
      try{
        await fetch('/img/api/inbox/clear', { method: 'POST' });
      }catch(e){
        console.error('img inbox clear failed', e);
      }
      return Date.now(); // 데이터 리로드 트리거
    }
    """,
    Output("img-clear-signal", "data"),
    Input("img-btn-clear", "n_clicks"),
)










# 2) 표 바인딩 + 미리보기 markdown 생성
@img_inbox_app.callback(
    Output("img-table", "data"),
    Input("img-store", "data"),
)
def _img_feed_table(data):
    out = []
    for row in (data or []):
        # 미리보기 링크(마크다운)
        url = row.get("url") or ""
        preview = f"[열기]({url})" if url else ""
        out.append({
            "ts": row.get("ts"),
            "src": row.get("src"),
            "name": row.get("name"),
            "lat": row.get("lat"),
            "lon": row.get("lon"),
            "note": row.get("note") or "",
            "preview": preview,
        })
    return out

# ===================== Entrypoint =====================
if __name__ == "__main__":
    # 로컬 자체서명 TLS라면 curl 테스트에 -k 필요
    ssl_cert = "./ssl/fullchain.pem"
    ssl_key = "./ssl/privkey.pem"
    app.run(host="0.0.0.0", port=8443, ssl_context=(ssl_cert, ssl_key))
