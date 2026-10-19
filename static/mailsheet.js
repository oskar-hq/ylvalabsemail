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
  const fields = () => ({ subject: $('mailSubject').value, greeting: $('mailGreeting').value, body: $('mailBody').value });
  function fill(d) {
    lead = d;
    $('mailTitle').textContent = d.name;
    $('mailTo').textContent = d.email ? `an ${d.contact ? d.contact + ' · ' : ''}${d.email}` : 'Keine E-Mail-Adresse bekannt';
    $('mailSubject').value = d.subject; $('mailGreeting').value = d.greeting; $('mailBody').value = d.body;
    ['mailSubject', 'mailGreeting', 'mailBody'].forEach((id) => { $(id).readOnly = !d.editable; });
    $('mailPreview').href = d.preview; $('mailDetail').href = d.detail;
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
    fill(await call(`/leads/${lead.id}/mail.json`, { neu: '1' }));
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
    window.open(lead.letter, '_blank');
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
