

from flask import Flask, make_response
import os

app = Flask(__name__)

@app.route("/")
def index():
    ION_TOKEN = os.getenv("CESIUM_ION_TOKEN", "")
    ION_TOKEN = os.getenv("CESIUM_ION_TOKEN",
                           "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJqdGkiOiIyYzQ5Y2JkYS05Zjk5LTRhMzMtYWFiMS05ZGIzYTA5OGFhZTQiLCJpZCI6MzQyNjc2LCJpYXQiOjE3NTgyMzg2MDB9.ilgb8Z8rsesnBolJK7gA1XPSVOjRVbDE3QaWGJIBnYM")


    html_doc = f"""
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Cesium 3D Map</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/cesium@1.114.0/Build/Cesium/Widgets/widgets.css">
<style>
  html, body, #cesiumContainer {{
    width: 100%; height: 100%; margin: 0; padding: 0; background: #000;
  }}
</style>
</head>
<body>
<div id="cesiumContainer"></div>
<script src="https://cdn.jsdelivr.net/npm/cesium@1.114.0/Build/Cesium/Cesium.js"></script>
<script>
(async function start() {{
  const ION_TOKEN = "{ION_TOKEN}";
  if (ION_TOKEN) {{
    Cesium.Ion.defaultAccessToken = ION_TOKEN;
  }}

  // === Viewer 생성 ===
  const viewer = new Cesium.Viewer('cesiumContainer', {{
    terrainProvider: ION_TOKEN ? Cesium.createWorldTerrain() : undefined,
    baseLayerPicker: true,
    sceneModePicker: true,
    geocoder: false,
    animation: false,
    timeline: false,
    navigationHelpButton: true
  }});

  // === 기본 타일 (Ion 토큰 없을 경우 OSM 타일 적용) ===
  if (!ION_TOKEN) {{
    const osm = new Cesium.UrlTemplateImageryProvider({{
      url: "https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png",
      subdomains: ["a","b","c"],
      credit: "© OpenStreetMap"
    }});
    viewer.imageryLayers.addImageryProvider(osm);
  }}

  // === 초기 카메라 위치 (서울) ===
  viewer.camera.flyTo({{
    destination: Cesium.Cartesian3.fromDegrees(126.9780, 37.5665, 1500000.0),
    duration: 1.5
  }});
}})();
</script>
</body>
</html>
"""
    resp = make_response(html_doc)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8050, debug=True)
