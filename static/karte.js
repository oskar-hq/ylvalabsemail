// Karte im CRM: Heatmap der bewerteten Betriebe, Punkte für Betriebe im CRM, Sprung zu den stärksten Gegenden.
(() => {
  const el = document.getElementById('map');
  if (!el || !window.L) return;
  const points = JSON.parse(document.getElementById('mapData').textContent);
  const STAGE_COLORS = { neu: '#8fd0ff', kontaktiert: '#8a929c', antwort: '#5aa9e6', gespraech: '#2f5bd3',
                         angebot: '#f6d36d', kunde: '#1d2126' };

  // Schleswig-Holstein
  const map = L.map(el, { scrollWheelZoom: false });
  const located = points.filter(p => p.passend);
  if (located.length > 1) map.fitBounds(located.map(p => [p.lat, p.lon]), { padding: [30, 30], maxZoom: 11 });
  else map.fitBounds([[53.35, 7.85], [55.10, 11.35]]);  // ganz Schleswig-Holstein
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 18, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);

  const heatOf = (only) => L.heatLayer(
    points.filter(p => !only || p.passend).map(p => [p.lat, p.lon, Math.max(0.15, (p.fit_score || 0) / 100)]),
    { radius: 34, blur: 24, max: 0.6, maxZoom: 10, minOpacity: 0.35,
      gradient: { 0.3: '#d4ecff', 0.55: '#8fd0ff', 0.75: '#5aa9e6', 0.9: '#f6d36d', 1.0: '#e8833a' } });
  const layers = { passend: heatOf(true), alle: heatOf(false) };
  let current = layers.passend.addTo(map);

  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const pipeline = L.layerGroup(points.filter(p => p.stage && STAGE_COLORS[p.stage]).map(p =>
    L.circleMarker([p.lat, p.lon], { radius: 6, color: '#ffffff', weight: 1.5, fillColor: STAGE_COLORS[p.stage], fillOpacity: 1 })
      .bindPopup(`<b>${esc(p.name)}</b><br>${esc(p.category_label)}${p.city ? ' · ' + esc(p.city) : ''}<br>`
        + `${p.fit_score} Punkte<br><a href="/leads/${p.id}">Zur Akte →</a>`))).addTo(map);

  document.querySelectorAll('[data-layer]').forEach(b => b.addEventListener('click', () => {
    document.querySelectorAll('[data-layer]').forEach(x => x.classList.toggle('on', x === b));
    map.removeLayer(current);
    current = layers[b.dataset.layer].addTo(map);
  }));
  document.getElementById('showPipeline')?.addEventListener('change', (e) => {
    if (e.target.checked) pipeline.addTo(map); else map.removeLayer(pipeline);
  });
  // Wachstum je Kreis: Flächen (wenn Grenzen geladen sind), sonst Kreise um die Kreisstadt
  const kreise = JSON.parse(document.getElementById('kreiseData')?.textContent || '[]');
  const geo = JSON.parse(document.getElementById('geoData')?.textContent || '{}');
  const SCALE = ['#e9f4fd', '#bfe2fb', '#8fd0ff', '#5aa9e6', '#f6d36d', '#e8833a'];
  map.createPane('kreise').style.zIndex = 350;  // unter der Heatmap
  let kreisLayer = null;
  function showKreise(kind) {
    if (kreisLayer) { map.removeLayer(kreisLayer); kreisLayer = null; }
    document.querySelector('.kreis-legend')?.toggleAttribute('hidden', !kind);
    if (!kind) return;
    const field = kind + '_1000';
    const vals = kreise.map(k => k[field]).filter(v => v != null).sort((a, b) => a - b);
    if (!vals.length) return;
    const colorOf = (v) => {
      if (v == null) return '#f1f4f7';
      const rank = vals.indexOf(v) / Math.max(1, vals.length - 1);
      return SCALE[Math.min(SCALE.length - 1, Math.floor(rank * SCALE.length))];
    };
    const label = kind === 'bau' ? 'Baugenehmigungen' : 'Gewerbeanmeldungen';
    kreisLayer = L.layerGroup(kreise.map(k => {
      const v = k[field];
      const tip = `<b>${esc(k.name)}</b><br>${label}: ${v == null ? 'keine Zahl' : String(v).replace('.', ',') + ' je 1.000 Einwohner'}`
        + (k[kind + '_rang'] ? `<br>Platz ${k[kind + '_rang']} von ${vals.length}` : '') + `<br>${k.passend} passende Betriebe`;
      const style = { pane: 'kreise', color: '#4a525c', weight: 1, fillColor: colorOf(v), fillOpacity: 0.55 };
      if (geo[k.key]) return L.polygon(geo[k.key].rings, style).bindTooltip(tip, { sticky: true });
      const r = v == null ? 6 : 8 + 22 * (vals.indexOf(v) / Math.max(1, vals.length - 1));
      return L.circleMarker([k.lat, k.lon], { ...style, radius: r, fillOpacity: 0.7 }).bindTooltip(tip);
    })).addTo(map);
  }
  document.querySelectorAll('[data-kreise]').forEach(b => b.addEventListener('click', () => {
    document.querySelectorAll('[data-kreise]').forEach(x => x.classList.toggle('on', x === b));
    showKreise(b.dataset.kreise);
  }));

  document.querySelectorAll('.hs-head').forEach(b => b.addEventListener('click', () => {
    map.flyTo([+b.dataset.lat, +b.dataset.lon], 11, { duration: 0.8 });
    el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }));
})();
