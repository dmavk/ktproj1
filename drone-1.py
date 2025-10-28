


"""


VWORLD_API_KEY = os.getenv("VWORLD_API_KEY", "4771156FE49F31389913312EBB80BCDB")
ION_TOKEN     = os.getenv("CESIUM_ION_TOKEN", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJqdGkiOiIyYzQ5Y2JkYS05Zjk5LTRhMzMtYWFiMS05ZGIzYTA5OGFhZTQiLCJpZCI6MzQyNjc2LCJpYXQiOjE3NTgyMzg2MDB9.ilgb8Z8rsesnBolJK7gA1XPSVOjRVbDE3QaWGJIBnYM")


"""



# app.py
# Flask + Dash + Cesium 3D 드론 지도
# - HUD: 카메라 MSL / 중심 지면고도 / AGL
# - 좌클릭 웨이포인트(지면고도 라벨)
# - 초기화 타이밍 안정화 및 숫자 포맷 null 안전화

import os
from flask import Flask, Response
from dash import Dash, html, dcc, Input, Output

CESIUM_VER = "1.113"
ION_TOKEN  = os.getenv("CESIUM_ION_TOKEN", "")
ION_TOKEN     = os.getenv("CESIUM_ION_TOKEN", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJqdGkiOiIyYzQ5Y2JkYS05Zjk5LTRhMzMtYWFiMS05ZGIzYTA5OGFhZTQiLCJpZCI6MzQyNjc2LCJpYXQiOjE3NTgyMzg2MDB9.ilgb8Z8rsesnBolJK7gA1XPSVOjRVbDE3QaWGJIBnYM")

# ---------------- Flask (개발용 CSP) ----------------
server = Flask(__name__)

@server.after_request
def add_csp(resp: Response):
    # Cesium(워커/wasm) 허용. 운영 전환 시 필요한 도메인만 남겨 더 좁히세요.
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "img-src 'self' data: blob: https:; "
        "style-src 'self' 'unsafe-inline' https:; "
        "font-src 'self' data: https:; "
        "connect-src 'self' data: blob: https: http: ws: wss:; "
        "worker-src 'self' blob: data:; "
        "child-src 'self' blob: data:; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' 'wasm-unsafe-eval' blob: data: https:; "
        "frame-ancestors 'self'; "
    )
    return resp

# ---------------- Dash ----------------
app = Dash(__name__, server=server, routes_pathname_prefix="/")

app.layout = html.Div(
    [
        html.Div(
            dcc.Dropdown(
                id="viewSelector",
                options=[
                    {"label": "World 3D (위성)",      "value": "world"},
                    {"label": "World 3D + OSM 건물", "value": "world_osm"},
                ],
                value="world", clearable=False, style={"width": 260},
            ),
            style={"position": "absolute", "zIndex": 1000, "top": 10, "left": 10},
        ),

        html.Div(id="cesiumContainer", style={"position": "absolute", "inset": 0}),
        html.Div(
            id="hud",
            style={
                "position": "absolute", "right": 10, "top": 10, "zIndex": 1000,
                "background": "rgba(0,0,0,.65)", "color": "#fff",
                "padding": "8px 10px", "borderRadius": "10px",
                "fontFamily": "system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
                "fontSize": "12px", "lineHeight": "1.4",
                "minWidth": "240px",
            },
            children=[
                html.Div("드론 HUD", style={"fontWeight": 700, "marginBottom": 6}),
                html.Div(id="hudText", children="초기화 중…"),
                html.Div("• 좌클릭: 웨이포인트 추가 (지면고도 라벨)", style={"opacity": 0.8, "marginTop": 6}),
            ],
        ),
        html.Div(id="viewEcho", style={"display": "none"}),
    ],
    style={"position": "relative", "height": "100vh", "width": "100vw", "overflow": "hidden"},
)

# ---------------- index_string (f-string 사용 안 함: placeholder 치환) ----------------
app.index_string = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8"/>
    <title>Drone 3D Map (Cesium)</title>
    {%metas%}
    <link rel="stylesheet" href="https://cesium.com/downloads/cesiumjs/releases/__VER__/Build/Cesium/Widgets/widgets.css"/>
    {%favicon%}{%css%}
    <style>
      html, body, #_dash-app-content { height:100%; margin:0; }
      #cesiumContainer { position:absolute; inset:0; }
      .labelBG {
        background: rgba(0,0,0,.6);
        color: #fff;
        padding: 2px 6px;
        border-radius: 6px;
        font-size: 12px;
        border: 1px solid rgba(255,255,255,.25);
      }
    </style>
  </head>
  <body>
    {%app_entry%}{%config%}{%scripts%}{%renderer%}
    <script src="https://cesium.com/downloads/cesiumjs/releases/__VER__/Build/Cesium/Cesium.js"></script>
    <script>
    (function(){
      const ION = "__ION__";

      let viewer=null;
      let osmBuildings=null;
      let hudTextEl=null;

      // --- 안전 숫자 포맷터 (null/undefined/NaN 모두 '-') ---
      function fmt(n, d) {
        const v = Number(n);
        return Number.isFinite(v) ? v.toFixed(d) : "-";
      }
      function deg(n){
        const v = Number(n);
        return Number.isFinite(v) ? v.toFixed(6) : "-";
      }
      function m(n){
        const v = Number(n);
        return Number.isFinite(v) ? v.toFixed(1) : "-";
      }

      function setHud(lines){
        if(!hudTextEl) return;
        hudTextEl.innerHTML = lines.join("<br/>");
      }

      // ---- 안전한 초기화 대기 ----
      function waitForEl(id, cb, deadline){
        const el = document.getElementById(id);
        if (el) return cb(el);
        if (!deadline) deadline = performance.now() + 15000;
        if (performance.now() > deadline) {
          console.error("timeout waiting for #"+id);
          return;
        }
        requestAnimationFrame(function(){ waitForEl(id, cb, deadline); });
      }

      function initViewer(containerEl){
        Cesium.Ion.defaultAccessToken = ION || "";
        let terrain;
        try{ terrain = Cesium.Terrain.fromWorldTerrain(); }
        catch(_){ terrain = new Cesium.EllipsoidTerrain(); }

        // 컨테이너 'id' 문자열이 아닌 실제 엘리먼트 객체 전달
        viewer = new Cesium.Viewer(containerEl, {
          animation:false, timeline:false, baseLayerPicker:false,
          geocoder:false, sceneModePicker:true, navigationHelpButton:false,
          terrain
        });

        (async ()=>{
          try{
            if (ION){
              const imagery = await Cesium.createWorldImageryAsync({style: Cesium.IonWorldImageryStyle.AERIAL});
              viewer.imageryLayers.addImageryProvider(imagery);
            } else {
              viewer.imageryLayers.addImageryProvider(new Cesium.OpenStreetMapImageryProvider());
            }
          }catch(e){
            console.warn("Imagery fail; fallback OSM", e);
            viewer.imageryLayers.addImageryProvider(new Cesium.OpenStreetMapImageryProvider());
          }
        })();

        viewer.camera.flyTo({
          destination: Cesium.Cartesian3.fromDegrees(126.9780, 37.5665, 15000)
        });

        hudTextEl = document.getElementById("hudText");

        // 카메라 이동 시 HUD 갱신(디바운스)
        let t=null;
        viewer.camera.changed.addEventListener(function(){
          clearTimeout(t);
          t = setTimeout(updateHudCenterAGL, 250);
        });

        // 좌클릭: 웨이포인트 라벨(지면고도)
        const handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
        handler.setInputAction(async function(click){
          const cart = groundFromWindowPos(click.position);
          if(!cart) return;
          const sampled = await sampleTerrainMostDetailed([cart]);
          const g = sampled[0];
          const hGround = Number(g.height);
          const pos = Cesium.Cartesian3.fromRadians(g.longitude, g.latitude, hGround);
          viewer.entities.add({
            position: pos,
            point: { pixelSize: 6, color: Cesium.Color.CYAN },
            label: {
              text: `Lat ${deg(Cesium.Math.toDegrees(g.latitude))} / Lon ${deg(Cesium.Math.toDegrees(g.longitude))}\\n지면고도 ${m(hGround)} m MSL`,
              font: "13px/1.4 system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
              showBackground: true,
              backgroundColor: Cesium.Color.BLACK.withAlpha(0.6),
              pixelOffset: new Cesium.Cartesian2(0, -18),
              horizontalOrigin: Cesium.HorizontalOrigin.CENTER,
              verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
              disableDepthTestDistance: Number.POSITIVE_INFINITY
            }
          });
        }, Cesium.ScreenSpaceEventType.LEFT_CLICK);

        setTimeout(updateHudCenterAGL, 600);
      }

      function groundFromWindowPos(winPos){
        const scene = viewer.scene;
        const ray = viewer.camera.getPickRay(winPos);
        if(!ray) return null;
        const cart = scene.globe.pick(ray, scene);
        return cart ? Cesium.Cartographic.fromCartesian(cart) : null;
      }

      async function sampleTerrainMostDetailed(carts){
        try{ return await Cesium.sampleTerrainMostDetailed(viewer.terrainProvider, carts); }
        catch(_){ return carts.map(c => { c.height = 0; return c; }); }
      }

      // --- HUD 계산 (중심 픽 실패시도 안전) ---
      async function updateHudCenterAGL(){
        if(!viewer) return;

        const canvas = viewer.scene.canvas;
        const center = new Cesium.Cartesian2(canvas.clientWidth/2, canvas.clientHeight/2);

        // 카메라 고도(MSL)
        const cameraCarto = Cesium.Cartographic.fromCartesian(viewer.camera.positionWC);
        const camLatDeg = Cesium.Math.toDegrees(cameraCarto.latitude);
        const camLonDeg = Cesium.Math.toDegrees(cameraCarto.longitude);
        const cameraMSL  = Number(cameraCarto.height);

        // 화면 중심 지면 고도
        const picked = groundFromWindowPos(center);
        let groundMSL = NaN;
        let agl       = NaN;

        if (picked) {
          try {
            const res = await sampleTerrainMostDetailed([picked]);
            groundMSL = Number(res[0].height);
            agl       = cameraMSL - groundMSL;
          } catch (_) {
            groundMSL = 0;
            agl       = cameraMSL - groundMSL;
          }
        }
        // picked가 null인 경우(하늘/수평선): groundMSL/agl는 NaN → 포매터가 '-' 표시

        const lines = [
          `카메라: Lat ${deg(camLatDeg)} / Lon ${deg(camLonDeg)}`,
          `고도(MSL): ${m(cameraMSL)} m`,
          `중심 지면고도: ${m(groundMSL)} m`,
          `<b>AGL(지면대비): ${m(agl)} m</b>`
        ];
        setHud(lines);
      }

      // 외부에서 보기 전환 (OSM 건물 on/off)
      window.switchView = async function(kind){
        if(!viewer) return;
        if (osmBuildings){
          try { viewer.scene.primitives.remove(osmBuildings); } catch(_){}
          osmBuildings = null;
        }
        if (kind === "world_osm"){
          try {
            osmBuildings = await Cesium.createOsmBuildingsAsync();
            viewer.scene.primitives.add(osmBuildings);
          } catch(e) {
            console.warn("OSM Buildings load fail", e);
          }
        }
        setTimeout(updateHudCenterAGL, 200);
      };

      // 부팅: Dash가 컨테이너를 만든 뒤 초기화
      function boot(){
        if(!window.Cesium){ console.error("CesiumJS not loaded"); return; }
        waitForEl("cesiumContainer", function(el){ initViewer(el); });
      }
      if (document.readyState==="complete"||document.readyState==="interactive") setTimeout(boot,0);
      else {
        window.addEventListener("DOMContentLoaded", boot);
        window.addEventListener("load", boot);
      }
    })();
    </script>
  </body>
</html>
"""

# 플레이스홀더 안전 치환
safe_token = ION_TOKEN.replace("\\", "\\\\").replace('"', '\\"')
app.index_string = app.index_string.replace("__VER__", CESIUM_VER)
app.index_string = app.index_string.replace("__ION__", safe_token)

# Dash → JS (드롭다운 값 전달)
app.clientside_callback(
    "function(v){ if(window && window.switchView){ window.switchView(v); } return v || ''; }",
    Output("viewEcho","children"),
    Input("viewSelector","value"),
)

if __name__ == "__main__":
    print("Run → http://127.0.0.1:8000")
    app.run(host="0.0.0.0", port=8000, debug=True)






