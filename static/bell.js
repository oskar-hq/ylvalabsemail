// Glocke oben: Zahl der ungelesenen Mails, beim Antippen die neuesten mit Absender und Anfang des Texts.
// Die Zahl wird jede Minute aktualisiert, damit neue Antworten auch ohne Neuladen auffallen.
(() => {
  const bell = document.getElementById('bell');
  if (!bell) return;
  const btn = document.getElementById('bellBtn');
  const panel = document.getElementById('bellPanel');
  const list = document.getElementById('bellList');
  const countEl = document.getElementById('bellCount');
  const errEl = document.getElementById('bellError');
  const fmt = (iso) => {
    if (!iso) return '';
    const d = new Date(iso.replace(' ', 'T'));
    const today = new Date().toDateString() === d.toDateString();
    return today ? d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' }) : d.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit' });
  };

  async function load() {
    try {
      const r = await (await fetch('/crm/postfach/neu.json', { headers: { Accept: 'application/json' } })).json();
      countEl.textContent = r.count;
      countEl.hidden = !r.count;
      btn.setAttribute('aria-label', r.count ? `Neue Mails: ${r.count}` : 'Neue Mails');
      errEl.textContent = r.error ? `Abrufen klappt gerade nicht: ${r.error}` : '';
      errEl.hidden = !r.error;
      list.innerHTML = '';
      if (!r.items.length) {
        const li = document.createElement('li');
        li.className = 'muted small bell-empty';
        li.textContent = 'Keine ungelesenen Mails.';
        list.appendChild(li);
      }
      r.items.forEach((it) => {
        const li = document.createElement('li');
        const a = document.createElement('a');
        a.href = it.url;
        a.innerHTML = '<span class="bell-top"><b></b><time></time></span><span class="bell-text"></span>';
        a.querySelector('b').textContent = it.n > 1 ? `${it.name} (${it.n})` : it.name;
        a.querySelector('time').textContent = fmt(it.at);
        a.querySelector('.bell-text').textContent = it.text;
        li.appendChild(a);
        list.appendChild(li);
      });
    } catch (e) { /* offline: Zahl bleibt, wie sie war */ }
  }

  const close = () => { panel.hidden = true; btn.setAttribute('aria-expanded', 'false'); };
  btn.addEventListener('click', () => {
    const open = panel.hidden;
    panel.hidden = !open;
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) load();
  });
  document.addEventListener('click', (e) => { if (!bell.contains(e.target)) close(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
  setInterval(() => { if (document.visibilityState === 'visible') load(); }, 60000);
})();
