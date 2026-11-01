// Postfach: Gespräch (Stimmung, antworten), Liste (jetzt abrufen) und neue Mail schreiben
(() => {
  const csrf = document.querySelector('meta[name=csrf-token]')?.content;
  const post = async (url, data) => {
    const body = data instanceof FormData ? data : new FormData();
    if (!(data instanceof FormData)) Object.entries(data || {}).forEach(([k, v]) => body.append(k, v));
    const resp = await fetch(url, { method: 'POST', body, headers: { 'X-CSRF-Token': csrf, Accept: 'application/json' } });
    const json = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(json.error || 'Hat nicht geklappt.');
    return json;
  };
  const melden = (text, fehler) => (window.osToast ? window.osToast(text, fehler) : alert(text));

  const root = document.getElementById('conv');
  if (root) {
    const id = root.dataset.id;
    root.querySelectorAll('[data-mood]').forEach((b) => b.addEventListener('click', async () => {
      try { await post(`/crm/postfach/${id}/stimmung`, { mood: b.dataset.mood }); location.reload(); }
      catch (e) { melden(e.message, true); }
    }));
    const form = document.getElementById('replyForm');
    form?.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = document.getElementById('replySend');
      const label = btn?.querySelector('span');
      if (btn) { btn.disabled = true; label.textContent = 'Wird gesendet …'; }
      try { await post(`/crm/postfach/${id}/antwort`, { text: form.text.value, subject: form.subject.value, von: form.von?.value || '', postfach: form.postfach?.value || '' }); location.reload(); }
      catch (err) { melden(err.message, true); if (btn) { btn.disabled = false; label.textContent = 'Antworten'; } }
    });
    const entwurf = document.getElementById('entwurfAssistent');
    entwurf?.addEventListener('click', async () => {
      const text = entwurf.textContent;
      entwurf.disabled = true; entwurf.textContent = 'Schreibt …';
      try {
        const r = await post(entwurf.dataset.url);
        if (r.betreff) form.subject.value = r.betreff;
        form.text.value = r.text;
        form.text.focus();
        melden(r.message);
      } catch (err) { melden(err.message, true); }
      finally { entwurf.disabled = false; entwurf.textContent = text; }
    });
    root.querySelectorAll('[data-wunsch]').forEach((b) => b.addEventListener('click', async () => {
      b.disabled = true;
      try { await post(`/crm/postfach/nachricht/${b.dataset.wunsch}/wunsch`, { ja: b.dataset.ja }); location.reload(); }
      catch (e) { melden(e.message, true); b.disabled = false; }
    }));
    const fh = document.getElementById('festhaltenForm');
    fh?.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = fh.querySelector('button');
      btn.disabled = true;
      try { await post(`/crm/postfach/${id}/festhalten`, new FormData(fh)); location.reload(); }
      catch (err) { melden(err.message, true); btn.disabled = false; }
    });
    if (!location.hash && document.querySelector('.os-thread li.in')) document.querySelector('.os-thread li:last-child')?.scrollIntoView({ block: 'center' });
  }

  const fetchBtn = document.getElementById('inboxFetch');
  fetchBtn?.addEventListener('click', async () => {
    const msg = document.getElementById('inboxMsg');
    const label = fetchBtn.querySelector('span') || fetchBtn;
    fetchBtn.disabled = true; label.textContent = 'Ruft ab …';
    try {
      const r = await post('/crm/postfach/abrufen');
      msg.textContent = r.message; msg.hidden = false; melden(r.message);
      if (r.neu) location.reload();
    } catch (e) { msg.textContent = e.message; msg.hidden = false; melden(e.message, true); }
    finally { fetchBtn.disabled = false; label.textContent = 'Jetzt abrufen'; }
  });

  const mailNew = document.getElementById('mailNew');
  mailNew?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = document.querySelector('[form="mailNew"]');
    const label = btn?.querySelector('span');
    if (btn) { btn.disabled = true; label.textContent = 'Wird gesendet …'; }
    try { const r = await post('/crm/postfach/schreiben', new FormData(mailNew)); location.href = r.url || '/crm/postfach'; }
    catch (err) { melden(err.message, true); if (btn) { btn.disabled = false; label.textContent = 'Senden'; } }
  });
})();
