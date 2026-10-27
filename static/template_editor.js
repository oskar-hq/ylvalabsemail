// Eigene Vorlage: Betreff, Anrede und Text bearbeiten, Platzhalter einfügen, Design wählen, Vorschau sofort.
// Am Handy wechselt oben „Bearbeiten | Vorschau“, am großen Bildschirm steht beides nebeneinander.
(() => {
  const root = document.getElementById('tplEditor');
  if (!root) return;
  const csrf = document.querySelector('meta[name=csrf-token]').content;
  const $ = (id) => document.getElementById(id);
  const form = $('tplForm');

  // Gespeicherter Text „Betreff: …\nAnrede: …\n\nText“ in die drei Felder aufteilen
  const lines = $('tplText').value.split('\n');
  let subject = '', greeting = '';
  while (lines.length && (!lines[0].trim() || /^(betreff|anrede):/i.test(lines[0]))) {
    const m = lines.shift().match(/^(betreff|anrede):\s*(.*)$/i);
    if (m && m[1].toLowerCase() === 'betreff') subject = m[2]; else if (m) greeting = m[2];
  }
  $('tplSubject').value = subject; $('tplGreeting').value = greeting; $('tplBody').value = lines.join('\n').trim();
  const compose = () => `Betreff: ${$('tplSubject').value.trim()}\nAnrede: ${$('tplGreeting').value.trim()}\n\n${$('tplBody').value.trim()}\n`;

  let timer = null, lastFocus = $('tplBody'), dirty = false;
  function refresh() {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      $('tplText').value = compose();
      const resp = await fetch('/vorlage/vorschau', { method: 'POST', body: new FormData(form), headers: { 'X-CSRF-Token': csrf } });
      if (resp.ok) $('tplFrame').srcdoc = await resp.text();
    }, 250);
  }
  const changed = () => { dirty = true; $('tplMsg').textContent = 'Nicht gespeichert'; refresh(); };
  ['tplSubject', 'tplGreeting', 'tplBody'].forEach((id) => {
    $(id).addEventListener('input', changed);
    $(id).addEventListener('focus', () => { lastFocus = $(id); });
  });
  form.querySelector('input[name=gruss]').addEventListener('input', changed);
  $('tplExample').addEventListener('change', refresh);

  // Platzhalter an der Cursor-Stelle einfügen
  root.querySelectorAll('[data-ph]').forEach((b) => b.addEventListener('click', () => {
    const el = lastFocus, ph = b.dataset.ph;
    const start = el.selectionStart ?? el.value.length, end = el.selectionEnd ?? el.value.length;
    el.value = el.value.slice(0, start) + ph + el.value.slice(end);
    el.focus(); el.selectionStart = el.selectionEnd = start + ph.length;
    changed();
  }));

  // Design
  root.querySelectorAll('[data-tpl]').forEach((b) => b.addEventListener('click', () => {
    root.querySelectorAll('[data-tpl]').forEach((x) => x.classList.toggle('on', x === b));
    $('tplTemplate').value = b.dataset.tpl; changed();
  }));
  $('tplGraphic').addEventListener('change', () => {
    $('tplIcon').hidden = $('tplGraphic').value !== 'icon'; $('tplSeed').hidden = $('tplGraphic').value !== 'band'; changed();
  });
  $('tplIcon').addEventListener('change', changed);
  $('tplSeed').addEventListener('click', () => { $('tplSeedInput').value = String(1 + Math.floor(Math.random() * 50)); changed(); });

  // Handy: Bearbeiten | Vorschau
  root.querySelectorAll('.tpl-switch [data-pane]').forEach((b) => b.addEventListener('click', () => {
    root.querySelectorAll('.tpl-switch [data-pane]').forEach((x) => x.classList.toggle('on', x === b));
    root.dataset.show = b.dataset.pane;
  }));
  root.dataset.show = 'edit';

  $('tplSave').addEventListener('click', async () => {
    $('tplText').value = compose();
    const btn = $('tplSave'); btn.disabled = true;
    try {
      const resp = await fetch('/vorlage', { method: 'POST', body: new FormData(form), headers: { 'X-CSRF-Token': csrf, Accept: 'application/json' } });
      const json = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(json.error || 'Hat nicht geklappt.');
      dirty = false; $('tplMsg').textContent = json.message;
    } catch (e) { $('tplMsg').textContent = e.message; }
    finally { btn.disabled = false; }
  });
  window.addEventListener('beforeunload', (e) => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
  refresh();
})();
