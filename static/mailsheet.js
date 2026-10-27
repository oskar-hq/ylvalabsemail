// Mail-Fenster: Tippen auf ✉ bei einem Betrieb öffnet die fertige Mail (Vorlage mit Firmenname, Anrede …).
// Ändern, dann senden. Ist der Erstkontakt per Mail aus (§ 7 UWG), bleiben Brief und „als versendet markieren“.
(() => {
  const dlg = document.getElementById('mailSheet');
  if (!dlg) return;
  const csrf = document.querySelector('meta[name=csrf-token]').content;
  const $ = (id) => document.getElementById(id);
  let lead = null;

  async function call(url, data, method = 'POST') {
    const opts = { method, headers: { 'X-CSRF-Token': csrf, Accept: 'application/json' } };
    if (method === 'POST') { opts.body = new FormData(); Object.entries(data || {}).forEach(([k, v]) => opts.body.append(k, v)); }
    const resp = await fetch(url, opts);
    const json = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(json.error || 'Hat nicht geklappt.');
    return json;
  }
  const fields = () => ({ subject: $('mailSubject').value, greeting: $('mailGreeting').value, body: $('mailBody').value,
    template: $('mailTemplateInput').value, graphic: $('mailGraphic').value, icon: $('mailIcon').value,
    band_seed: $('mailSeedInput').value, von: $('mailFrom').value });

  // Design: Vorlage (Kurz-Mail / Newsletter), Grafik, Symbol, Variante des Pixel-Bands. Vorschau live.
  function setDesign(d, opts) {
    if (opts) {
      $('mailTpl').innerHTML = Object.entries(opts.templates).map(([k, v]) => `<button type="button" data-tpl="${k}">${v}</button>`).join('');
      $('mailGraphic').innerHTML = Object.entries(opts.graphics).map(([k, v]) => `<option value="${k}">${v}</option>`).join('');
      $('mailIcon').innerHTML = Object.entries(opts.icons).map(([k, v]) => `<option value="${k}">${v}</option>`).join('');
    }
    $('mailTemplateInput').value = d.template;
    $('mailTpl').querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.tpl === d.template));
    $('mailGraphic').value = d.graphic; $('mailIcon').value = d.icon; $('mailSeedInput').value = d.band_seed;
    $('mailIcon').hidden = d.graphic !== 'icon'; $('mailSeed').hidden = d.graphic !== 'band';
    refreshPreview();
  }
  const currentDesign = () => ({ template: $('mailTemplateInput').value, graphic: $('mailGraphic').value,
    icon: $('mailIcon').value, band_seed: $('mailSeedInput').value });
  let previewTimer = null;
  function refreshPreview() {
    if (!lead || !$('mailPreviewBox').open) return;
    clearTimeout(previewTimer);
    previewTimer = setTimeout(async () => {
      const body = new FormData(); Object.entries(fields()).forEach(([k, v]) => body.append(k, v));
      const resp = await fetch(`/leads/${lead.id}/preview`, { method: 'POST', body, headers: { 'X-CSRF-Token': csrf } });
      if (resp.ok) $('mailFrame').srcdoc = await resp.text();
    }, 250);
  }
  $('mailTpl').addEventListener('click', (e) => { const b = e.target.closest('[data-tpl]'); if (b) setDesign({ ...currentDesign(), template: b.dataset.tpl }); });
  $('mailGraphic').addEventListener('change', () => setDesign(currentDesign()));
  $('mailIcon').addEventListener('change', () => setDesign(currentDesign()));
  $('mailSeed').addEventListener('click', () => setDesign({ ...currentDesign(), band_seed: String(1 + Math.floor(Math.random() * 50)) }));
  $('mailPreviewBox').addEventListener('toggle', refreshPreview);
  $('mailFrom').addEventListener('change', refreshPreview);
  ['mailSubject', 'mailGreeting', 'mailBody'].forEach((id) => $(id).addEventListener('input', refreshPreview));
  function fill(d) {
    lead = d;
    $('mailTitle').textContent = d.name;
    $('mailTo').textContent = d.email ? `an ${d.contact ? d.contact + ' · ' : ''}${d.email}` : 'Keine E-Mail-Adresse bekannt';
    $('mailSubject').value = d.subject; $('mailGreeting').value = d.greeting; $('mailBody').value = d.body;
    ['mailSubject', 'mailGreeting', 'mailBody'].forEach((id) => { $(id).readOnly = !d.editable; });
    $('mailDetail').href = d.detail;
    $('mailFrom').innerHTML = '';
    Object.entries(d.senders || {}).forEach(([k, v]) => $('mailFrom').append(new Option(v, k, false, k === d.sender)));
    $('mailFrom').closest('.from-field').hidden = !Object.keys(d.senders || {}).length;
    setDesign(d.design, d);
    $('mailSend').disabled = !d.can_email;
    $('mailSend').textContent = d.sent ? 'Schon versendet' : 'Senden';
    $('mailLetter').hidden = d.can_email || d.sent;
    dlg.querySelectorAll('[data-mark]').forEach((b) => { b.hidden = d.can_email || d.sent; });
    $('mailTemplate').hidden = !d.editable; $('mailSave').hidden = !d.editable;
    $('mailNote').hidden = !d.why; $('mailNote').textContent = d.why || '';
  }
  async function open(id) {
    dlg.showModal();
    $('mailTitle').textContent = 'Lädt …';
    try { fill(await call(`/leads/${id}/mail.json`, null, 'GET')); }
    catch (e) { $('mailNote').hidden = false; $('mailNote').textContent = e.message; }
  }
  const toast = (text) => { window.dispatchEvent(new CustomEvent('ylva:toast', { detail: text })); };
  const done = (how) => {
    dlg.close();
    window.dispatchEvent(new CustomEvent('ylva:contacted', { detail: { id: lead.id, how } }));
  };

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-mail]');
    if (!btn) return;
    e.preventDefault(); e.stopPropagation();
    open(btn.dataset.mail);
  });
  dlg.querySelector('[data-close]').addEventListener('click', () => dlg.close());
  $('mailTemplate').addEventListener('click', async () => {
    if (!confirm('Den Text durch die Vorlage ersetzen?')) return;
    fill(await call(`/leads/${lead.id}/mail.json`, { neu: '1', von: $('mailFrom').value }));
  });
  $('mailSave').addEventListener('click', async () => {
    await call(`/leads/${lead.id}/save`, fields());
    toast('Gespeichert.');
  });
  $('mailForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!lead?.can_email) return;
    const btn = $('mailSend'); btn.disabled = true; btn.textContent = 'Wird gesendet …';
    try { await call(`/leads/${lead.id}/send`, fields()); toast(`An ${lead.name} gesendet.`); done('email'); }
    catch (err) { btn.disabled = false; btn.textContent = 'Senden'; $('mailNote').hidden = false; $('mailNote').textContent = err.message; }
  });
  $('mailLetter').addEventListener('click', async () => {
    await call(`/leads/${lead.id}/save`, fields());
    window.open(`${lead.letter}?von=${encodeURIComponent($('mailFrom').value)}`, '_blank');
  });
  dlg.querySelectorAll('[data-mark]').forEach((b) => b.addEventListener('click', async () => {
    await call(`/leads/${lead.id}/mark-sent`, { ...fields(), channel: b.dataset.mark });
    toast(b.dataset.mark === 'brief' ? 'Als Brief versendet vermerkt.' : 'Anruf vermerkt.'); done(b.dataset.mark);
  }));
  // kleine Bestätigung unten, wenn die Seite selbst keine hat
  window.addEventListener('ylva:toast', (e) => {
    let t = document.getElementById('toast');
    if (!t) { t = document.createElement('div'); t.id = 'toast'; document.body.append(t); }
    t.textContent = e.detail; t.className = 'show';
    clearTimeout(t._h); t._h = setTimeout(() => { t.className = ''; }, 2600);
  });
})();
