// Nach dem Neustart: warten, bis die App wieder antwortet, dann den Link zur Anmeldung zeigen.
(() => {
  const start = Date.now();
  const tick = async () => {
    try {
      const r = await fetch('/healthz', { cache: 'no-store' });
      if (r.ok && Date.now() - start > 3000) {
        document.getElementById('restartMsg').textContent = 'Fertig. Die App läuft wieder.';
        document.getElementById('restartLink').hidden = false;
        return;
      }
    } catch (e) {}
    setTimeout(tick, 1500);
  };
  setTimeout(tick, 2500);
})();
