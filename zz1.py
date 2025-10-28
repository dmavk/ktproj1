

# ================================
# vwmap.py — Google Maps + deck.gl + 이미지 이벤트 연동 버전
# ================================

import os, json
from flask import Flask, jsonify, make_response
from dash import Dash, html

# === Flask + Dash 통합 ===
app = Flask(__name__)
dash_app = Dash(__name__, server=app, url_base_pathname="/")

# === Google Maps API 키 ===
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY",
    "AIzaSyDNUWxSp_t5VrXMFD5UrH9QaqLm7cGrecg")  # ⚠️ 실제 키 입력

# === 샘플용 이미지 이벤트 데이터 (보통은 DB/Redis에서 로드됨) ===
IMG_EVENTS = [
    {"lat": 37.5665, "lon": 126.9780, "note": "서울 시청", "url": "/static/img/sample1.jpg"},
    {"lat": 37.5650, "lon": 126.9820, "note": "Drone A - Test", "url": "/static/img/sample2.jpg"},
    {"lat": 37.5680, "lon": 126.9750, "note": "Drone B - Alert", "url": "/static/img/sample3.jpg"},
]

# === Dash Layout ===
dash_app.layout = html.Div(
    [
        html.H3("🛰️ DroneEngage 3D Map Viewer (deck.gl + Google Maps)",
                style={"color": "#cfe8ff", "padding": "10px"}),
        html.Iframe(
            src="/map3d",
            style={
                "width": "100%",
                "height": "90vh",
                "border": "0",
                "backgroundColor": "#000"
            }
        )
    ],
    style={"background": "#0d1117", "height": "100vh", "margin": 0, "padding": 0}
)

# === REST API: /img-inbox → JSON 데이터 반환 ===
@app.route("/img-inbox")
def img_inbox():
    """이미지 업로드 목록 (좌표 + 설명)"""
    return jsonify(IMG_EVENTS)

# === /map3d: Google Maps + deck.gl + 이미지 이벤트 표시 ===
@app.route("/map3d")
def map3d():
    html_doc = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<title>deck.gl + Google Maps (Image Event)</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />

<!-- Google Maps & deck.gl -->
<script src="https://maps.googleapis.com/maps/api/js?key={GOOGLE_MAPS_API_KEY}"></script>
<script src="https://unpkg.com/deck.gl@8.9.35/dist.min.js"></script>

<style>
html,body,#map {{
  margin: 0; padding: 0;
  width: 100%; height: 100%;
  background: #000;
  overflow: hidden;
}}
#tooltip {{
  position: absolute;
  z-index: 1000;
  background: rgba(0,0,0,0.75);
  color: #fff;
  padding: 5px 8px;
  border-radius: 4px;
  font-size: 12px;
  display: none;
  pointer-events: none;
}}
#refresh {{
  position: fixed;
  top: 12px; right: 12px;
  z-index: 999;
  padding: 6px 10px;
  border-radius: 6px;
  background: rgba(0,0,0,0.6);
  color: #fff;
  border: 1px solid #444;
  cursor: pointer;
}}
</style>
</head>
<body>
<div id="map"></div>
<div id="tooltip"></div>
<button id="refresh">🔄 새로고침</button>

<script>
const {{ GoogleMapsOverlay, ScatterplotLayer, IconLayer }} = deck;
let overlay;
const tooltip = document.getElementById("tooltip");

// === 지도 초기화 ===
const map = new google.maps.Map(document.getElementById('map'), {{
  center: {{ lat: 37.5665, lng: 126.9780 }},
  zoom: 14,
  tilt: 60,
  heading: 0,
  mapId: '4504f8b37365c3d0',
  mapTypeId: 'satellite'
}});

// === 이미지 이벤트 로드 ===
async function loadImgEvents() {{
  const res = await fetch('/img-inbox');
  const data = await res.json();
  console.log('Loaded image events:', data);

  const layer = new IconLayer({{
    id: 'img-layer',
    data: data,
    pickable: true,
    iconAtlas: 'https://upload.wikimedia.org/wikipedia/commons/e/ec/RedDot.svg',
    iconMapping: {{
      marker: {{ x: 0, y: 0, width: 256, height: 256, anchorY: 128 }}
    }},
    getIcon: d => 'marker',
    getSize: 4,
    sizeScale: 15,
    getPosition: d => [d.lon, d.lat],
    onHover: ({{object, x, y}}) => {{
      if (object) {{
        tooltip.style.display = 'block';
        tooltip.style.left = x + 'px';
        tooltip.style.top = y + 'px';
        tooltip.innerHTML = `<b>${{object.note}}</b><br>(${{
          object.lat.toFixed(5)
        }}, ${{
          object.lon.toFixed(5)
        }})`;
      }} else {{
        tooltip.style.display = 'none';
      }}
    }},
    onClick: info => {{
      if (info.object && info.object.url) {{
        window.open(info.object.url, '_blank');
      }}
    }}
  }});

  if (overlay) overlay.setMap(null);
  overlay = new GoogleMapsOverlay({{ layers: [layer] }});
  overlay.setMap(map);
}}

// === 새로고침 버튼 ===
document.getElementById('refresh').addEventListener('click', loadImgEvents);

// === 초기 로드 ===
loadImgEvents();
</script>
</body>
</html>"""
    resp = make_response(html_doc)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp


# === 실행 ===
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8050, debug=True)
