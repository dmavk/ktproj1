


# =================================================================
# ==== t24.py 맨 위 import 근처 ====
import os, time, uuid
from collections import deque
from flask import Blueprint, request, jsonify, make_response
from werkzeug.utils import secure_filename
from PIL import Image, ExifTags

# ==== 업로드 블루프린트 ====
imgsvc = Blueprint("imgsvc", __name__)

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
print("UPLOAD_DIR = ", UPLOAD_DIR)
IMG_INBOX = deque(maxlen=2000)   # 최근 업로드 메타 저장 (테이블용)

def _exif_to_latlon(img: Image.Image):
    try:
        exif = img._getexif() or {}
        if not exif:
            return (None, None)
        # 태그 이름으로 매핑
        exif = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
        gps = exif.get("GPSInfo")
        if not gps:
            return (None, None)

        def _rat_to_deg(rat):
            # (num, den) or PIL Rational -> float
            return float(rat[0]) / float(rat[1])

        def _dms_to_deg(dms):
            d, m, s = dms
            return _rat_to_deg(d) + _rat_to_deg(m)/60.0 + _rat_to_deg(s)/3600.0

        lat = _dms_to_deg(gps[2]); lon = _dms_to_deg(gps[4])
        if gps.get(1, 'N') == 'S':
            lat = -lat
        if gps.get(3, 'E') == 'W':
            lon = -lon
        return (lat, lon)
    except Exception:
        return (None, None)




@imgsvc.post("/img/api/inbox/clear")
def img_inbox_clear():
    """
    Image Inbox 테이블(인메모리 큐) 비우고,
    업로드 폴더(static/uploads)의 이미지 파일도 모두 삭제.
    """
    try:
        # 1) 인메모리 큐 비우기
        IMG_INBOX.clear()

        # 2) 업로드 폴더 내 이미지 파일 삭제
        exts = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif")
        removed = 0
        failed = []

        # 안전장치: 경로가 실제 디렉토리인지 확인
        if os.path.isdir(UPLOAD_DIR):
            for name in os.listdir(UPLOAD_DIR):
                path = os.path.join(UPLOAD_DIR, name)
                # 파일만, 그리고 지정 확장자만 제거
                if os.path.isfile(path) and name.lower().endswith(exts):
                    try:
                        os.remove(path)
                        removed += 1
                    except Exception as e:
                        failed.append({"file": name, "error": str(e)})

        return jsonify(ok=True, cleared=True, removed_files=removed, failed=failed, count=0)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500




@imgsvc.post("/img/upload")
def img_upload():
    print("upload image -------------------------")
    f = request.files.get("file")
    if not f:
        return jsonify(ok=False, error="missing file"), 400

    note = request.form.get("note") or ""
    # 폼 lat/lon (없으면 None)
    lat = request.form.get("lat", type=float)
    lon = request.form.get("lon", type=float)

    # 파일 저장
    name = secure_filename(f.filename or f"img_{uuid.uuid4().hex}.jpg")
    ts = int(time.time())
    # 파일명에 타임스탬프 접두 붙여 충돌방지
    save_name = f"{ts}_{name}"
    save_path = os.path.join(UPLOAD_DIR, save_name)
    f.save(save_path)

    # EXIF에서 좌표 시도 추출 (폼값보다 우선)
    try:
        with Image.open(save_path) as im:
            e_lat, e_lon = _exif_to_latlon(im)
            if e_lat is not None and e_lon is not None:
                lat, lon = e_lat, e_lon
    except Exception:
        pass

    # URL 구성
    url = f"/static/uploads/{save_name}"

    # 인메모리 박스에 기록
    IMG_INBOX.appendleft({
        "ts": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)),
        "src": "upload",
        "name": save_name,
        "lat": lat,
        "lon": lon,
        "note": note,
        "url": url,
    })

    return jsonify(ok=True, url=url, lat=lat, lon=lon, name=save_name)

@imgsvc.get("/img/api/inbox")
def img_inbox_list():
    limit = request.args.get("limit", type=int) or 100
    data = list(IMG_INBOX)[:limit]
    return jsonify(ok=True, count=len(data), data=data)

@imgsvc.get("/img-inbox/minimal")
def img_inbox_minimal():
    html = """<!doctype html>
<html><head><meta charset="utf-8"/><title>Minimal Uploader</title></head>
<body style="font-family: system-ui">
  <h3>이미지 업로드 테스트</h3>
  <form id="f" enctype="multipart/form-data" method="post" action="/img/upload">
    <input type="file" name="file" required><br><br>
    lat: <input name="lat" placeholder="optional"> 
    lon: <input name="lon" placeholder="optional"><br><br>
    note: <input name="note" placeholder="메모"><br><br>
    <button>업로드</button>
  </form>
  <hr>
  <button onclick="load()">목록 불러오기</button>
  <pre id="out"></pre>
<script>
async function load(){
  const r = await fetch('/img/api/inbox?limit=20',{cache:'no-store'});
  document.getElementById('out').textContent = JSON.stringify(await r.json(), null, 2);
}
</script>
</body></html>"""
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp

# =================================================================
