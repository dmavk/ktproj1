

# imgp.py
from flask import Blueprint, make_response

# 1) 블루프린트 생성 (url_prefix는 필요 없으면 생략)
imgbp = Blueprint("imgbp", __name__)



@imgbp.get("/img-inbox")
def img_inbox():
    html = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>이미지 이벤트 수신함</title>
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
</style>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
      <div class="brand" style="display:flex; align-items:center; gap:10px; font-weight:800;">
        <div style="width:40px; height:40px; border-radius:50%; background:#202020; display:flex; align-items:center; justify-content:center;">
          <img src="/static/icons/KT_logo1.png" alt="로고" style="width:40px; height:40px; display:block;">
        </div>
        <div style="font-size:22px;">통합 관제 플랫폼</div>
      </div>
      <div>
        <a class="link" href="/">홈</a><span style="opacity:.5"> · </span>
        <a class="link" href="/img-map">지도표시</a><span style="opacity:.5"> · </span>
        <a class="link" href="/dash/">대시보드</a>
      </div>
    </div>

    <div class="card">
      <h2 style="margin:0 0 10px;">📥 이미지 이벤트 수신함</h2>
      <p style="color:var(--muted); margin:0 0 12px;">
        외부에서 전송한 이미지 목록을 보여주는 자리입니다. (데모) 아직 수신 항목이 없습니다.
      </p>
      <ul style="margin:0; padding-left:18px; color:var(--muted);">
        <li>Webhook/S3 수신 구현 시 여기서 썸네일과 메타데이터(시간, 장비, 위치)를 나열합니다.</li>
        <li>EXIF에 좌표가 있는 경우 “지도에서 보기” 버튼으로 /img-map 에 표시합니다.</li>
      </ul>
    </div>

    <footer style="margin-top:8px; padding-top:8px; border-top:1px solid var(--border); color:var(--muted); font-size:13.5px; text-align:center;">
      © 통합 관제 플랫폼 · 이미지 이벤트 수신
    </footer>
  </div>
</body>
</html>"""
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp
# =========================================================================================

# imgp.py
import os, uuid, json, time
from collections import deque
from flask import Blueprint, request, jsonify, send_from_directory, make_response

# 업로드 디렉터리
UPLOAD_DIR = os.path.abspath("./uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 이미지 이벤트 버퍼
IMG_MAX = int(os.getenv("IMG_UI_BUFFER", "1000"))
IMG_INBOX = deque(maxlen=IMG_MAX)

imgbp = Blueprint("imgbp", __name__)

# (옵션) 아주 간단한 업로드 폼(테스트용)
@imgbp.get("/img-inbox/minimal")
def img_inbox_minimal():
    html = """<!doctype html><meta charset="utf-8">
<h3>이미지 수신함(최소)</h3>
<form action="/img/upload" method="post" enctype="multipart/form-data">
  <input type="file" name="file" required>
  <input type="text" name="ts" placeholder="timestamp (optional)">
  <input type="text" name="src" placeholder="source (optional)">
  <input type="text" name="lat" placeholder="lat (optional)">
  <input type="text" name="lon" placeholder="lon (optional)">
  <input type="text" name="note" placeholder="note (optional)">
  <button>업로드</button>
</form>
<p><a href="/img-inbox/">Dash 테이블 보기로 이동</a></p>
"""
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp

# 이미지 업로드(외부 시스템에서 호출 가능)
@imgbp.post("/img/upload")
def img_upload():
    print('nnnnnnnnnnnnnnnnnnnnnnn')
    f = request.files.get("file")
    if not f:
        return jsonify(ok=False, error="no file"), 400

    # 파일 저장
    ext = (os.path.splitext(f.filename or "")[1] or ".jpg").lower()
    fid = uuid.uuid4().hex
    fname = f"{fid}{ext}"
    path = os.path.join(UPLOAD_DIR, fname)
    f.save(path)

    # 메타
    ts  = request.form.get("ts") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    src = request.form.get("src") or "upload"
    lat = request.form.get("lat")
    lon = request.form.get("lon")
    note = request.form.get("note") or ""

    # 숫자 변환(실패시 None)
    def _f(v):
        try:
            if v is None or v == "": return None
            return float(v)
        except Exception:
            return None

    rec = {
        "id": fid,
        "ts": ts,
        "src": src,
        "name": f.filename,
        "file": fname,
        "lat": _f(lat),
        "lon": _f(lon),
        "note": note,
        # 미리보기/다운로드 URL
        "url": f"/img/raw/{fname}",
        "thumb": f"/img/raw/{fname}",  # 최소 구현: 원본을 그대로 씀
    }
    IMG_INBOX.appendleft(rec)
    return jsonify(ok=True, id=fid, record=rec)

# 리스트 API (/img-inbox Dash가 읽어감)
@imgbp.get("/img/api/inbox")
def img_api_inbox():
    try:
        limit = int(request.args.get("limit", "100"))
    except Exception:
        limit = 100
    data = list(IMG_INBOX)[:limit]
    return jsonify(data=data, count=len(data))

# 원본/썸네일 서빙(최소 구현)
@imgbp.get("/img/raw/<path:filename>")
def img_raw(filename):
    return send_from_directory(UPLOAD_DIR, filename, as_attachment=False)



# =========================================================================================
# =========================================================================================
"""
CESIUM_VER     = os.getenv("CESIUM_VER", "1.113")
VWORLD_API_KEY = os.getenv("VWORLD_API_KEY", "4771156FE49F31389913312EBB80BCDB")
CESIUM_ION     = os.getenv("CESIUM_ION_TOKEN", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJqdGkiOiIyYzQ5Y2JkYS05Zjk5LTRhMzMtYWFiMS05ZGIzYTA5OGFhZTQiLCJpZCI6MzQyNjc2LCJpYXQiOjE3NTgyMzg2MDB9.ilgb8Z8rsesnBolJK7gA1XPSVOjRVbDE3QaWGJIBnYM")

"""
@imgbp.get("/img-map")
def img_map_page():
    ION = os.getenv("CESIUM_ION_TOKEN",
                           "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJqdGkiOiIyYzQ5Y2JkYS05Zjk5LTRhMzMtYWFiMS05ZGIzYTA5OGFhZTQiLCJpZCI6MzQyNjc2LCJpYXQiOjE3NTgyMzg2MDB9.ilgb8Z8rsesnBolJK7gA1XPSVOjRVbDE3QaWGJIBnYM")

    mode = (request.args.get("mode") or "ion").lower()  # ion | osm | plain
    html = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>3D 이미지 지도</title>
<link rel="stylesheet" href="https://unpkg.com/cesium/Build/Cesium/Widgets/widgets.css">
<style>
  html,body{{height:100%;margin:0;background:#000;color:#cfe8ff;font-family:system-ui,Arial}}
  .top{{padding:8px 12px;position:fixed;left:0;right:0;top:0;z-index:10;background:rgba(0,0,0,.35)}}
  .top a{{color:#6cf;margin-right:12px}}
  #viewer{{position:absolute;left:0;right:0;top:40px;bottom:0}}
  .warn{{color:#ff7373;margin-left:8px}}
</style>
</head>
<body>
  <div class="top">
    <b>3D 이미지 지도</b>
    · <a href="/img-map?mode=ion">ion</a>
    <a href="/img-map?mode=osm">osm</a>
    <a href="/img-map?mode=plain">plain</a>
    <span>mode=<b>{mode}</b></span>
    {"<span class='warn'>Ion 토큰 없음</span>" if (mode=="ion" and not ION) else ""}
  </div>
  <div id="viewer"></div>

  <!-- ✅ CDN 워커 경로 지정: Cesium.js 로딩 '이전'에 -->
  <script>window.CESIUM_BASE_URL="https://unpkg.com/cesium/Build/Cesium/";</script>
  <script src="https://unpkg.com/cesium/Build/Cesium/Cesium.js"></script>
  <script>
    try {{
      console.log("CesiumJS", Cesium.VERSION);
      if (!window.WebGL2RenderingContext) {{
        console.error("WebGL2 미지원/비활성");
      }}

      // ✅ Ion 토큰
      Cesium.Ion.defaultAccessToken = "{ION}";

      let imageryProvider=null, terrainProvider=null;
      if ("{mode}" === "ion") {{
        imageryProvider = new Cesium.IonImageryProvider({{ assetId: 2 }}); // Bing Aerial (Ion)
        terrainProvider = Cesium.createWorldTerrain();
      }} else if ("{mode}" === "osm") {{
        imageryProvider = new Cesium.UrlTemplateImageryProvider({{
          url: "https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png",
          credit: "© OpenStreetMap contributors"
        }});
        terrainProvider = null; // 먼저 타일만 확인
      }} else {{
        // plain: 기본 베이스레이어(블루마블) + 지형 없음 — 워커/CSP만 확인
        imageryProvider = undefined;
        terrainProvider = null;
      }}

      const viewer = new Cesium.Viewer("viewer", {{
        imageryProvider,
        terrainProvider,
        baseLayerPicker:false, geocoder:false, timeline:false, animation:false
      }});
      window.viewer = viewer;

      // 인박스 위치 찍기
      async function plot(){{
        const r = await fetch("/img/api/inbox?limit=500", {{cache:"no-store"}});
        const j = await r.json(); const rows = j.data || [];
        viewer.entities.removeAll();
        const pts = [];
        rows.forEach(row => {{
          const lat = Number(row.lat), lon = Number(row.lon);
          if (!isFinite(lat) || !isFinite(lon)) return;
          const p = Cesium.Cartesian3.fromDegrees(lon, lat);
          pts.push(p);
          viewer.entities.add({{
            position: p,
            point: {{ pixelSize: 8, color: Cesium.Color.YELLOW }},
            label: {{
              text: (row.name||"image"),
              font: "12px sans-serif",
              fillColor: Cesium.Color.WHITE,
              outlineColor: Cesium.Color.BLACK,
              outlineWidth: 2,
              pixelOffset: new Cesium.Cartesian2(0,-14)
            }}
          }});
        }});
        if (pts.length) {{
          viewer.camera.flyToBoundingSphere(Cesium.BoundingSphere.fromPoints(pts), {{duration:1}});
        }}
      }}
      plot(); setInterval(plot, 5000);

    }} catch(e) {{
      console.error("init failed:", e);
    }}
  </script>
</body>
</html>"""
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp


# =========================================================================================