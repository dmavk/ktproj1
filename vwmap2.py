# vwmap2.py
import os, math, requests, json
from flask import Blueprint, Response, make_response, jsonify

# Blueprint 정의
imgbp = Blueprint("imgbp", __name__)

# -------- 환경변수 --------
VWORLD_API_KEY = os.getenv("VWORLD_API_KEY", "4771156FE49F31389913312EBB80BCDB")
CESIUM_ION = os.getenv("CESIUM_ION_TOKEN",
"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJqdGkiOiIyYzQ5Y2JkYS05Zjk5LTRhMzMtYWFiMS05ZGIzYTA5OGFhZTQiLCJpZCI6MzQyNjc2LCJpYXQiOjE3NTgyMzg2MDB9.ilgb8Z8rsesnBolJK7gA1XPSVOjRVbDE3QaWGJIBnYM")
VWORLD_REFERER = os.getenv("VWORLD_REFERER", "http://localhost")
VWORLD_HOST = os.getenv("VWORLD_HOST", "https://api.vworld.kr")
CESIUM_VER = os.getenv("CESIUM_VER", "1.113")

SEOUL = (126.9780, 37.5665)
DEFAULT_ZOOM = 13


# ==================== 유틸 ====================
def zxy_from_lonlat(lon, lat, z):
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return x, y

def http_ok_image(r):
    return r.ok and r.headers.get("Content-Type","").startswith("image/")

def req(url, timeout=8, headers=None):
    h = {"User-Agent":"Dash-Leaflet-Proxy","Accept":"*/*"}
    if headers: h.update(headers)
    return requests.get(url, timeout=timeout, headers=h)


# ==================== WMTS / XDWorld 자동탐지 ====================
def detect_wmts_pattern():
    if not VWORLD_API_KEY:
        return None
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
            if http_ok_image(r):
                return name
        except requests.RequestException:
            pass
    return None

WMTS_MODE = detect_wmts_pattern()
print("WMTS_MODE =", WMTS_MODE or "None (will use xdworld fallback)")

VERSIONS = os.getenv("XDWORLD_VERSIONS","202307,202112,202009,202002").split(",")
LAYER_EXTS = {"Base":["png"], "Hybrid":["png"], "Satellite":["jpg","jpeg"]}

def find_best_xd_variant(layer:str):
    z = DEFAULT_ZOOM
    x, y = zxy_from_lonlat(*SEOUL, z)
    for ver in VERSIONS:
        for ext in LAYER_EXTS[layer]:
            url = f"http://xdworld.vworld.kr:8080/2d/{layer}/{ver}/{z}/{x}/{y}.{ext}"
            try:
                r = req(url, timeout=5)
                if http_ok_image(r):
                    return {"version":ver, "ext":ext}
            except requests.RequestException:
                pass
    return None

XDWORLD_BEST = {
    "Base": find_best_xd_variant("Base"),
    "Satellite": find_best_xd_variant("Satellite"),
    "Hybrid": find_best_xd_variant("Hybrid"),
}


# ==================== 이미지 응답 헬퍼 ====================
def respond_image(r, tag):
    resp = make_response(r.content, 200)
    resp.headers["Content-Type"]  = r.headers.get("Content-Type","image/png")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    resp.headers["X-Source"]      = tag
    return resp


# ==================== 타일 프록시 ====================
@imgbp.route("/tiles/<layer>/<int:z>/<int:x>/<int:y>.<ext>")
def tiles(layer, z, x, y, ext):
    layer = layer.capitalize()  # base -> Base
    # 어떤 레이어든 후보 확장자 풀을 넉넉히 시도
    cand_exts = list(dict.fromkeys([
        ext.lower(),
        *LAYER_EXTS.get(layer, []),
        "png", "jpg", "jpeg"
    ]))

    # ---- 1) WMTS (검출된 모드가 있으면 우선, 없으면 전 패턴 시도) ----
    wmts_modes = [WMTS_MODE] if WMTS_MODE else ["zxy", "zyx", "zxtms", "zyxtms"]
    def _wmts_url(mode, lay, Z, X, Y, E):
        if mode == "zxy":
            return f"{VWORLD_HOST}/req/wmts/1.0.0/{VWORLD_API_KEY}/{lay}/default/GoogleMapsCompatible/{Z}/{X}/{Y}.{E}"
        if mode == "zyx":
            return f"{VWORLD_HOST}/req/wmts/1.0.0/{VWORLD_API_KEY}/{lay}/default/GoogleMapsCompatible/{Z}/{Y}/{X}.{E}"
        y_tms = (1 << Z) - 1 - Y
        if mode == "zxtms":
            return f"{VWORLD_HOST}/req/wmts/1.0.0/{VWORLD_API_KEY}/{lay}/default/GoogleMapsCompatible/{Z}/{X}/{y_tms}.{E}"
        # zyxtms
        return f"{VWORLD_HOST}/req/wmts/1.0.0/{VWORLD_API_KEY}/{lay}/default/GoogleMapsCompatible/{Z}/{((1<<Z)-1-Y)}/{X}.{E}"

    if VWORLD_API_KEY:
        for mode in wmts_modes:
            for e in cand_exts:
                try:
                    u = _wmts_url(mode, layer, z, x, y, e)
                    r = req(u, headers={"Referer": VWORLD_REFERER})
                    if http_ok_image(r):
                        resp = respond_image(r, f"wmts:{mode}.{e}")
                        return resp
                except Exception:
                    pass  # 다음 조합 시도

    # ---- 2) xdworld 폴백 (사전 탐색 우선 → 전체 버전/확장자 전부 시도) ----
    tried = set()
    first = XDWORLD_BEST.get(layer)
    if first:
        tried.add((first["version"], first["ext"]))
        try:
            u = f"http://xdworld.vworld.kr:8080/2d/{layer}/{first['version']}/{z}/{x}/{y}.{first['ext']}"
            r = req(u, timeout=6)
            if http_ok_image(r):
                return respond_image(r, f"xdworld:{first['version']}.{first['ext']}")
        except Exception:
            pass

    last = None
    for ver in VERSIONS:
        for e in cand_exts:
            if (ver, e) in tried:
                continue
            try:
                u = f"http://xdworld.vworld.kr:8080/2d/{layer}/{ver}/{z}/{x}/{y}.{e}"
                r = req(u, timeout=6)
                last = r
                if http_ok_image(r):
                    return respond_image(r, f"xdworld:{ver}.{e}")
            except Exception:
                pass

    # ---- 3) Base로 최종 폴백(그래도 실패하면 원인 파악 쉽게 404) ----
    if layer != "Base":
        for ver in VERSIONS:
            for e in ["png", "jpg", "jpeg"]:
                try:
                    u = f"http://xdworld.vworld.kr:8080/2d/Base/{ver}/{z}/{x}/{y}.{e}"
                    r = req(u, timeout=6)
                    if http_ok_image(r):
                        resp = respond_image(r, f"xdworld:Base:{ver}.{e} -> fallback")
                        return resp
                except Exception:
                    pass

    # 디버그 힌트를 위해 상태코드/소스를 그대로 전달
    if last is not None:
        return respond_image(last, "xdworld:last_error") if http_ok_image(last) else respond_fail(last, "all_failed")
    return Response("tile not found", status=404)

@imgbp.route("/img-map/diag")
def img_map_diag():
    z = DEFAULT_ZOOM
    x, y = zxy_from_lonlat(*SEOUL, z)
    report = {"wmts_mode": WMTS_MODE, "vworld_key_set": bool(VWORLD_API_KEY), "checks": []}

    # WMTS 모든 조합 확인
    wmts_modes = [WMTS_MODE] if WMTS_MODE else ["zxy","zyx","zxtms","zyxtms"]
    for mode in wmts_modes:
        for e in ["jpg","jpeg","png"]:
            try:
                u = f"{VWORLD_HOST}/req/wmts/1.0.0/{VWORLD_API_KEY}/Satellite/default/GoogleMapsCompatible/{z}/{x}/{y}.{e}"
                if mode == "zyx":
                    u = f"{VWORLD_HOST}/req/wmts/1.0.0/{VWORLD_API_KEY}/Satellite/default/GoogleMapsCompatible/{z}/{y}/{x}.{e}"
                elif mode in ("zxtms","zyxtms"):
                    y_tms = (1<<z)-1-y
                    u = f"{VWORLD_HOST}/req/wmts/1.0.0/{VWORLD_API_KEY}/Satellite/default/GoogleMapsCompatible/{z}/{x}/{y_tms}.{e}"
                    if mode == "zyxtms":
                        u = f"{VWORLD_HOST}/req/wmts/1.0.0/{VWORLD_API_KEY}/Satellite/default/GoogleMapsCompatible/{z}/{y_tms}/{x}.{e}"
                r = req(u, headers={"Referer": VWORLD_REFERER})
                report["checks"].append({"src":"wmts", "mode":mode, "ext":e, "ok": http_ok_image(r), "status": getattr(r, "status_code", None)})
                if http_ok_image(r):
                    report["wmts_hit"] = {"mode": mode, "ext": e}
                    return jsonify(report)
            except Exception as ex:
                report["checks"].append({"src":"wmts", "mode":mode, "ext":e, "ok": False, "err": str(ex)})

    # xdworld 모든 버전/확장자 확인
    for ver in VERSIONS:
        for e in ["jpg","jpeg","png"]:
            try:
                u = f"http://xdworld.vworld.kr:8080/2d/Satellite/{ver}/{z}/{x}/{y}.{e}"
                r = req(u, timeout=6)
                report["checks"].append({"src":"xdworld", "ver":ver, "ext":e, "ok": http_ok_image(r), "status": getattr(r, "status_code", None)})
                if http_ok_image(r):
                    report["xdworld_hit"] = {"ver": ver, "ext": e}
                    return jsonify(report)
            except Exception as ex:
                report["checks"].append({"src":"xdworld", "ver":ver, "ext":e, "ok": False, "err": str(ex)})

    return jsonify(report)

# ==================== /img-map 라우트 ====================
@imgbp.route("/img-map")
def img_map_page():
    TEMPLATE_HTML = """
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<title>Image Map 3D</title>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<link rel="stylesheet" href="https://cesium.com/downloads/cesiumjs/releases/__CESIUM_VER__/Build/Cesium/Widgets/widgets.css"/>
<style>
  html,body {margin:0;height:100%;overflow:hidden;background:#000;}
  #leafletMap,#cesiumContainer {position:absolute;inset:0;}
  .leaflet-container {background:#000;}
  #mapSelectorWrap {position:absolute;top:10px;left:10px;z-index:1000;}
  #mapSelector {padding:6px 8px;border-radius:8px;font-size:14px;}
  #toast {
    position:fixed;left:50%;transform:translateX(-50%);bottom:20px;
    background:rgba(0,0,0,.8);color:#fff;padding:6px 10px;border-radius:8px;
    font:12px/1.4 system-ui,sans-serif;display:none;z-index:2000;
  }
</style>
</head>
<body>
  <div id="mapSelectorWrap">
    <select id="mapSelector">
      <option value="2d:base">World 2D - Base</option>
      <option value="2d:sat">World 2D - Satellite</option>
      <option value="2d:hyb">World 2D - Hybrid(라벨)</option>
      <option value="3d:world" selected>World 3D - Cesium(기본)</option>
      <option value="3d:vbase">World 3D - VWorld Base</option>
      <option value="3d:vsat">World 3D - VWorld Sat.</option>
      <option value="3d:vhyb">World 3D - VWorld Hybrid</option>
    </select>
  </div>
  <div id="leafletMap" style="display:none;"></div>
  <div id="cesiumContainer"></div>
  <div id="toast"></div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://cesium.com/downloads/cesiumjs/releases/__CESIUM_VER__/Build/Cesium/Cesium.js"></script>
<script>
(function(){
  const ION_TOKEN = __ION_TOKEN__;

  var map2D=null, viewer3D=null;

  function toast(msg){
    var t=document.getElementById('toast');
    if(!t)return; t.innerHTML=msg; t.style.display='block';
    clearTimeout(t._t); t._t=setTimeout(()=>t.style.display='none',3000);
  }

  function url2D(layer,ext){return `/tiles/${layer}/{z}/{x}/{y}.${ext}`;}

  function ensure2D(){
    if(map2D)return;
    map2D=L.map('leafletMap',{center:[37.5665,126.9780],zoom:13,zoomControl:true});
  }

  function set2DLayers(kind){
    ensure2D();
    map2D.eachLayer(l=>map2D.removeLayer(l));
    var opt={minZoom:6,maxZoom:19,attribution:'&copy; VWorld'};
    if(kind==='base'){
      L.tileLayer(url2D('Base','png'),opt).addTo(map2D);
    }else if(kind==='sat'){
      L.tileLayer(url2D('Satellite','jpg'),opt).addTo(map2D);
    }else if(kind==='hyb'){
      L.tileLayer(url2D('Satellite','jpg'),opt).addTo(map2D);
      L.tileLayer(url2D('Hybrid','png'),opt).addTo(map2D);
    }
  }

  function ensure3D(){
    if(viewer3D)return;
    Cesium.Ion.defaultAccessToken = ION_TOKEN;
    viewer3D=new Cesium.Viewer('cesiumContainer',{
      animation:false,timeline:false,geocoder:false,
      baseLayerPicker:false,sceneModePicker:true,
      navigationHelpButton:false,terrain:Cesium.Terrain.fromWorldTerrain()
    });
    viewer3D.camera.flyTo({destination:Cesium.Cartesian3.fromDegrees(126.9780,37.5665,15000)});
  }

  async function set3D(kind){
    ensure3D();
    viewer3D.imageryLayers.removeAll();
    if(kind==='world'){
      try{
        const prov=await Cesium.createWorldImageryAsync({style:Cesium.IonWorldImageryStyle.AERIAL});
        viewer3D.imageryLayers.addImageryProvider(prov);
      }catch(e){
        viewer3D.imageryLayers.addImageryProvider(new Cesium.OpenStreetMapImageryProvider());
      }
      return;
    }
    function vworldProvider(layer,ext){
      return new Cesium.UrlTemplateImageryProvider({
        url:`/tiles/${layer}/{z}/{x}/{y}.${ext}`,
        tilingScheme:new Cesium.WebMercatorTilingScheme(),
        maximumLevel:19,credit:'VWorld'
      });
    }
    if(kind==='vbase'){
      viewer3D.imageryLayers.addImageryProvider(vworldProvider('Base','png'));
    }else if(kind==='vsat'){
      viewer3D.imageryLayers.addImageryProvider(vworldProvider('Satellite','jpg'));
    }else if(kind==='vhyb'){
      viewer3D.imageryLayers.addImageryProvider(vworldProvider('Satellite','jpg'));
      viewer3D.imageryLayers.addImageryProvider(vworldProvider('Hybrid','png'));
    }
  }

  window.switchMap=function(v){
    if(v.startsWith('2d:')){
      document.getElementById('cesiumContainer').style.display='none';
      document.getElementById('leafletMap').style.display='block';
      set2DLayers(v.split(':')[1]);
    }else{
      document.getElementById('leafletMap').style.display='none';
      document.getElementById('cesiumContainer').style.display='block';
      set3D(v.split(':')[1]);
    }
  }

  document.addEventListener('DOMContentLoaded',()=>{
    const sel=document.getElementById('mapSelector');
    if(sel&&window.switchMap){
      window.switchMap(sel.value);
      sel.addEventListener('change',()=>window.switchMap(sel.value));
    }
  });
})();
</script>
</body></html>
"""
    html = TEMPLATE_HTML \
        .replace("__CESIUM_VER__", CESIUM_VER) \
        .replace("__ION_TOKEN__", json.dumps(CESIUM_ION))
    return make_response(html, 200, {"Content-Type": "text/html; charset=utf-8"})
