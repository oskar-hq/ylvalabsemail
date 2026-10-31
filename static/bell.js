// Glocke oben: Meldungen der App (Läufe, Antworten, Wiedervorlagen, Tagesberichte) und ungelesene Mails.
// Die Zahl = neue Meldungen + ungelesene Mails, jede Minute aktualisiert. Beim Öffnen gelten die Meldungen als gesehen.
(() => {
  const bell = document.getElementById('bell');
  if (!bell) return;
  const btn = document.getElementById('bellBtn');
  const panel = document.getElementById('bellPanel');
  const list = document.getElementById('bellList');
  const countEl = document.getElementById('bellCount');
  const errEl = document.getElementById('bellError');
  const noticeList = document.getElementById('noticeList');
  const csrf = document.querySelector('meta[name=csrf-token]')?.content;
  let mails = 0, notices = 0;
  const showCount = () => {
    const n = mails + notices;
    countEl.textContent = n;
    countEl.hidden = !n;
    btn.setAttribute('aria-label', n ? `Meldungen und neue Mails: ${n}` : 'Meldungen und neue Mails');
  };
  const entry = (url, title, at, text, isNew) => {
    const li = document.createElement('li');
    const a = document.createElement('a');
    a.href = url;
    if (isNew) a.className = 'is-new';
    a.innerHTML = '<span class="bell-top"><b></b><time></time></span><span class="bell-text"></span>';
    a.querySelector('b').textContent = title;
    a.querySelector('time').textContent = fmt(at);
    a.querySelector('.bell-text').textContent = text;
    li.appendChild(a);
    return li;
  };
  const empty = (text) => Object.assign(document.createElement('li'), { className: 'muted small bell-empty', textContent: text });

  async function loadNotices() {
    try {
      const r = await (await fetch('/meldungen.json', { headers: { Accept: 'application/json' } })).json();
      notices = r.count;
      noticeList.innerHTML = '';
      if (!r.items.length) noticeList.appendChild(empty('Noch keine Meldungen.'));
      r.items.forEach((it) => noticeList.appendChild(entry(it.url, it.title, it.at, it.text, it.new)));
      showCount();
    } catch (e) { /* offline */ }
  }
  const fmt = (iso) => {
    if (!iso) return '';
    const d = new Date(iso.replace(' ', 'T'));
    const today = new Date().toDateString() === d.toDateString();
    return today ? d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' }) : d.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit' });
  };

  async function load() {
    try {
      const r = await (await fetch('/crm/postfach/neu.json', { headers: { Accept: 'application/json' } })).json();
      mails = r.count;
      showCount();
      errEl.textContent = r.error ? `Abrufen klappt gerade nicht: ${r.error}` : '';
      errEl.hidden = !r.error;
      list.innerHTML = '';
      if (!r.items.length) list.appendChild(empty('Keine ungelesenen Mails.'));
      r.items.forEach((it) => list.appendChild(entry(it.url, it.n > 1 ? `${it.name} (${it.n})` : it.name, it.at, it.text, false)));
    } catch (e) { /* offline: Zahl bleibt, wie sie war */ }
  }

  const close = () => { panel.hidden = true; btn.setAttribute('aria-expanded', 'false'); };
  btn.addEventListener('click', () => {
    const open = panel.hidden;
    panel.hidden = !open;
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) {
      load();
      loadNotices().then(() => {
        if (!notices) return;
        fetch('/meldungen/gelesen', { method: 'POST', headers: { 'X-CSRF-Token': csrf } }).catch(() => {});
        notices = 0;
        showCount();
      });
    }
  });
  document.addEventListener('click', (e) => { if (!bell.contains(e.target)) close(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
  setInterval(() => { if (document.visibilityState === 'visible') { load(); loadNotices(); } }, 60000);
  loadNotices();
})();
