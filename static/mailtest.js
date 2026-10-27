// Einstellungen → Postfach: Anmeldung bei Strato prüfen (SMTP und IMAP), ohne etwas zu verschicken
(() => {
  const btn = document.getElementById('mailTestBtn');
  if (!btn) return;
  const out = document.getElementById('mailTestOut');
  const csrf = document.querySelector('meta[name=csrf-token]').content;
  btn.addEventListener('click', async () => {
    btn.disabled = true; btn.textContent = 'Prüft …'; out.innerHTML = '';
    try {
      const resp = await fetch('/einstellungen/postfach/test', { method: 'POST', headers: { 'X-CSRF-Token': csrf, Accept: 'application/json' } });
      const json = await resp.json();
      (json.results || []).forEach((r) => {
        const li = document.createElement('li');
        li.className = r.ok ? 'ok' : 'bad';
        li.innerHTML = '<b></b><span></span>';
        li.querySelector('b').textContent = `${r.ok ? '✓' : '✕'} ${r.name}`;
        li.querySelector('span').textContent = r.text;
        out.appendChild(li);
      });
    } catch (e) {
      const li = document.createElement('li'); li.className = 'bad'; li.textContent = 'Test hat nicht geklappt. Startet die App gerade neu? Gleich noch einmal versuchen.'; out.appendChild(li);
    } finally { btn.disabled = false; btn.textContent = 'Verbindung testen'; }
  });
})();
