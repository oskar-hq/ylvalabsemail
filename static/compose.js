(() => {
  const form = document.getElementById('mail');
  const csrf = document.querySelector('meta[name=csrf-token]').content;
  const frame = document.getElementById('frame');
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const DRAFT_KEY = 'ylva-mailer-draft';

  // ---------------------------------------------------------------- Entwurf
  const store = {
    get() { try { return JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null'); } catch { return null; } },
    set(v) { try { localStorage.setItem(DRAFT_KEY, JSON.stringify(v)); } catch {} },
    clear() { try { localStorage.removeItem(DRAFT_KEY); } catch {} },
  };

  function serialize() {
    const data = {};
    for (const [k, v] of new FormData(form)) {
      if (k === '_csrf') continue;
      if (k === 'info_label' || k === 'info_value') (data[k] ||= []).push(v);
      else data[k] = v;
    }
    data._image_url = $('#imgPrev').dataset.url || '';
    return data;
  }

  function infoRow(label = '', value = '') {
    const row = document.createElement('div');
    row.className = 'info-row';
    row.innerHTML = '<input name="info_label" type="text" placeholder="Termin"><input name="info_value" type="text" placeholder="Offener Lab-Abend am 12.10. in Kiel."><button type="button" class="x" title="Zeile entfernen">×</button>';
    row.children[0].value = label;
    row.children[1].value = value;
    return row;
  }

  function restore(data) {
    for (const [k, v] of Object.entries(data)) {
      if (k === 'info_label' || k.startsWith('_')) continue;
      $$(`[name="${k}"]`, form).forEach(el => {
        if (el.type === 'radio') el.checked = el.value === v;
        else if (el.type === 'checkbox') el.checked = !!v;
        else el.value = v;
      });
    }
    if (data.info_label) {
      const box = $('#infoRows');
      box.innerHTML = '';
      data.info_label.forEach((l, i) => box.append(infoRow(l, (data.info_value || [])[i] || '')));
    }
    if (data.image_id && data._image_url) setImage(data._image_url, 'Bild gewählt · zum Ersetzen klicken');
  }

  if (form.dataset.fromHistory !== 'true') {
    const draft = store.get();
    if (draft && Object.values(draft).some(v => typeof v === 'string' && v.trim() && v.length > 0)) {
      restore(draft);
      $('#draftNote').hidden = false;
    }
  }
  $('#resetDraft').addEventListener('click', () => { store.clear(); location.href = location.pathname; });

  // ---------------------------------------------------------------- Grafik
  function syncGraphic() {
    const g = $('input[name=graphic]:checked', form)?.value;
    $$('.gfx-opt').forEach(el => el.classList.toggle('on', el.dataset.for === g));
    const seed = $('input[name=band_seed]').value;
    $('#seedLabel').textContent = seed;
    $('#seedPrev').style.backgroundImage = `url('/pixel/band/${seed}.svg')`;
    const icon = $('input[name=icon]:checked');
    if (icon) $('.icon-prev').innerHTML = icon.closest('label').querySelector('svg').outerHTML;
  }
  $('#shuffle').addEventListener('click', () => {
    const r = $('input[name=band_seed]');
    let n; do { n = 1 + Math.floor(Math.random() * 50); } while (String(n) === r.value);
    r.value = n;
    changed();
  });

  function setImage(url, text) {
    const p = $('#imgPrev');
    p.dataset.url = url;
    p.style.backgroundImage = `url('${url}')`;
    p.innerHTML = '';
    $('#dropText').textContent = text;
  }

  async function uploadImage(file) {
    if (!file) return;
    $('#dropText').textContent = 'Lädt hoch …';
    const fd = new FormData();
    fd.append('image', file);
    try {
      const res = await fetch('/upload', { method: 'POST', body: fd, headers: { 'X-CSRF-Token': csrf } });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error || 'Upload fehlgeschlagen');
      form.image_id.value = json.id;
      setImage(json.url, `${json.filename} · zum Ersetzen klicken`);
      $('input[name=graphic][value=image]').checked = true;
      changed();
    } catch (e) {
      $('#dropText').textContent = e.message;
    }
  }
  const drop = $('#drop');
  $('#imageFile').addEventListener('change', e => uploadImage(e.target.files[0]));
  ['dragenter', 'dragover'].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(t => drop.addEventListener(t, () => drop.classList.remove('over')));
  drop.addEventListener('drop', e => { e.preventDefault(); uploadImage(e.dataTransfer.files[0]); });

  // ---------------------------------------------------------------- Infozeilen
  $('#addInfo').addEventListener('click', () => { $('#infoRows').append(infoRow()); $('#infoRows').lastChild.firstChild.focus(); });
  $('#infoRows').addEventListener('click', e => {
    if (!e.target.classList.contains('x')) return;
    const rows = $$('.info-row');
    if (rows.length > 1) e.target.parentElement.remove();
    else rows[0].querySelectorAll('input').forEach(i => (i.value = ''));
    changed();
  });

  // ---------------------------------------------------------------- Empfänger
  const EMAIL = /^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$/;
  function recipients() {
    const raw = form.recipients.value.replace(/[;\n]/g, ',');
    const ok = [], bad = [];
    const seen = new Set();
    raw.split(',').map(s => s.trim()).filter(Boolean).forEach(part => {
      const m = part.match(/^(.*)<([^>]+)>$/);
      const name = m ? m[1].trim().replace(/^"|"$/g, '') : '';
      const email = (m ? m[2] : part).trim();
      if (!EMAIL.test(email)) bad.push(part);
      else if (!seen.has(email.toLowerCase())) { seen.add(email.toLowerCase()); ok.push({ name, email }); }
    });
    return { ok, bad };
  }
  function syncRecipients() {
    const { ok, bad } = recipients();
    const pill = $('#rcptCount');
    pill.textContent = ok.length === 1 ? '1 Empfänger' : `${ok.length} Empfänger`;
    pill.classList.toggle('ok', ok.length > 0 && !bad.length);
    const err = $('#rcptErrors');
    err.hidden = !bad.length;
    err.textContent = bad.length ? `Nicht erkannt: ${bad.join(', ')}` : '';
    $('#sendBtn').textContent = ok.length > 1 ? `An ${ok.length} senden` : 'Senden';
    $('#previewFor').textContent = ok[0] ? `· für ${ok[0].name || ok[0].email}` : '';
  }

  // ---------------------------------------------------------------- Vorschau
  let timer, seq = 0;
  async function refreshPreview() {
    const my = ++seq;
    const fd = new FormData(form);
    try {
      const res = await fetch('/preview', { method: 'POST', body: fd, headers: { 'X-CSRF-Token': csrf } });
      if (res.status === 401) { location.href = '/login?next=/compose'; return; }
      const html = await res.text();
      if (my !== seq) return;
      const y = frame.contentWindow?.scrollY || 0;
      frame.srcdoc = html;
      frame.onload = () => frame.contentWindow.scrollTo(0, y);
    } catch {}
    const first = recipients().ok[0] || {};
    const fill = s => s.replace(/[  ]?\{vorname\}/g, first.name ? ' ' + first.name.split(' ')[0] : '')
      .replace(/[  ]?\{name\}/g, first.name ? ' ' + first.name : '').trim();
    $('#pvSubject').textContent = fill(form.subject.value) || 'Betreff';
    $('#pvPre').textContent = fill(form.preheader.value);
  }

  function changed() {
    syncGraphic();
    syncRecipients();
    store.set(serialize());
    clearTimeout(timer);
    timer = setTimeout(refreshPreview, 350);
  }
  form.addEventListener('input', changed);
  form.addEventListener('change', changed);

  $$('.seg button').forEach(b => b.addEventListener('click', () => {
    $$('.seg button').forEach(x => x.classList.toggle('on', x === b));
    $('.frame-wrap').classList.toggle('narrow', b.dataset.w === '375');
  }));

  // ---------------------------------------------------------------- Versand
  const dlg = $('#dlg');
  const esc = s => s.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  function openDialog({ label, title, body, ok = 'Jetzt senden', cancel = 'Abbrechen' }) {
    $('#dlgLabel').textContent = label;
    $('#dlgTitle').textContent = title;
    $('#dlgBody').innerHTML = body;
    $('#dlgOk').textContent = ok;
    $('#dlgOk').hidden = !ok;
    $('#dlgCancel').textContent = cancel;
    dlg.returnValue = '';
    dlg.showModal();
    return new Promise(resolve => dlg.addEventListener('close', () => resolve(dlg.returnValue === 'ok'), { once: true }));
  }

  async function send(mode) {
    const { ok, bad } = recipients();
    if (!form.subject.value.trim()) { form.subject.focus(); form.subject.reportValidity?.(); return; }
    if (mode === 'send') {
      if (bad.length) { form.recipients.focus(); return; }
      if (!ok.length) { form.recipients.focus(); return; }
      const list = ok.slice(0, 50).map(r => `<li>${esc(r.name ? `${r.name} <${r.email}>` : r.email)}</li>`).join('');
      const go = await openDialog({
        label: 'Senden', title: `„${form.subject.value.trim()}“ an ${ok.length} ${ok.length === 1 ? 'Person' : 'Personen'} senden?`,
        body: `<ul>${list}${ok.length > 50 ? `<li>… und ${ok.length - 50} weitere</li>` : ''}</ul><p class="small">Jede Person bekommt eine eigene Mail.</p>`,
      });
      if (!go) return;
    }
    const btns = [$('#sendBtn'), $('#testBtn')];
    btns.forEach(b => (b.disabled = true));
    const btn = mode === 'test' ? $('#testBtn') : $('#sendBtn');
    const old = btn.textContent;
    btn.textContent = 'Sendet …';
    const fd = new FormData(form);
    fd.append('mode', mode);
    try {
      const res = await fetch('/send', { method: 'POST', body: fd, headers: { 'X-CSRF-Token': csrf } });
      const json = await res.json().catch(() => ({ error: `Serverfehler (${res.status})` }));
      if (res.status === 401) { location.href = '/login?next=/compose'; return; }
      if (json.error) {
        await openDialog({ label: 'Fehler', title: 'Nicht gesendet.', body: `<p>${esc(json.error)}</p>`, ok: '', cancel: 'Schließen' });
      } else {
        const failed = (json.failed || []).map(f => `<li>${esc(f.email)}: ${esc(f.error)}</li>`).join('');
        const title = json.test ? 'Testmail ist unterwegs.'
          : json.failed.length ? `${json.sent.length} gesendet, ${json.failed.length} fehlgeschlagen.` : `${json.sent.length} ${json.sent.length === 1 ? 'Mail' : 'Mails'} gesendet.`;
        const body = (json.test ? `<p>An ${esc(json.sent[0] || '')}. Schau in dein Postfach.</p>` : '')
          + (failed ? `<ul>${failed}</ul>` : '') + (json.warning ? `<p class="small">${esc(json.warning)}</p>` : '');
        if (!json.test && json.sent.length) store.clear();
        await openDialog({ label: json.test ? 'Test' : 'Gesendet', title, body, ok: '', cancel: 'Schließen' });
      }
    } catch (e) {
      await openDialog({ label: 'Fehler', title: 'Keine Verbindung.', body: `<p>${esc(e.message)}</p>`, ok: '', cancel: 'Schließen' });
    } finally {
      btns.forEach(b => (b.disabled = false));
      btn.textContent = old;
      syncRecipients();
    }
  }
  $('#sendBtn').addEventListener('click', () => send('send'));
  $('#testBtn').addEventListener('click', () => send('test'));

  // Start
  if (form.image_id.value && !$('#imgPrev').dataset.url) setImage(`/uploads/${form.image_id.value}`, 'Bild gewählt · zum Ersetzen klicken');
  syncGraphic();
  syncRecipients();
  refreshPreview();
})();
