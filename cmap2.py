from flask import Flask
from dash import Dash, html
import dash_html_components as html

app = Flask(__name__)
dash_app = Dash(__name__, server=app, url_base_pathname="/")

# === Flask + Dash 통합 deck.gl + Google Maps ===
# (Google Maps API 키가 반드시 필요합니다.)
GOOGLE_MAPS_API_KEY = "AIzaSyDNUWxSp_t5VrXMFD5UrH9QaqLm7cGrecg"  # 👈 여기에 실제 키 입력
GOOGLE_MAPS_API_KEY = "AIzaSyDNUWxSp_t5VrXMFD5UrH9QaqLm7cGrecg"

# --- HTML layout ---
dash_app.layout = html.Div(
    [
        html.H3("🚁 DroneEngage 3D Viewer (deck.gl + Google Maps)",
                style={"color": "#cfe8ff", "padding": "10px"}),
        html.Iframe(
            src="/map3d",
            style={"width": "100%", "height": "90vh", "border": "0", "backgroundColor": "#000"}
        )
    ],
    style={"background": "#0d1117", "height": "100vh"}
)

# --- Google Maps + deck.gl 페이지 ---
@app.route("/map3d")
def map3d():
    html_doc = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<title>deck.gl + Google Maps</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />

<script src="https://maps.googleapis.com/maps/api/js?key={GOOGLE_MAPS_API_KEY}"></script>
<script src="https://unpkg.com/deck.gl@8.9.35/dist.min.js"></script>

<style>
html,body,#map {{
  margin: 0; padding: 0;
  width: 100%; height: 100%;
  background: #000;
}}
#tooltip {{
  position: absolute;
  z-index: 1000;
  background: rgba(0,0,0,0.7);
  color: #fff;
  padding: 5px 8px;
  border-radius: 4px;
  font-size: 12px;
  display: none;
  pointer-events: none;
}}
</style>
</head>
<body>
<div id="map"></div>
<div id="tooltip"></div>

<script>
// ⚠️ 중괄호는 {{ → {{{{ , }} → }}}} 로 바꿔야 함!
const {{GoogleMapsOverlay, ScatterplotLayer}} = deck;

const drones = [
  {{ position: [126.9780, 37.5665, 120], name: "Drone A" }},
  {{ position: [126.9820, 37.5650, 200], name: "Drone B" }},
  {{ position: [126.9750, 37.5680, 320], name: "Drone C" }}
];

const map = new google.maps.Map(document.getElementById('map'), {{
  center: {{ lat: 37.5665, lng: 126.9780 }},
  zoom: 14,
  tilt: 60,
  heading: 0,
  mapId: '4504f8b37365c3d0',
  mapTypeId: 'satellite'
}});

const scatterLayer = new ScatterplotLayer({{
  id: 'drone-layer',
  data: drones,
  getPosition: d => d.position,
  getRadius: 60,
  radiusScale: 2,
  getFillColor: [0, 200, 255, 230],
  pickable: true,
  onHover: ({{object, x, y}}) => {{
    const tooltip = document.getElementById("tooltip");
    if (object) {{
      tooltip.style.display = "block";
      tooltip.style.left = x + "px";
      tooltip.style.top = y + "px";
      tooltip.innerHTML = `${{object.name}}<br>Alt: ${{object.position[2]}} m`;
    }} else {{
      tooltip.style.display = "none";
    }}
  }}
}});

const overlay = new GoogleMapsOverlay({{ layers: [scatterLayer] }});
overlay.setMap(map);
</script>
</body>
</html>"""
    return html_doc

# --- 실행 ---
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8050, debug=True)
