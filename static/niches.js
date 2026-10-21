// Nischen wählen und die 100 % verteilen. Ohne eigene Verteilung: gleichmäßig. Ein Regler verschiebt die anderen
// im Verhältnis mit, sodass die Summe immer genau 100 % bleibt (jede Nische mindestens 1 %).
(() => {
  const form = document.getElementById('nicheForm');
  if (!form) return;
  const boxes = [...form.querySelectorAll('input[name=niche]')];
  const customEl = document.getElementById('nicheCustom');
  const dlg = document.getElementById('weightDlg');
  const rowsEl = document.getElementById('weightRows');
  let weights = JSON.parse(form.dataset.weights || '{}');
  let custom = !!form.dataset.custom;

  const chosen = () => boxes.filter((b) => b.checked).map((b) => b.value);
  const label = (k) => boxes.find((b) => b.value === k).dataset.label;

  function equal(keys) {
    const out = {};
    const base = Math.floor(100 / keys.length), rest = 100 - base * keys.length;
    keys.forEach((k, i) => { out[k] = base + (i < rest ? 1 : 0); });
    return out;
  }

  // Rest (100 - v) auf die anderen verteilen, im Verhältnis ihrer bisherigen Werte, ganze Zahlen, mindestens 1
  function rebalance(key, v) {
    const keys = Object.keys(weights), others = keys.filter((k) => k !== key);
    if (!others.length) { weights[key] = 100; return; }
    v = Math.max(1, Math.min(100 - others.length, v));
    const rest = 100 - v;
    const sum = others.reduce((s, k) => s + weights[k], 0) || others.length;
    const raw = others.map((k) => ({ k, x: rest * (weights[k] || 1) / sum }));
    raw.forEach((r) => { r.n = Math.max(1, Math.floor(r.x)); });
    let diff = rest - raw.reduce((s, r) => s + r.n, 0);
    raw.sort((a, b) => (b.x - b.n) - (a.x - a.n));
    for (let i = 0; diff > 0; i = (i + 1) % raw.length) { raw[i].n += 1; diff -= 1; }
    raw.sort((a, b) => b.n - a.n);
    for (let i = 0, guard = 0; diff < 0 && guard < 10000; i = (i + 1) % raw.length, guard += 1) {
      if (raw[i].n > 1) { raw[i].n -= 1; diff += 1; }
    }
    weights[key] = v;
    raw.forEach((r) => { weights[r.k] = r.n; });
  }

  function syncSelection() {
    const keys = chosen();
    const same = keys.length === Object.keys(weights).length && keys.every((k) => k in weights);
    if (!same) { weights = keys.length ? equal(keys) : {}; custom = false; }
    render();
  }

  function render() {
    const keys = chosen();
    document.getElementById('nicheCount').textContent = keys.length;
    const top = Object.entries(weights).sort((a, b) => b[1] - a[1]);
    document.getElementById('nicheSplit').textContent = !keys.length ? 'bitte mindestens eine wählen'
      : (custom ? '' : 'gleichmäßig · ') + top.slice(0, 3).map(([k, w]) => `${label(k)} ${w} %`).join(', ')
        + (top.length > 3 ? ` und ${top.length - 3} weitere` : '');
    document.getElementById('weightOpen').disabled = keys.length < 2;
    customEl.value = custom ? '1' : '';
    form.querySelectorAll('input[data-weight]').forEach((el) => el.remove());
    Object.entries(weights).forEach(([k, w]) => {
      const el = Object.assign(document.createElement('input'), { type: 'hidden', name: `w_${k}`, value: w });
      el.dataset.weight = '1';
      form.appendChild(el);
    });
  }

  function renderRows() {
    rowsEl.innerHTML = '';
    const max = 100 - (Object.keys(weights).length - 1);
    Object.keys(weights).forEach((k) => {
      const row = document.createElement('div');
      row.className = 'weight-row';
      row.innerHTML = `<span class="weight-name"></span>
        <input type="range" min="1" max="${max}" step="1" aria-label="">
        <span class="weight-val"><input type="number" min="1" max="${max}" inputmode="numeric"><em>%</em></span>`;
      row.querySelector('.weight-name').textContent = label(k);
      row.querySelector('input[type=range]').setAttribute('aria-label', `Anteil ${label(k)}`);
      row.dataset.key = k;
      rowsEl.appendChild(row);
    });
    paintRows();
  }

  function paintRows() {
    rowsEl.querySelectorAll('.weight-row').forEach((row) => {
      const w = weights[row.dataset.key];
      row.querySelector('input[type=range]').value = w;
      const num = row.querySelector('input[type=number]');
      if (document.activeElement !== num) num.value = w;
      row.style.setProperty('--w', `${w}%`);
    });
    const total = Object.values(weights).reduce((s, w) => s + w, 0);
    document.getElementById('weightTotal').textContent = `${total} %`;
  }

  rowsEl.addEventListener('input', (e) => {
    const row = e.target.closest('.weight-row');
    if (!row) return;
    const v = parseInt(e.target.value, 10);
    if (Number.isNaN(v)) return;
    rebalance(row.dataset.key, v);
    custom = true;
    paintRows();
    render();
  });
  rowsEl.addEventListener('change', (e) => { if (e.target.type === 'number') { e.target.blur(); paintRows(); } });

  document.getElementById('weightOpen').addEventListener('click', () => { renderRows(); dlg.showModal(); });
  document.getElementById('weightDone').addEventListener('click', () => dlg.close());
  document.getElementById('weightEqual').addEventListener('click', () => {
    weights = equal(Object.keys(weights)); custom = false; paintRows(); render();
  });

  boxes.forEach((b) => b.addEventListener('change', syncSelection));
  form.querySelectorAll('[data-all]').forEach((btn) => btn.addEventListener('click', () => {
    const group = form.querySelector(`.niche-group[data-group="${btn.dataset.all}"]`);
    const inGroup = [...group.querySelectorAll('input[name=niche]')];
    const on = !inGroup.every((b) => b.checked);
    inGroup.forEach((b) => { b.checked = on; });
    syncSelection();
  }));
  document.getElementById('nicheNone').addEventListener('click', () => { boxes.forEach((b) => { b.checked = false; }); syncSelection(); });
  const def = document.getElementById('nicheDefault');
  def.addEventListener('click', () => {
    const keys = JSON.parse(def.dataset.keys);
    boxes.forEach((b) => { b.checked = keys.includes(b.value); });
    syncSelection();
  });

  const filter = document.getElementById('nicheFilter');
  filter.addEventListener('input', () => {
    const q = filter.value.trim().toLowerCase();
    let any = false;
    form.querySelectorAll('.niche-group').forEach((g) => {
      let shown = 0;
      g.querySelectorAll('.niche-chip').forEach((chip) => {
        const hit = !q || chip.dataset.label.includes(q) || g.querySelector('h2').textContent.toLowerCase().includes(q);
        chip.hidden = !hit; shown += hit ? 1 : 0;
      });
      g.hidden = !shown; any = any || shown > 0;
    });
    document.getElementById('nicheEmpty').hidden = any;
  });

  form.addEventListener('submit', (e) => {
    if (!chosen().length) { e.preventDefault(); alert('Bitte mindestens eine Nische wählen.'); }
  });
  render();
})();
