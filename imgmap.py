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
    html = """
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>3D 지도 (deck.gl + Google Maps)</title>
<script src="https://maps.googleapis.com/maps/api/js?key=__GMAPS_KEY__"></script>
<script src="https://unpkg.com/deck.gl@8.9.35/dist.min.js"></script>
<style>
  html,body,#map{margin:0;padding:0;width:100%;height:100%;background:#000}
  .toolbar{position:absolute;left:10px;top:10px;z-index:2000;background:rgba(0,0,0,.55);color:#cfe8ff;padding:8px 10px;border-radius:10px}
  .toolbar select{background:#132033;color:#cfe8ff;border:1px solid #3a4e7a;border-radius:8px;padding:4px 8px}
  #tooltip{position:absolute;z-index:2500;display:none;pointer-events:none;background:rgba(0,0,0,.8);color:#fff;padding:6px 8px;border-radius:6px;font-size:12px}
  .thumb{width:120px;max-height:90px;object-fit:cover;display:block;margin-top:6px;border-radius:4px}
</style>
</head>
<body>
  <div id="map"></div>
  <div class="toolbar">
    <strong>3D 지도 (deck.gl + Google Maps)</strong>
    <select id="basemap">
      <option value="satellite" selected>Satellite</option>
      <option value="roadmap">Roadmap</option>
      <option value="hybrid">Hybrid</option>
      <option value="terrain">Terrain</option>
    </select>
  </div>
  <div id="tooltip"></div>

<script>
(function(){
  // ✅ IconLayer 사용
  const {GoogleMapsOverlay, IconLayer} = deck;
  // ✅ 아이콘 파일 경로 (PNG 권장, 투명배경)
  const ICON_URL = '/static/icons/camera.png';

  const map = new google.maps.Map(document.getElementById('map'), {
    center:{lat:37.5665,lng:126.9780}, zoom:15, tilt:60, heading:0, mapTypeId:'satellite'
  });
  document.getElementById('basemap').addEventListener('change', e => map.setMapTypeId(e.target.value));

  const overlay = new GoogleMapsOverlay({layers:[]});
  overlay.setMap(map);

  async function loadImageMarkers(){
    try{
      const r = await fetch('/img/api/inbox?limit=200',{cache:'no-store'});
      const j = await r.json();
      if(!j || j.ok === false || !Array.isArray(j.data)) return;

      const items = j.data
        .filter(e => typeof e.lat==='number' && typeof e.lon==='number')
        .map(e => ({
          position:[e.lon, e.lat], name:e.name||'(no name)', note:e.note||'',
          url:e.url||'', ts:e.ts||''
        }));

      // ✅ 픽셀 고정 크기 아이콘 마커
      const imgLayer = new IconLayer({
        id:'img-inbox-layer',
        data: items,
        getIcon: d => ({ url: ICON_URL, width: 64, height: 64, anchorX: 32, anchorY: 64 }),
        // sizeUnits 'pixels' 가 기본 → 줌과 무관하게 픽셀 고정
        //getSize: d => 24,       // 아이콘 표시 크기(px) — 원하면 숫자 조절
        getSize: d => 48,       // 아이콘 표시 크기(px) — 원하면 숫자 조절
        sizeScale: 1,           // 전체 스케일 팩터
        getPosition: d => d.position,
        pickable: true,
        onHover: ({object, x, y}) => {
          const tip = document.getElementById('tooltip');
          if(object){
            tip.style.display = 'block';
            tip.style.left = x + 'px';
            tip.style.top  = y + 'px';
            const imgTag = object.url ? `<img class="thumb" src="${object.url}">` : '';
            tip.innerHTML = `<b>${object.name}</b><br>${object.note}<br><small>${object.ts}</small>${imgTag}`;
          }else{
            tip.style.display = 'none';
          }
        }
      });

      overlay.setProps({layers:[imgLayer]});

      if(items.length){ const [lng,lat] = items[0].position; map.setCenter({lat,lng}); }
    }catch(e){ console.error('loadImageMarkers failed:', e); }
  }

  loadImageMarkers();
  setInterval(loadImageMarkers, 5000);
})();
</script>
</body>
</html>
"""
    html = html.replace("__GMAPS_KEY__", GOOGLE_MAPS_API_KEY)
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp
