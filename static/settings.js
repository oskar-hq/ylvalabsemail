// Seite „Software und Updates“: Aufträge an den Update-Dienst und Fortschritt anzeigen.
(() => {
  const page = document.getElementById('software');
  if (!page) return;
  const csrf = document.querySelector('meta[name=csrf-token]').content;
  const toast = (text, err) => {
    let el = document.getElementById('toast');
    if (!el) { el = document.createElement('div'); el.id = 'toast'; document.body.append(el); }
    el.textContent = text; el.className = err ? 'show err' : 'show';
    clearTimeout(toast.t); toast.t = setTimeout(() => { el.className = ''; }, 6000);
  };

  document.querySelectorAll('[data-sw]').forEach(btn => btn.addEventListener('click', async () => {
    const kind = btn.dataset.sw;
    if (kind === 'update' && !window.confirm('Update jetzt einspielen? Die App ist dabei kurz nicht erreichbar.')) return;
    btn.disabled = true;
    try {
      const r = await fetch(`/einstellungen/software/${kind}`, { method: 'POST', headers: { 'X-CSRF-Token': csrf } });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || `Fehler ${r.status}`);
      toast(j.message);
      if (kind === 'update') waitForRestart(); else setTimeout(() => location.reload(), 8000);
    } catch (e) { toast(e.message, true); btn.disabled = false; }
  }));

  // Während des Updates ist die App weg. Erst auf den Status, dann auf /healthz warten.
  function waitForRestart() {
    const state = document.getElementById('upState');
    let wasDown = false;
    const tick = async () => {
      try {
        const r = await fetch('/einstellungen/software/status.json', { cache: 'no-store', redirect: 'manual' });
        if (r.ok) {
          const st = await r.json();
          const s = st.status || {};
          if (state) state.textContent = { running: 'Läuft gerade', done: 'Erfolgreich', failed: 'Fehlgeschlagen', rolled_back: 'Zurückgerollt' }[s.state] || 'Wartet auf den Dienst …';
          if (!st.pending.update && s.state && s.state !== 'running') { location.reload(); return; }
        } else if (wasDown) {  // nach dem Neustart: Anmeldung ist weg
          location.href = '/login?next=/einstellungen/software'; return;
        }
      } catch (e) {
        wasDown = true;
        if (state) state.textContent = 'Startet neu …';
      }
      setTimeout(tick, 3000);
    };
    setTimeout(tick, 2000);
  }
  if (page.dataset.running) waitForRestart();
})();
