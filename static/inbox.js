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

  const root = document.getElementById('conv');
  if (root) {
    const id = root.dataset.id;
    root.querySelectorAll('[data-mood]').forEach((b) => b.addEventListener('click', async () => {
      try { await post(`/crm/postfach/${id}/stimmung`, { mood: b.dataset.mood }); location.reload(); }
      catch (e) { alert(e.message); }
    }));
    const form = document.getElementById('replyForm');
    form?.addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = form.querySelector('button.primary');
      btn.disabled = true; btn.textContent = 'Wird gesendet …';
      try { await post(`/crm/postfach/${id}/antwort`, { text: form.text.value, subject: form.subject.value }); location.reload(); }
      catch (err) { alert(err.message); btn.disabled = false; btn.textContent = 'Antworten'; }
    });
    document.querySelector('.thread li:last-child')?.scrollIntoView({ block: 'end' });
  }

  const fetchBtn = document.getElementById('inboxFetch');
  fetchBtn?.addEventListener('click', async () => {
    const msg = document.getElementById('inboxMsg');
    fetchBtn.disabled = true; fetchBtn.textContent = 'Ruft ab …';
    try {
      const r = await post('/crm/postfach/abrufen');
      msg.textContent = r.message; msg.hidden = false;
      if (r.neu) location.reload();
    } catch (e) { msg.textContent = e.message; msg.hidden = false; msg.className = 'note err small'; }
    finally { fetchBtn.disabled = false; fetchBtn.textContent = 'Jetzt abrufen'; }
  });

  const mailNew = document.getElementById('mailNew');
  mailNew?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = mailNew.querySelector('button.primary');
    btn.disabled = true; btn.textContent = 'Wird gesendet …';
    try { const r = await post('/crm/postfach/schreiben', new FormData(mailNew)); location.href = r.url; }
    catch (err) { alert(err.message); btn.disabled = false; btn.textContent = 'Senden'; }
  });
})();
