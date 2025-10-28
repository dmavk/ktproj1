# ======================================
# vwmap.py  (deck.gl + Google Maps 기반)
# ======================================

from flask import Blueprint, make_response

# Blueprint 생성
imgbp = Blueprint("imgbp", __name__)

# Google Maps API 키 (실제 키로 교체 가능)
GOOGLE_MAPS_API_KEY = "AIzaSyDNUWxSp_t5VrXMFD5UrH9QaqLm7cGrecg"

# ------------------------------------------------------------
# /img-map : deck.gl + Google Maps 시각화
# ------------------------------------------------------------
@imgbp.route("/img-map")
def img_map_page():
    html = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<title>DroneEngage - 3D 지도 (deck.gl + Google Maps)</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />

<!-- ✅ Google Maps & deck.gl -->
<script src="https://maps.googleapis.com/maps/api/js?key={GOOGLE_MAPS_API_KEY}"></script>
<script src="https://unpkg.com/deck.gl@8.9.35/dist.min.js"></script>

<style>
html, body, #map {{
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
  padding: 6px 8px;
  border-radius: 6px;
  font-size: 12px;
  display: none;
  pointer-events: none;
}}
.control-panel {{
  position: fixed;
  top: 10px; left: 10px;
  background: rgba(0,0,0,0.45);
  color: #fff;
  font-family: system-ui, Arial;
  padding: 8px 10px;
  border-radius: 10px;
  z-index: 9999;
}}
select {{
  background: #132033;
  color: #cfe8ff;
  border: 1px solid #3a4e7a;
  border-radius: 8px;
  padding: 5px 8px;
}}
</style>
</head>
<body>
<div id="map"></div>
<div id="tooltip"></div>
<div class="control-panel">
  <b>3D 지도 (deck.gl + Google Maps)</b><br>
  <select id="mapStyle">
    <option value="satellite">Satellite (위성)</option>
    <option value="roadmap">Roadmap (도로)</option>
    <option value="terrain">Terrain (지형)</option>
    <option value="hybrid">Hybrid (혼합)</option>
  </select>
</div>

<script>
const {{GoogleMapsOverlay, ScatterplotLayer}} = deck;

// ✅ 예시 데이터 (드론 위치)
//const drones = [
//  {{ position: [126.9780, 37.5665, 120], name: "Drone A" }},
//  {{ position: [126.9820, 37.5650, 220], name: "Drone B" }},
//  {{ position: [126.9750, 37.5680, 320], name: "Drone C" }}
//];

// ✅ Google 지도 초기화
let map = new google.maps.Map(document.getElementById('map'), {{
  center: {{ lat: 37.5665, lng: 126.9780 }},
  zoom: 15,
  tilt: 60,
  heading: 0,
  mapTypeId: 'satellite'
}});

// ✅ deck.gl Layer 생성
//const scatterLayer = new ScatterplotLayer({{
//  id: 'drone-layer',
//  data: drones,
//  getPosition: d => d.position,
//  getRadius: 60,
//  radiusScale: 2,
//  getFillColor: [0, 200, 255, 230],
//  pickable: true,
//  onHover: ({{object, x, y}}) => {{
//    const tooltip = document.getElementById('tooltip');
//    if (object) {{
//      tooltip.style.display = 'block';
//      tooltip.style.left = x + 'px';
//      tooltip.style.top = y + 'px';
//      tooltip.innerHTML = `<b>${{object.name}}</b><br>고도: ${{object.position[2]}} m`;
//    }} else {{
//      tooltip.style.display = 'none';
//    }}
//  }}
//}});

//const overlay = new GoogleMapsOverlay({{ layers: [scatterLayer] }});
//overlay.setMap(map);

// ✅ 지도 스타일 전환
document.getElementById('mapStyle').addEventListener('change', e => {{
  map.setMapTypeId(e.target.value);
}});
</script>
</body>
</html>"""
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp
