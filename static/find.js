// Schnellsuche: ⌘K oder Strg+K (oder „/“) öffnet, Tippen filtert, Enter springt zum ersten (oder gewählten) Treffer.
(() => {
  const dlg = document.getElementById('findDlg');
  if (!dlg) return;
  const input = document.getElementById('findInput');
  const list = document.getElementById('findList');
  const mac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
  document.getElementById('findKey').textContent = mac ? '⌘K' : 'Strg K';
  let pages = null, items = [], active = 0, timer = null, seq = 0;

  const norm = (s) => (s || '').toLowerCase().replace(/ä/g, 'ae').replace(/ö/g, 'oe').replace(/ü/g, 'ue').replace(/ß/g, 'ss');

  function score(p, words) {
    const title = norm(p.t), all = `${title} ${norm(p.s)} ${norm(p.k)}`;
    let total = 0;
    for (const w of words) {
      if (!all.includes(w)) return 0;
      if (title.startsWith(w)) total += 30;
      else if (title.split(/[\s·-]+/).some((t) => t.startsWith(w))) total += 20;
      else if (title.includes(w)) total += 12;
      else total += 4;
    }
    return total - title.length / 100;
  }

  function show(leads) {
    const q = norm(input.value.trim());
    const words = q.split(/\s+/).filter(Boolean);
    let found = (pages || []).map((p) => [score(p, words), p]);
    found = words.length ? found.filter(([s]) => s > 0).sort((a, b) => b[0] - a[0]) : found.filter(([, p]) => !p.theme);
    items = found.slice(0, words.length ? 8 : 12).map(([, p]) => p).concat(leads || []);
    active = 0;
    list.innerHTML = '';
    if (!items.length) {
      const li = document.createElement('li');
      li.className = 'find-none';
      li.textContent = q ? 'Nichts gefunden.' : 'Lädt …';
      list.appendChild(li);
      return;
    }
    items.forEach((it, i) => {
      const li = document.createElement('li');
      li.role = 'option';
      li.id = `find-${i}`;
      li.innerHTML = '<b></b><span></span>';
      li.querySelector('b').textContent = it.t;
      li.querySelector('span').textContent = it.s;
      li.addEventListener('mousemove', () => mark(i));
      li.addEventListener('click', () => go(i));
      list.appendChild(li);
    });
    mark(0);
  }

  function mark(i) {
    active = i;
    [...list.children].forEach((li, j) => li.setAttribute('aria-selected', j === i ? 'true' : 'false'));
    input.setAttribute('aria-activedescendant', `find-${i}`);
    list.children[i]?.scrollIntoView({ block: 'nearest' });
  }

  function go(i) {
    const it = items[i];
    if (!it) return;
    if (it.theme) { document.querySelector(`[data-theme-set="${it.theme}"]`)?.click(); dlg.close(); return; }
    dlg.close();
    window.location.href = it.u;
  }

  async function open() {
    if (dlg.open) { input.select(); return; }
    input.value = '';
    dlg.showModal();
    input.focus();
    if (!pages) {
      try { pages = (await (await fetch('/suche.json', { headers: { Accept: 'application/json' } })).json()).pages; }
      catch (e) { pages = []; }
    }
    show();
  }

  input.addEventListener('input', () => {
    show();
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) return;
    const mine = ++seq;
    timer = setTimeout(async () => {
      try {
        const r = await (await fetch(`/suche.json?q=${encodeURIComponent(q)}`, { headers: { Accept: 'application/json' } })).json();
        if (mine === seq && input.value.trim() === q) show(r.leads);
      } catch (e) { /* Betriebe sind ein Zusatz, Seiten gehen auch ohne */ }
    }, 160);
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); if (items.length) mark((active + 1) % items.length); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); if (items.length) mark((active - 1 + items.length) % items.length); }
    else if (e.key === 'Enter') { e.preventDefault(); go(active); }
  });
  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
  document.getElementById('findOpen').addEventListener('click', open);
  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); open(); return; }
    if (e.key === '/' && !e.target.closest('input,textarea,select,[contenteditable]') && !document.querySelector('dialog[open]')) {
      e.preventDefault(); open();
    }
  });
})();
