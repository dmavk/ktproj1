
# Final OK !!!!!!!!!!

"""


VWORLD_API_KEY = os.getenv("VWORLD_API_KEY", "4771156FE49F31389913312EBB80BCDB")
CESIUM_ION     = os.getenv("CESIUM_ION_TOKEN", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJqdGkiOiIyYzQ5Y2JkYS05Zjk5LTRhMzMtYWFiMS05ZGIzYTA5OGFhZTQiLCJpZCI6MzQyNjc2LCJpYXQiOjE3NTgyMzg2MDB9.ilgb8Z8rsesnBolJK7gA1XPSVOjRVbDE3QaWGJIBnYM")


"""








# app.py
# Flask + Dash + Leaflet(2D) + Cesium(3D) + VWorld 타일 프록시(자동 탐지/폴백)

import os, math, requests
from flask import Flask, Response, make_response, jsonify
from dash import Dash, html, dcc, Input, Output

# -------- 환경변수 --------
VWORLD_API_KEY = os.getenv("VWORLD_API_KEY", "")
VWORLD_REFERER = os.getenv("VWORLD_REFERER", "http://localhost")
VWORLD_HOST    = os.getenv("VWORLD_HOST", "https://api.vworld.kr")
CESIUM_ION     = os.getenv("CESIUM_ION_TOKEN", "")
CESIUM_VER     = os.getenv("CESIUM_VER", "1.113")
VWORLD_API_KEY = os.getenv("VWORLD_API_KEY", "4771156FE49F31389913312EBB80BCDB")
CESIUM_ION     = os.getenv("CESIUM_ION_TOKEN", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJqdGkiOiIyYzQ5Y2JkYS05Zjk5LTRhMzMtYWFiMS05ZGIzYTA5OGFhZTQiLCJpZCI6MzQyNjc2LCJpYXQiOjE3NTgyMzg2MDB9.ilgb8Z8rsesnBolJK7gA1XPSVOjRVbDE3QaWGJIBnYM")

# -------- 유틸 --------
SEOUL = (126.9780, 37.5665)
DEFAULT_ZOOM = 13

def zxy_from_lonlat(lon, lat, z):
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return x, y

def http_ok_image(r): return r.ok and r.headers.get("Content-Type","").startswith("image/")

def req(url, timeout=8, headers=None):
    h = {"User-Agent":"Dash-Leaflet-Proxy","Accept":"*/*"}
    if headers: h.update(headers)
    return requests.get(url, timeout=timeout, headers=h)

# -------- 1) WMTS 자동 탐지 --------
def detect_wmts_pattern():
    if not VWORLD_API_KEY: return None
    z = DEFAULT_ZOOM
    x, y = zxy_from_lonlat(*SEOUL, z)
    y_tms = (1<<z)-1-y
    patterns = [
        ("zxy",    f"{VWORLD_HOST}/req/wmts/1.0.0/{VWORLD_API_KEY}/Base/default/GoogleMapsCompatible/{z}/{x}/{y}.png"),
        ("zyx",    f"{VWORLD_HOST}/req/wmts/1.0.0/{VWORLD_API_KEY}/Base/default/GoogleMapsCompatible/{z}/{y}/{x}.png"),
        ("zxtms",  f"{VWORLD_HOST}/req/wmts/1.0.0/{VWORLD_API_KEY}/Base/default/GoogleMapsCompatible/{z}/{x}/{y_tms}.png"),
        ("zyxtms", f"{VWORLD_HOST}/req/wmts/1.0.0/{VWORLD_API_KEY}/Base/default/GoogleMapsCompatible/{z}/{y_tms}/{x}.png"),
    ]
    for name, u in patterns:
        try:
            r = req(u, headers={"Referer":VWORLD_REFERER})
            if http_ok_image(r): return name
        except requests.RequestException:
            pass
    return None

WMTS_MODE = detect_wmts_pattern()
print("WMTS_MODE =", WMTS_MODE or "None (will use xdworld fallback)")

# -------- 2) xdworld(공개타일) 자동 탐색 --------
VERSIONS = os.getenv("XDWORLD_VERSIONS","202307,202112,202009,202002,201912,201802,201512").split(",")
LAYER_EXTS = {"Base":["png"], "Hybrid":["png"], "Satellite":["jpg","jpeg"]}

def find_best_xd_variant(layer:str):
    z = DEFAULT_ZOOM
    x, y = zxy_from_lonlat(*SEOUL, z)
    for ver in VERSIONS:
        for ext in LAYER_EXTS[layer]:
            url = f"http://xdworld.vworld.kr:8080/2d/{layer}/{ver}/{z}/{x}/{y}.{ext}"
            try:
                r = req(url, timeout=6)
                if http_ok_image(r): return {"version":ver, "ext":ext}
            except requests.RequestException:
                pass
    return None

XDWORLD_BEST = {
    "Base":      find_best_xd_variant("Base"),
    "Satellite": find_best_xd_variant("Satellite"),
    "Hybrid":    find_best_xd_variant("Hybrid"),
}
print("XDWORLD_BEST =", XDWORLD_BEST)

# -------- Flask(프록시 + CSP) --------
server = Flask(__name__)

@server.after_request
def csp(resp: Response):
    # ✅ Cesium 워커/wasm 허용(개발용), Leaflet용 이미지/스크립트 허용
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

@server.get("/diag")
def diag():
    return jsonify({
        "WMTS_MODE": WMTS_MODE,
        "XDWORLD_BEST": XDWORLD_BEST,
        "VERSIONS_TRIED": VERSIONS,
        "REFERER": VWORLD_REFERER,
        "API_KEY_SET": bool(VWORLD_API_KEY),
        "CESIUM_VER": CESIUM_VER,
        "ION_SET": bool(CESIUM_ION),
    })

def wmts_url(layer, z, x, y, ext, mode):
    if mode == "zxy":   return f"{VWORLD_HOST}/req/wmts/1.0.0/{VWORLD_API_KEY}/{layer}/default/GoogleMapsCompatible/{z}/{x}/{y}.{ext}"
    if mode == "zyx":   return f"{VWORLD_HOST}/req/wmts/1.0.0/{VWORLD_API_KEY}/{layer}/default/GoogleMapsCompatible/{z}/{y}/{x}.{ext}"
    y_tms = (1<<z)-1-y
    if mode == "zxtms": return f"{VWORLD_HOST}/req/wmts/1.0.0/{VWORLD_API_KEY}/{layer}/default/GoogleMapsCompatible/{z}/{x}/{y_tms}.{ext}"
    return f"{VWORLD_HOST}/req/wmts/1.0.0/{VWORLD_API_KEY}/{layer}/default/GoogleMapsCompatible/{z}/{((1<<z)-1-y)}/{x}.{ext}"

def respond_image(r, tag):
    resp = make_response(r.content, 200)
    resp.headers["Content-Type"]  = r.headers.get("Content-Type","image/png")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    resp.headers["X-Source"]      = tag
    return resp

def respond_fail(r, tag="fail"):
    code = r.status_code if r is not None else 502
    body = r.content if r is not None else b""
    resp = make_response(body, code)
    resp.headers["Content-Type"]  = (r.headers.get("Content-Type") if (r is not None) else "text/plain")
    resp.headers["X-Source"]      = tag
    return resp

def try_xdworld_all(layer, z, x, y, prefer_ext):
    # 1) 사전 탐색 결과 우선
    first = XDWORLD_BEST.get(layer)
    tried = set()
    if first:
        u = f"http://xdworld.vworld.kr:8080/2d/{layer}/{first['version']}/{z}/{x}/{y}.{first['ext']}"
        r = req(u, timeout=6)
        if http_ok_image(r): return respond_image(r, f"xdworld:{first['version']}.{first['ext']}")
        tried.add((first["version"], first["ext"]))
    # 2) 전체 조합
    last = None
    for ver in VERSIONS:
        for ext in LAYER_EXTS[layer]:
            if (ver, ext) in tried: continue
            u = f"http://xdworld.vworld.kr:8080/2d/{layer}/{ver}/{z}/{x}/{y}.{ext}"
            try:
                r = req(u, timeout=6)
                last = r
                if http_ok_image(r): return respond_image(r, f"xdworld:{ver}.{ext}")
            except requests.RequestException:
                pass
    return respond_fail(last, "xdworld:all_failed")

@server.route("/tiles/<layer>/<int:z>/<int:x>/<int:y>.<ext>")
def tiles(layer, z, x, y, ext):
    layer = layer.capitalize()  # base→Base
    # 1) WMTS 시도
    if WMTS_MODE and VWORLD_API_KEY:
        try:
            u = wmts_url(layer, z, x, y, ext, WMTS_MODE)
            r = req(u, timeout=8, headers={"Referer": VWORLD_REFERER})
            if http_ok_image(r): return respond_image(r, f"wmts:{WMTS_MODE}")
        except requests.RequestException:
            pass
    # 2) 공개타일
    res = try_xdworld_all(layer, z, x, y, ext)
    if res.status_code < 400:
        return res
    # 3) 최종 폴백: Base로 대체
    if layer != "Base":
        base_res = try_xdworld_all("Base", z, x, y, "png")
        base_res.headers["X-Source"] = base_res.headers.get("X-Source","") + " -> fallback(Base)"
        return base_res
    return res

# -------- Dash --------
app = Dash(__name__, server=server, routes_pathname_prefix="/")

app.layout = html.Div(
    [
        html.Div(
            dcc.Dropdown(
                id="mapSelector",
                options=[
                    # 2D
                    {"label":"World 2D - Base",         "value":"2d:base"},
                    {"label":"World 2D - Satellite",    "value":"2d:sat"},
                    {"label":"World 2D - Hybrid(라벨)", "value":"2d:hyb"},
                    # 3D
                    {"label":"World 3D - Cesium(기본)", "value":"3d:world"},
                    {"label":"World 3D - VWorld Base",  "value":"3d:vbase"},
                    {"label":"World 3D - VWorld Sat.",  "value":"3d:vsat"},
                    {"label":"World 3D - VWorld Hybrid","value":"3d:vhyb"},
                ],
                value="2d:base", clearable=False, style={"width": 280},
            ),
            style={"position":"absolute","zIndex":1000,"top":10,"left":10},
        ),
        html.Div(id="leafletMap",      style={"position":"absolute","inset":0, "display":"block"}),
        html.Div(id="cesiumContainer", style={"position":"absolute","inset":0, "display":"none"}),
        html.Div(id="mapSelectorEcho", style={"display":"none"}),
    ],
    style={"position":"relative","height":"100vh","width":"100vw","overflow":"hidden"},
)

app.index_string = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8"/>
    <title>Dash + VWorld 2D/3D</title>
    {%metas%}
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
    <link rel="stylesheet" href="https://cesium.com/downloads/cesiumjs/releases/__CESIUM_VER__/Build/Cesium/Widgets/widgets.css"/>
    {%favicon%}{%css%}
    <style>
      html, body, #_dash-app-content { height:100%; margin:0; }
      #leafletMap, #cesiumContainer { position:absolute; inset:0; }
      .leaflet-container { background:#000; }
      #toast {
        position:fixed; left:50%; transform:translateX(-50%); bottom:18px;
        background:rgba(0,0,0,.8); color:#fff; padding:6px 10px; border-radius:8px;
        font:12px/1.4 system-ui, sans-serif; display:none; z-index:2000;
      }
    </style>
  </head>
  <body>
    {%app_entry%}{%config%}{%scripts%}{%renderer%}
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script src="https://cesium.com/downloads/cesiumjs/releases/__CESIUM_VER__/Build/Cesium/Cesium.js"></script>
    <div id="toast"></div>
    <script>
    (function(){
      const ION_TOKEN = "__ION_TOKEN__";
      var map2D=null, base2D=null, label2D=null;
      var viewer3D=null;

      function waitForEl(id, cb){
        var el=document.getElementById(id);
        if (el) return cb(el);
        requestAnimationFrame(()=>waitForEl(id, cb));
      }
      function toast(msg){
        var t=document.getElementById('toast'); if(!t) return;
        t.innerHTML=msg; t.style.display='block';
        clearTimeout(t._t); t._t=setTimeout(()=>t.style.display='none', 3500);
      }
      function url2D(layer, ext){ return `/tiles/${layer}/{z}/{x}/{y}.${ext}`; }
      function url3D(layer, ext){ return `/tiles/${layer}/{z}/{x}/{y}.${ext}`; }

      // lon/lat → z/x/y 계산(2D 소스 HEAD용)
      function lonLatToTile(lon, lat, z){
        var n = Math.pow(2,z);
        var x = Math.floor((lon + 180.0) / 360.0 * n);
        var latRad = lat * Math.PI / 180.0;
        var y = Math.floor((1.0 - Math.log(Math.tan(latRad) + 1/Math.cos(latRad)) / Math.PI) / 2.0 * n);
        return {x:x, y:y};
      }

      // ---------- 2D ----------
      function ensure2D(){
        if (!map2D) {
          // ✅ 페이드 애니메이션 끄기
          map2D = L.map('leafletMap', {
            center:[37.5665,126.9780], zoom:13, zoomControl:true,
            fadeAnimation:false, zoomAnimation:true, updateWhenIdle:true
          });
        } else {
          setTimeout(function(){ try { map2D.invalidateSize(); } catch(_){} }, 0);
        }
      }
      function set2DLayers(kind){
        ensure2D();
        if (base2D)  { map2D.removeLayer(base2D);  base2D  = null; }
        if (label2D) { map2D.removeLayer(label2D); label2D = null; }
        var opts = { minZoom:6, maxZoom:19, attribution:'&copy; VWorld' };
        if (kind==="base") {
          base2D = L.tileLayer(url2D("Base","png"), opts).addTo(map2D);
        } else if (kind==="sat") {
          base2D = L.tileLayer(url2D("Satellite","jpg"), opts).addTo(map2D);
        } else if (kind==="hyb") {
          base2D  = L.tileLayer(url2D("Satellite","jpg"), opts).addTo(map2D);
          label2D = L.tileLayer(url2D("Hybrid","png"),   opts).addTo(map2D);
        }
        // ✅ 임시 레이어 없이 HEAD만 직접
        if (document.getElementById('leafletMap').style.display !== 'none') {
          var c = map2D.getCenter(), z = map2D.getZoom();
          var t = lonLatToTile(c.lng, c.lat, z);
          var layer = (kind==="base"?"Base":(kind==="sat"?"Satellite":"Hybrid"));
          var ext   = (layer==="Base"||layer==="Hybrid")?"png":"jpg";
          var sample = `/tiles/${layer}/${z}/${t.x}/${t.y}.${ext}`;
          fetch(sample, {method:'HEAD'}).then(r=>{
            var src=r.headers.get('X-Source')||''; if(src) toast('2D Source: <code>'+src+'</code>');
          }).catch(()=>{});
        }
      }

      // ---------- 3D ----------
      function ensure3D(){
        if (viewer3D) return;
        Cesium.Ion.defaultAccessToken = ION_TOKEN || "";
        viewer3D = new Cesium.Viewer("cesiumContainer", {
          animation:false, timeline:false, geocoder:false,
          baseLayerPicker:false, sceneModePicker:true, navigationHelpButton:false,
          terrain: Cesium.Terrain.fromWorldTerrain()
        });
        viewer3D.camera.flyTo({ destination: Cesium.Cartesian3.fromDegrees(126.9780, 37.5665, 15000) });
      }
      function vworldProvider3D(layer, ext){
        return new Cesium.UrlTemplateImageryProvider({
          url: url3D(layer, ext),
          tilingScheme: new Cesium.WebMercatorTilingScheme(),
          maximumLevel: 19,
          credit: "VWorld"
        });
      }
      async function set3D(kind){
        ensure3D();
        try { viewer3D.imageryLayers.removeAll(); } catch(e){}
        if (kind==="world"){
          try{
            if (ION_TOKEN) {
              const prov = await Cesium.createWorldImageryAsync({style: Cesium.IonWorldImageryStyle.AERIAL});
              viewer3D.imageryLayers.addImageryProvider(prov);
            } else {
              viewer3D.imageryLayers.addImageryProvider(new Cesium.OpenStreetMapImageryProvider());
            }
          }catch(e){
            viewer3D.imageryLayers.addImageryProvider(new Cesium.OpenStreetMapImageryProvider());
          }
          return; // world 모드에선 소스 HEAD 체크 생략
        }
        if (kind==="vbase") {
          viewer3D.imageryLayers.addImageryProvider(vworldProvider3D("Base","png"));
        } else if (kind==="vsat") {
          viewer3D.imageryLayers.addImageryProvider(vworldProvider3D("Satellite","jpg"));
          try{ const b=await Cesium.createOsmBuildingsAsync(); viewer3D.scene.primitives.add(b); }catch(_){}
        } else if (kind==="vhyb") {
          viewer3D.imageryLayers.addImageryProvider(vworldProvider3D("Satellite","jpg"));
          viewer3D.imageryLayers.addImageryProvider(vworldProvider3D("Hybrid","png"));
        }
        // 소스 표기(간단 샘플 HEAD)
        var z=13, t=lonLatToTile(126.9780,37.5665,z);
        var layer = (kind==="vbase"?"Base":(kind==="vsat"?"Satellite":"Hybrid"));
        var ext   = (layer==="Base"||layer==="Hybrid")?"png":"jpg";
        var sample = `/tiles/${layer}/${z}/${t.x}/${t.y}.${ext}`;
        fetch(sample, {method:"HEAD"}).then(r=>{
          var src=r.headers.get("X-Source")||""; if(src) toast('3D Source: <code>'+src+'</code>');
        }).catch(()=>{});
        viewer3D.scene.requestRender();
      }

      // ---------- 뷰 전환 ----------
      function show2D(){
        // 2D 화면으로 전환
        document.getElementById('cesiumContainer').style.display='none';
        document.getElementById('leafletMap').style.display='block';
        ensure2D();
      }
      function show3D(){
        // ✅ 3D로 갈 땐 2D 완전 제거(Leaflet fadeAnimated 관련 에러 방지)
        try {
          if (map2D) {
            map2D.eachLayer(function(l){ map2D.removeLayer(l); });
            map2D.off(); map2D.remove();
            map2D = null; base2D = null; label2D = null;
          }
        } catch(_) {}
        document.getElementById('leafletMap').style.display='none';
        document.getElementById('cesiumContainer').style.display='block';
      }

      // 최초 부팅: 2D
      function boot(){ waitForEl("leafletMap", function(){ set2DLayers("base"); }); }
      if (document.readyState==="complete"||document.readyState==="interactive") setTimeout(boot,0);
      else window.addEventListener("DOMContentLoaded", boot);

      // 드롭다운에서 호출
      window.switchMap = async function(v){
        if(!v) return "";
        if (v.startsWith("2d:")){
          show2D();
          var k = v.split(":")[1]; // base|sat|hyb
          set2DLayers(k);
        } else {
          show3D();
          var m = v.split(":")[1]; // world|vbase|vsat|vhyb
          await set3D(m);
        }
        return v;
      };
    })();
    </script>
  </body>
</html>
"""

# 플레이스홀더 치환
app.index_string = app.index_string.replace("__CESIUM_VER__", CESIUM_VER)
app.index_string = app.index_string.replace("__ION_TOKEN__", CESIUM_ION.replace('"','\\"'))

# 드롭다운 → JS 함수 호출
app.clientside_callback(
    "function(v){ if(window && window.switchMap){ return window.switchMap(v); } return v || ''; }",
    Output("mapSelectorEcho","children"),
    Input("mapSelector","value"),
)

if __name__ == "__main__":
    print("Visit /diag for detection status → http://127.0.0.1:8000/diag")
    app.run(host="0.0.0.0", port=8000, debug=True)
