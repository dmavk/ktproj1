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
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>3D 지도 (deck.gl + Google Maps)</title>

<!-- Google Maps JS -->
<script src="https://maps.googleapis.com/maps/api/js?key=__GMAPS_KEY__"></script>
<!-- deck.gl (standalone UMD) -->
<script src="https://unpkg.com/deck.gl@8.9.35/dist.min.js"></script>

<style>
  html, body, #map {
    margin: 0; padding: 0; width: 100%; height: 100%;
    background: #000; overflow: hidden;
    font-family: system-ui, -apple-system, Segoe UI, Roboto, "Noto Sans KR", Arial, sans-serif;
  }
  .toolbar {
    position: absolute; left: 10px; top: 10px; z-index: 2000;
    background: rgba(0,0,0,.55); color: #cfe8ff; padding: 8px 10px;
    border-radius: 10px; display: flex; align-items: center; gap: 8px;
  }
  .toolbar h3 { margin: 0; font-size: 14px; font-weight: 800; }
  .toolbar select {
    background:#132033; color:#cfe8ff; border:1px solid #3a4e7a; border-radius:8px; padding:4px 8px;
  }
  #tooltip {
    position: absolute; z-index: 2500; display: none; pointer-events: none;
    background: rgba(0,0,0,.8); color: #fff; padding: 6px 8px; border-radius: 6px; font-size: 12px;
    box-shadow: 0 4px 12px rgba(0,0,0,.4);
  }
  #badge {
    position: absolute; right: 12px; top: 12px; z-index: 2000;
    color: #ffad66; font-size: 12px; background: rgba(0,0,0,.5); padding:6px 8px; border-radius: 8px;
  }
  .thumb { width: 120px; max-height: 90px; object-fit: cover; display:block; margin-top:6px; border-radius:4px; }
</style>
</head>
<body>
  <div id="map"></div>
  <div class="toolbar">
    <h3>3D 지도 (deck.gl + Google Maps)</h3>
    <select id="basemap">
      <option value="satellite" selected>Satellite (위성)</option>
      <option value="roadmap">Roadmap (일반)</option>
      <option value="hybrid">Hybrid (하이브리드)</option>
      <option value="terrain">Terrain (지형)</option>
    </select>
  </div>
  <div id="badge" style="display:none">Google Maps API 키가 설정되지 않았습니다</div>
  <div id="tooltip"></div>

<script>
(function(){
  // UMD 전역에서 가져오기
  const {GoogleMapsOverlay, ScatterplotLayer} = deck;

  // API 키 점검 배지
  const keyMissing = "__GMAPS_KEY__" === "YOUR_GOOGLE_MAPS_API_KEY";
  if (keyMissing) document.getElementById('badge').style.display = 'block';

  // 기본 지도 생성 (위성 + 60도 틸트)
  const map = new google.maps.Map(document.getElementById('map'), {
    center: { lat: 37.5665, lng: 126.9780 },
    zoom: 15,
    tilt: 60,
    heading: 0,
    mapTypeId: 'satellite',
    // 신규 벡터맵을 쓰고 싶다면 mapId 사용(콘솔에서 활성화 필요)
    // mapId: '4504f8b37365c3d0'
  });

  // 베이스맵 셀렉터
  document.getElementById('basemap').addEventListener('change', (e) => {
    map.setMapTypeId(e.target.value);
  });

  // deck.gl Overlay
  // (초기에는 빈 레이어 → 이후 setProps로 교체)
  const overlay = new GoogleMapsOverlay({ layers: [] });
  overlay.setMap(map);

  // ===== 이미지 인박스 포인트 레이어 =====
async function loadImageMarkers(){
  try {
    const r = await fetch('/img/api/inbox?limit=200', { cache: 'no-store' });
    const j = await r.json();
    console.log("inbox data:", j);

    if (!j || j.ok === false || !Array.isArray(j.data)) {
      console.warn('Unexpected /img/api/inbox payload', j);
      return;
    }

    const items = j.data
      .filter(e => typeof e.lat === 'number' && typeof e.lon === 'number')
      .map(e => ({
        position: [e.lon, e.lat],
        name: e.name || '(no name)',
        note: e.note || '',
        url: e.url || '',
        ts:  e.ts  || ''
      }));

    console.log("parsed items:", items);

    const imgLayer = new ScatterplotLayer({
      id: 'img-inbox-layer',
      data: items,
      getPosition: d => d.position,
      getRadius: 60,
      radiusScale: 3,
      getFillColor: [255, 120, 0, 220],
      pickable: true,
      onHover: ({object, x, y}) => {
        const tip = document.getElementById('tooltip');
        if (object) {
          tip.style.display = 'block';
          tip.style.left = x + 'px';
          tip.style.top  = y + 'px';
          const imgTag = object.url ? `<img class="thumb" src="${object.url}">` : '';
          tip.innerHTML = `
            <b>${object.name}</b><br>
            ${object.note}<br>
            <small>${object.ts}</small>
            ${imgTag}
          `;
        } else {
          tip.style.display = 'none';
        }
      }
    });

    overlay.setProps({ layers: [imgLayer] });

    if (items.length > 0) {
      const first = items[0];
      map.setCenter({ lat: first.position[1], lng: first.position[0] });
    }

  } catch (err) {
    console.error('loadImageMarkers failed:', err);
  }
}

  // 최초 로드 + 주기 갱신(30초)
  loadImageMarkers();
  setInterval(loadImageMarkers, 30000);

})();
</script>
</body>
</html>
"""
    html = html.replace("__GMAPS_KEY__", GOOGLE_MAPS_API_KEY)
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp