// Meldungen aufs Handy (Mein Profil): Gerät für Push-Meldungen anmelden oder testen.
(() => {
  const btn = document.getElementById('osPush');
  if (!btn) return;
  const csrf = document.querySelector('meta[name=csrf-token]').content;
  const toast = (t, err) => (window.osToast ? window.osToast(t, err) : alert(t));
  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const standalone = matchMedia('(display-mode: standalone)').matches || navigator.standalone;
  const supported = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  const label = btn.querySelector('span') || btn;

  async function post(url, data = {}) {
    const body = new FormData();
    for (const [k, v] of Object.entries(data)) body.append(k, v);
    const resp = await fetch(url, { method: 'POST', body, headers: { 'X-CSRF-Token': csrf } });
    let json = {};
    try { json = await resp.json(); } catch { json = { error: `Fehler ${resp.status}` }; }
    if (!resp.ok || json.error) throw new Error(json.error || `Fehler ${resp.status}`);
    return json;
  }
  const bytes = (b64) => {
    const s = atob((b64 + '='.repeat((4 - b64.length % 4) % 4)).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(s, (c) => c.charCodeAt(0));
  };
  async function state() {
    const reg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
    return { reg, sub: await reg.pushManager.getSubscription() };
  }
  const an = () => { btn.dataset.on = '1'; label.textContent = 'Meldungen sind an · testen'; };

  if (supported) state().then((s) => { if (s.sub) an(); }).catch(() => {});
  btn.addEventListener('click', async () => {
    if (isIOS && !standalone) {
      toast('Auf dem iPhone erst „Teilen → Zum Home-Bildschirm“ wählen und die App von dort öffnen.', true);
      return;
    }
    if (!supported) { toast('Dieser Browser kann keine Meldungen empfangen.', true); return; }
    btn.disabled = true;
    try {
      if (btn.dataset.on) {
        const r = await post('/push/test');
        toast(`Test an ${r.devices} Gerät(e) geschickt.`);
        return;
      }
      if (await Notification.requestPermission() !== 'granted') throw new Error('Meldungen wurden nicht erlaubt.');
      const { reg } = await state();
      const { key } = await (await fetch('/push/key')).json();
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: bytes(key) });
      await post('/push/subscribe', { subscription: JSON.stringify(sub) });
      an();
      toast('Fertig. Meldungen kommen jetzt auf dieses Gerät.');
    } catch (e) { toast(e.message, true); }
    finally { btn.disabled = false; }
  });
})();
