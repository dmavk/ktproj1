
# Final OK !!!!!!!!!!

"""


VWORLD_API_KEY = os.getenv("VWORLD_API_KEY", "4771156FE49F31389913312EBB80BCDB")
CESIUM_ION     = os.getenv("CESIUM_ION_TOKEN", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJqdGkiOiIyYzQ5Y2JkYS05Zjk5LTRhMzMtYWFiMS05ZGIzYTA5OGFhZTQiLCJpZCI6MzQyNjc2LCJpYXQiOjE3NTgyMzg2MDB9.ilgb8Z8rsesnBolJK7gA1XPSVOjRVbDE3QaWGJIBnYM")


"""







# =============== /img-map (VWorld 2D/3D + Cesium) ===============
# 필요한 import (중복되면 생략해도 OK)
import os, math, requests
from flask import make_response, jsonify, Response, request
from flask import Blueprint, make_response
import logging
import os, math, requests, json, logging  # ← json 추가
from flask import Blueprint, current_app, request, make_response, jsonify


# 1) 블루프린트 생성 (url_prefix는 필요 없으면 생략)
imgbp = Blueprint("imgbp", __name__)
log = logging.getLogger(__name__)     # ← 모듈 전용 로거 생성

# ---- 환경 변수
VWORLD_API_KEY = os.getenv("VWORLD_API_KEY", "")
VWORLD_API_KEY = os.getenv("VWORLD_API_KEY", "4771156FE49F31389913312EBB80BCDB")
VWORLD_REFERER = os.getenv("VWORLD_REFERER", "http://localhost")
VWORLD_HOST    = os.getenv("VWORLD_HOST", "https://api.vworld.kr")
CESIUM_ION     = os.getenv("CESIUM_ION_TOKEN", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJqdGkiOiIyYzQ5Y2JkYS05Zjk5LTRhMzMtYWFiMS05ZGIzYTA5OGFhZTQiLCJpZCI6MzQyNjc2LCJpYXQiOjE3NTgyMzg2MDB9.ilgb8Z8rsesnBolJK7gA1XPSVOjRVbDE3QaWGJIBnYM")

CESIUM_VER     = os.getenv("CESIUM_VER", "1.113")

# 진단 기본점(서울 시청)
SEOUL = (126.9780, 37.5665)
DEFAULT_ZOOM = 13

def zxy_from_lonlat(lon, lat, z):
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return x, y

def _http_ok_image(r):
    return r.ok and (r.headers.get("Content-Type","").startswith("image/"))

def _req(url, timeout=8, headers=None):
    h = {"User-Agent":"ImgMimgbproxy/1.0","Accept":"*/*"}
    if headers: h.update(headers)
    return requests.get(url, timeout=timeout, headers=h)

# ---- 1) WMTS 타일 z/x/y 패턴 자동 탐지
def _detect_wmts_pattern():
    if not VWORLD_API_KEY:
        return None
    z = DEFAULT_ZOOM
    x, y = zxy_from_lonlat(*SEOUL, z)
    y_tms = (1<<z) - 1 - y
    patterns = [
        ("zxy",    f"{VWORLD_HOST}/req/wmts/1.0.0/{VWORLD_API_KEY}/Base/default/GoogleMapsCompatible/{z}/{x}/{y}.png"),
        ("zyx",    f"{VWORLD_HOST}/req/wmts/1.0.0/{VWORLD_API_KEY}/Base/default/GoogleMapsCompatible/{z}/{y}/{x}.png"),
        ("zxtms",  f"{VWORLD_HOST}/req/wmts/1.0.0/{VWORLD_API_KEY}/Base/default/GoogleMapsCompatible/{z}/{x}/{y_tms}.png"),
        ("zyxtms", f"{VWORLD_HOST}/req/wmts/1.0.0/{VWORLD_API_KEY}/Base/default/GoogleMapsCompatible/{z}/{y_tms}/{x}.png"),
    ]
    for name, u in patterns:
        try:
            r = _req(u, headers={"Referer":VWORLD_REFERER})
            if _http_ok_image(r):
                return name
        except requests.RequestException:
            pass
    return None

WMTS_MODE = _detect_wmts_pattern()
log.info("WMTS_MODE = %s", WMTS_MODE or "None (will try xdworld fallback)")

# ---- 2) xdworld 공개 타일 후보 자동 탐색
XDWORLD_VERS = os.getenv("XDWORLD_VERSIONS","202307,202112,202009,202002,201912,201802,201512").split(",")
_LAYER_EXTS = {"Base":["png"], "Hybrid":["png"], "Satellite":["jpg","jpeg"]}

def _find_best_xd_variant(layer:str):
    z = DEFAULT_ZOOM
    x, y = zxy_from_lonlat(*SEOUL, z)
    for ver in XDWORLD_VERS:
        for ext in _LAYER_EXTS[layer]:
            url = f"http://xdworld.vworld.kr:8080/2d/{layer}/{ver}/{z}/{x}/{y}.{ext}"
            try:
                r = _req(url, timeout=6)
                if _http_ok_image(r):
                    return {"version":ver, "ext":ext}
            except requests.RequestException:
                pass
    return None

XDWORLD_BEST = {
    "Base":      _find_best_xd_variant("Base"),
    "Satellite": _find_best_xd_variant("Satellite"),
    "Hybrid":    _find_best_xd_variant("Hybrid"),
}
log.info("XDWORLD_BEST = %s", XDWORLD_BEST)

# ---- 3) 프록시 유틸
def _wmts_url(layer, z, x, y, ext, mode):
    if mode == "zxy":   return f"{VWORLD_HOST}/req/wmts/1.0.0/{VWORLD_API_KEY}/{layer}/default/GoogleMapsCompatible/{z}/{x}/{y}.{ext}"
    if mode == "zyx":   return f"{VWORLD_HOST}/req/wmts/1.0.0/{VWORLD_API_KEY}/{layer}/default/GoogleMapsCompatible/{z}/{y}/{x}.{ext}"
    y_tms = (1<<z) - 1 - y
    if mode == "zxtms": return f"{VWORLD_HOST}/req/wmts/1.0.0/{VWORLD_API_KEY}/{layer}/default/GoogleMapsCompatible/{z}/{x}/{y_tms}.{ext}"
    # zyxtms
    return f"{VWORLD_HOST}/req/wmts/1.0.0/{VWORLD_API_KEY}/{layer}/default/GoogleMapsCompatible/{z}/{((1<<z)-1-y)}/{x}.{ext}"

def _respond_image(r, tag):
    resp = make_response(r.content, 200)
    resp.headers["Content-Type"]  = r.headers.get("Content-Type","image/png")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    resp.headers["X-Source"]      = tag
    return resp

def _respond_fail(r, tag="fail"):
    code = r.status_code if r is not None else 502
    body = r.content if r is not None else b""
    resp = make_response(body, code)
    resp.headers["Content-Type"]  = (r.headers.get("Content-Type") if (r is not None) else "text/plain")
    resp.headers["X-Source"]      = tag
    return resp

def _try_xdworld_all(layer, z, x, y):
    # 1) 사전 탐색 결과 우선
    first = XDWORLD_BEST.get(layer)
    tried = set()
    if first:
        u = f"http://xdworld.vworld.kr:8080/2d/{layer}/{first['version']}/{z}/{x}/{y}.{first['ext']}"
        r = _req(u, timeout=6)
        if _http_ok_image(r):
            return _respond_image(r, f"xdworld:{first['version']}.{first['ext']}")
        tried.add((first["version"], first["ext"]))
    # 2) 전체 조합
    last = None
    for ver in XDWORLD_VERS:
        for ext in _LAYER_EXTS[layer]:
            if (ver, ext) in tried:
                continue
            u = f"http://xdworld.vworld.kr:8080/2d/{layer}/{ver}/{z}/{x}/{y}.{ext}"
            try:
                r = _req(u, timeout=6)
                last = r
                if _http_ok_image(r):
                    return _respond_image(r, f"xdworld:{ver}.{ext}")
            except requests.RequestException:
                pass
    return _respond_fail(last, "xdworld:all_failed")

# ---- 4) 타일 프록시 라우트
@imgbp.route("/tiles/<layer>/<int:z>/<int:x>/<int:y>.<ext>")
def vworld_tiles(layer, z, x, y, ext):
    layer = layer.capitalize()  # base→Base
    # 1) WMTS (정식 키 보유 시)
    if WMTS_MODE and VWORLD_API_KEY:
        try:
            u = _wmts_url(layer, z, x, y, ext, WMTS_MODE)
            r = _req(u, timeout=8, headers={"Referer": VWORLD_REFERER})
            if _http_ok_image(r):
                return _respond_image(r, f"wmts:{WMTS_MODE}")
        except requests.RequestException:
            pass
    # 2) 공개타일 폴백
    res = _try_xdworld_all(layer, z, x, y)
    if res.status_code < 400:
        return res
    # 3) 최종 폴백: Base로 대체
    if layer != "Base":
        base_res = _try_xdworld_all("Base", z, x, y)
        base_res.headers["X-Source"] = base_res.headers.get("X-Source","") + " -> fallback(Base)"
        return base_res
    return res

# ---- 5) 진단 라우트
@imgbp.get("/diag")
def imgmap_diag():
    return jsonify({
        "WMTS_MODE": WMTS_MODE,
        "XDWORLD_BEST": XDWORLD_BEST,
        "XDWORLD_VERS_TRIED": XDWORLD_VERS,
        "REFERER": VWORLD_REFERER,
        "VWORLD_API_KEY_SET": bool(VWORLD_API_KEY),
        "CESIUM_VER": CESIUM_VER,
        "ION_SET": bool(CESIUM_ION),
    })

# ---- 6) /img-map: Leaflet(2D) + Cesium(3D) UI
@imgbp.get("/img-map")
def img_map_page():
    ION = CESIUM_ION or ""
    html = """
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>3D 이미지 지도</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<link rel="stylesheet" href="https://cesium.com/downloads/cesiumjs/releases/__CESIUM_VER__/Build/Cesium/Widgets/widgets.css"/>
<style>
  html,body{height:100%;margin:0;background:#000;color:#cfe8ff;font-family:system-ui,Arial}
  .top{position:fixed;left:10px;top:10px;z-index:10;background:rgba(0,0,0,.35);
        padding:8px 10px;border-radius:10px}
  select{background:#132033;color:#cfe8ff;border:1px solid #3a4e7a;border-radius:8px;padding:6px 10px}
  #leaf{position:absolute;inset:0}
  #czm{position:absolute;inset:0;display:none}
  #toast{position:fixed;left:50%;transform:translateX(-50%);bottom:18px;background:rgba(0,0,0,.8);
          color:#fff;padding:6px 10px;border-radius:8px;font:12px/1.4 system-ui,sans-serif;display:none;z-index:2000}
</style>
</head>
<body>
  <div class="top">
    <select id="sel">
      <option value="2d:base">World 2D - Base</option>
      <option value="2d:sat">World 2D - Satellite</option>
      <option value="2d:hyb">World 2D - Hybrid(라벨)</option>
      <option value="3d:world">World 3D - Cesium(기본)</option>
      <option value="3d:vbase">World 3D - VWorld Base</option>
      <option value="3d:vsat">World 3D - VWorld Sat.</option>
      <option value="3d:vhyb">World 3D - VWorld Hybrid</option>
    </select>
    __ION_BADGE__
  </div>
  <div id="leaf"></div>
  <div id="czm"></div>
  <div id="toast"></div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script src="https://cesium.com/downloads/cesiumjs/releases/__CESIUM_VER__/Build/Cesium/Cesium.js"></script>
  <script>
  (function(){
    const ION_TOKEN = __ION_TOKEN__;
    var map2D=null, base2D=null, label2D=null;
    var viewer3D=null;

    function toast(msg){var t=document.getElementById('toast');t.innerHTML=msg;t.style.display='block';clearTimeout(t._t);t._t=setTimeout(()=>t.style.display='none',3000);}

    function lonLatToTile(lon, lat, z){
      var n = Math.pow(2,z);
      var x = Math.floor((lon + 180.0) / 360.0 * n);
      var latRad = lat * Math.PI / 180.0;
      var y = Math.floor((1.0 - Math.log(Math.tan(latRad) + 1/Math.cos(latRad)) / Math.PI) / 2.0 * n);
      return {x:x, y:y};
    }

    function urlTile(layer, ext){ return `/tiles/${layer}/{z}/{x}/{y}.${ext}`; }

    // ---------- 2D ----------
    function ensure2D(){
      if(!map2D){
        map2D = L.map('leaf',{center:[37.5665,126.9780], zoom:13, fadeAnimation:false});
      }else{
        setTimeout(()=>{ try{ map2D.invalidateSize(); }catch(_ ){} }, 0);
      }
    }
    function set2DLayers(kind){
      ensure2D();
      if (base2D)  { map2D.removeLayer(base2D);  base2D=null; }
      if (label2D) { map2D.removeLayer(label2D); label2D=null; }
      var opts={ minZoom:6, maxZoom:19, attribution:'&copy; VWorld' };
      if(kind==='base'){ base2D=L.tileLayer(urlTile('Base','png'),opts).addTo(map2D); }
      if(kind==='sat'){  base2D=L.tileLayer(urlTile('Satellite','jpg'),opts).addTo(map2D); }
      if(kind==='hyb'){  base2D=L.tileLayer(urlTile('Satellite','jpg'),opts).addTo(map2D);
                         label2D=L.tileLayer(urlTile('Hybrid','png'),opts).addTo(map2D); }
      // 샘플 HEAD로 소스 토스트
      var c=map2D.getCenter(), z=map2D.getZoom(); var t=lonLatToTile(c.lng,c.lat,z);
      var layer=(kind==='base'?'Base':(kind==='sat'?'Satellite':'Hybrid'));
      var ext=(layer==='Base' || layer==='Hybrid')?'png':'jpg';
      var sample=`/tiles/${layer}/${z}/${t.x}/${t.y}.${ext}`;
      fetch(sample,{method:'HEAD'}).then(r => {
        var src=r.headers.get('X-Source')||''; if(src) toast('2D Source: '+src);
      }).catch(()=>{});
    }

    // ---------- 3D ----------
    function ensure3D(){
      if(viewer3D) return;
      Cesium.Ion.defaultAccessToken = ION_TOKEN || "";
      viewer3D = new Cesium.Viewer('czm', {
        animation:false, timeline:false, geocoder:false, baseLayerPicker:false, sceneModePicker:true, navigationHelpButton:false,
        terrain: Cesium.Terrain.fromWorldTerrain()
      });
      viewer3D.camera.flyTo({ destination: Cesium.Cartesian3.fromDegrees(126.9780, 37.5665, 15000) });
    }
    function vworldProvider3D(layer, ext){
      return new Cesium.UrlTemplateImageryProvider({
        url: urlTile(layer, ext),
        tilingScheme: new Cesium.WebMercatorTilingScheme(),
        maximumLevel: 19,
        credit: "VWorld"
      });
    }
    async function set3D(kind){
      ensure3D();
      try{ viewer3D.imageryLayers.removeAll(); }catch(e){}
      if(kind==='world'){
        try{
          if(ION_TOKEN){
            const prov = await Cesium.createWorldImageryAsync({style: Cesium.IonWorldImageryStyle.AERIAL});
            viewer3D.imageryLayers.addImageryProvider(prov);
          } else {
            viewer3D.imageryLayers.addImageryProvider(new Cesium.OpenStreetMapImageryProvider());
          }
        }catch(e){
          viewer3D.imageryLayers.addImageryProvider(new Cesium.OpenStreetMapImageryProvider());
        }
        return;
      }
      if(kind==='vbase') viewer3D.imageryLayers.addImageryProvider(vworldProvider3D('Base','png'));
      if(kind==='vsat')  viewer3D.imageryLayers.addImageryProvider(vworldProvider3D('Satellite','jpg'));
      if(kind==='vhyb'){ viewer3D.imageryLayers.addImageryProvider(vworldProvider3D('Satellite','jpg'));
                         viewer3D.imageryLayers.addImageryProvider(vworldProvider3D('Hybrid','png')); }
      // 소스 토스트
      var z=13, t=lonLatToTile(126.9780,37.5665,z);
      var layer=(kind==='vbase'?'Base':(kind==='vsat'?'Satellite':'Hybrid'));
      var ext=(layer==='Base' || layer==='Hybrid')?'png':'jpg';
      var sample=`/tiles/${layer}/${z}/${t.x}/${t.y}.${ext}`;
      fetch(sample,{method:'HEAD'}).then(r=>{ var src=r.headers.get('X-Source')||''; if(src) toast('3D Source: '+src); }).catch(()=>{});
      viewer3D.scene.requestRender();
    }

    // ---------- 뷰 전환 ----------
    function show2D(){
      document.getElementById('czm').style.display='none';
      document.getElementById('leaf').style.display='block';
      ensure2D();
    }
    function show3D(){
      try {
        if(map2D){ map2D.eachLayer(l=>map2D.removeLayer(l)); map2D.off(); map2D.remove(); map2D=null; base2D=null; label2D=null; }
      } catch(_){}
      document.getElementById('leaf').style.display='none';
      document.getElementById('czm').style.display='block';
    }

    // 부팅: 2D Base
    document.addEventListener('DOMContentLoaded', function(){
      show2D(); set2DLayers('base');
      var sel=document.getElementById('sel');
      sel.addEventListener('change', async function(){
        var v=this.value;
        if(v.startsWith('2d:')){ show2D(); set2DLayers(v.split(':')[1]); }
        else { show3D(); await set3D(v.split(':')[1]); }
      });
    });
  })();
  </script>
</body>
</html>
"""
    # 토큰/버전 치환
    ion_badge = "" if ION else "<span style='color:#ff8080;margin-left:8px'>Ion 토큰 없음 → OSM로 폴백</span>"
    html = (html
            .replace("__CESIUM_VER__", CESIUM_VER)
            .replace("__ION_TOKEN__", json.dumps(ION))
            .replace("__ION_BADGE__", ion_badge))
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp


# ================== /img-map 끝 ==================
