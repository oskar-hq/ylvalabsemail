// Gespräch im Postfach: Stimmung setzen und direkt antworten
(() => {
  const root = document.getElementById('conv');
  if (!root) return;
  const csrf = document.querySelector('meta[name=csrf-token]').content;
  const post = async (url, data) => {
    const body = new FormData();
    Object.entries(data).forEach(([k, v]) => body.append(k, v));
    const resp = await fetch(url, { method: 'POST', body, headers: { 'X-CSRF-Token': csrf, Accept: 'application/json' } });
    const json = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(json.error || 'Hat nicht geklappt.');
    return json;
  };
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
})();
