(() => {
  const csrf = document.querySelector('meta[name=csrf-token]').content;
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  async function post(url, data = {}) {
    const body = data instanceof FormData ? data : new FormData();
    if (!(data instanceof FormData)) for (const [k, v] of Object.entries(data)) body.append(k, v);
    const resp = await fetch(url, { method: 'POST', body, headers: { 'X-CSRF-Token': csrf } });
    let json = {};
    try { json = await resp.json(); } catch { json = { error: `Fehler ${resp.status}` }; }
    if (!resp.ok || json.error) throw new Error(json.error || `Fehler ${resp.status}`);
    return json;
  }

  function busy(btn, on, text) {
    if (!btn) return;
    if (on) { btn.dataset.label = btn.textContent; btn.disabled = true; if (text) btn.textContent = text; }
    else { btn.disabled = false; if (btn.dataset.label) btn.textContent = btn.dataset.label; }
  }

  function toast(text, isError) {
    let el = $('#toast');
    if (!el) { el = document.createElement('div'); el.id = 'toast'; document.body.append(el); }
    el.textContent = text;
    el.className = isError ? 'show err' : 'show';
    clearTimeout(toast.t);
    toast.t = setTimeout(() => { el.className = ''; }, isError ? 7000 : 3500);
  }

  $$('dialog [data-close]').forEach(b => b.addEventListener('click', () => b.closest('dialog').close()));

  // ---------------------------------------------------------------- KI steuern
  $$('[data-run]').forEach(btn => btn.addEventListener('click', async () => {
    busy(btn, true);
    try { toast((await post('/leads/run', { action: btn.dataset.run })).message); }
    catch (e) { toast(e.message, true); }
    finally { setTimeout(() => busy(btn, false), 1500); }
  }));

  const autoToggle = $('#autoToggle');
  autoToggle?.addEventListener('change', async () => {
    try {
      await post('/leads/auto', { on: autoToggle.checked ? '1' : '0' });
      toast(autoToggle.checked ? 'Automatik an: Die KI sucht und schreibt selbstständig weiter.' : 'Automatik aus.');
    } catch (e) { autoToggle.checked = !autoToggle.checked; toast(e.message, true); }
  });

  // Aktivität live anzeigen; bei neuen Einträgen Hinweis zum Neuladen
  const agentBox = $('#agent');
  if (agentBox) {
    const initial = agentBox.dataset.counts;
    setInterval(async () => {
      if (document.hidden) return;
      try {
        const resp = await fetch('/leads/status.json', { headers: { Accept: 'application/json' } });
        if (!resp.ok) return;
        const st = await resp.json();
        $('#activity').textContent = st.activity;
        $('#since').textContent = st.since;
        $('.agent .dot').className = 'dot ' + (st.activity !== 'Wartet' ? 'busy' : (!st.ai || st.paused ? 'off' : ''));
        if (JSON.stringify(st.counts) !== JSON.stringify(JSON.parse(initial)) && !$('#reloadHint')) {
          const a = document.createElement('a');
          a.id = 'reloadHint'; a.href = location.href; a.className = 'btn small primary';
          a.textContent = 'Neue Einträge · neu laden';
          $('.agent-actions').append(a);
        }
      } catch {}
    }, 15000);
  }

  // ---------------------------------------------------------------- Push
  const pushBtn = $('#pushBtn');
  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const standalone = matchMedia('(display-mode: standalone)').matches || navigator.standalone;
  const pushSupported = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;

  function b64ToBytes(b64) {
    const s = atob((b64 + '='.repeat((4 - b64.length % 4) % 4)).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(s, c => c.charCodeAt(0));
  }

  async function pushState() {
    if (!pushSupported) return null;
    const reg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
    return { reg, sub: await reg.pushManager.getSubscription() };
  }

  if (pushBtn) {
    if (pushSupported || isIOS) pushBtn.hidden = false;
    pushState().then(s => {
      if (s?.sub) { pushBtn.textContent = 'Benachrichtigungen aktiv · testen'; pushBtn.dataset.on = '1'; }
    }).catch(() => {});
    pushBtn.addEventListener('click', async () => {
      if (isIOS && !standalone) { $('#iosDlg').showModal(); return; }
      if (!pushSupported) { toast('Dieser Browser kann keine Benachrichtigungen empfangen.', true); return; }
      busy(pushBtn, true);
      try {
        if (pushBtn.dataset.on) {
          const r = await post('/push/test');
          toast(`Test an ${r.devices} Gerät(e) geschickt.`);
          return;
        }
        if (await Notification.requestPermission() !== 'granted') throw new Error('Benachrichtigungen wurden nicht erlaubt.');
        const { reg } = await pushState();
        const { key } = await (await fetch('/push/key')).json();
        const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(key) });
        await post('/push/subscribe', { subscription: JSON.stringify(sub) });
        pushBtn.dataset.on = '1';
        pushBtn.dataset.label = 'Benachrichtigungen aktiv · testen';
        toast('Fertig. Neue Entwürfe und Antworten kommen jetzt aufs Gerät.');
      } catch (e) { toast(e.message, true); }
      finally { busy(pushBtn, false); }
    });
  } else if (pushSupported) {
    navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => {});
  }

  // ---------------------------------------------------------------- Einzelne Firma
  const page = $('[data-lead]');
  if (!page) return;
  const base = `/leads/${page.dataset.lead}`;
  const draft = $('#draft');
  const draftData = () => {
    const fd = new FormData();
    if (draft && !$('[name=body]', draft).readOnly) for (const el of $$('input,textarea', draft)) fd.append(el.name, el.value);
    return fd;
  };
  const reload = () => location.reload();

  // Text / Vorschau
  $$('[data-view]').forEach(b => b.addEventListener('click', async () => {
    $$('[data-view]').forEach(x => x.classList.toggle('on', x === b));
    const preview = b.dataset.view === 'preview';
    $('[data-pane=edit]').hidden = preview;
    $('[data-pane=preview]').hidden = !preview;
    if (preview) {
      if (!$('[name=body]', draft).readOnly) await post(`${base}/save`, draftData()).catch(() => {});
      const f = $('#leadFrame');
      f.src = f.dataset.src + '?t=' + Date.now();
    }
  }));

  $$('[data-act]').forEach(btn => btn.addEventListener('click', async (ev) => {
    const act = btn.dataset.act;
    if (act === 'letter') { post(`${base}/save`, draftData()).catch(() => {}); return; }
    ev.preventDefault();
    try {
      if (act === 'save') {
        busy(btn, true);
        const r = await post(`${base}/save`, draftData());
        toast(r.changed ? 'Gespeichert. Die KI lernt aus deinen Änderungen.' : 'Keine Änderung.');
      } else if (act === 'send') {
        if (!confirm('Diese Nachricht jetzt per Mail verschicken?')) return;
        busy(btn, true, 'Sendet …');
        await post(`${base}/send`, draftData());
        reload();
      } else if (act === 'mark') {
        const dlg = $('#markDlg');
        dlg.returnValue = '';
        dlg.showModal();
        dlg.addEventListener('close', async () => {
          if (!['brief', 'telefon', 'email'].includes(dlg.returnValue)) return;
          const fd = draftData(); fd.append('channel', dlg.returnValue);
          try { await post(`${base}/mark-sent`, fd); reload(); } catch (e) { toast(e.message, true); }
        }, { once: true });
      } else if (act === 'reject') {
        const reason = await askReason('Warum passt der Betrieb nicht?');
        if (reason === null) return;
        await post(`${base}/reject`, { reason });
        reload();
      } else if (act === 'retry') {
        busy(btn, true);
        await post(`${base}/retry`);
        toast('Die KI prüft den Betrieb gleich noch einmal.');
        setTimeout(reload, 2500);
      }
    } catch (e) { toast(e.message, true); }
    finally { busy(btn, false); }
  }));

  function askReason(title) {
    const dlg = $('#reasonDlg');
    $('#reasonTitle').textContent = title;
    $('[name=reason]', dlg).value = '';
    dlg.returnValue = '';
    dlg.showModal();
    return new Promise(res => dlg.addEventListener('close', () => res(dlg.returnValue === 'ok' ? $('[name=reason]', dlg).value : null), { once: true }));
  }

  $$('[data-interest]').forEach(btn => btn.addEventListener('click', async () => {
    const reason = await askReason(`Als „${btn.textContent}“ einordnen. Warum?`);
    if (reason === null) return;
    try { await post(`${base}/interest`, { interest: btn.dataset.interest, reason }); reload(); }
    catch (e) { toast(e.message, true); }
  }));

  // Chat
  const chatForm = $('#chatForm');
  const chatBox = $('#chat');
  function bubble(cls, who, text) {
    $('#chatEmpty')?.remove();
    const div = document.createElement('div');
    div.className = `msg ${cls}`;
    div.innerHTML = '<span class="mono muted"></span><p></p>';
    div.children[0].textContent = who;
    div.children[1].textContent = text;
    chatBox.append(div);
    div.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    return div;
  }
  chatForm?.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const ta = $('textarea', chatForm);
    const text = ta.value.trim();
    if (!text) return;
    const btn = $('button', chatForm);
    const fd = draftData(); fd.append('text', text);
    bubble('me', 'Du · jetzt', text);
    const pending = bubble('ai pending', 'KI', 'Überarbeitet den Entwurf …');
    ta.value = '';
    busy(btn, true);
    try {
      const r = await post(`${base}/chat`, fd);
      pending.remove();
      bubble('ai', 'KI · jetzt', r.reply);
      if (draft) {
        for (const [name, key] of [['subject', 'subject'], ['greeting', 'greeting'], ['body', 'body']]) {
          const el = $(`[name=${name}]`, draft);
          if (el && r[key] != null && el.value !== r[key]) { el.value = r[key]; el.classList.add('flash'); setTimeout(() => el.classList.remove('flash'), 1200); }
        }
      }
    } catch (e) {
      pending.remove();
      ta.value = text;
      toast(e.message, true);
    } finally { busy(btn, false); }
  });
  $('textarea', chatForm || document.createElement('form'))?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) chatForm.requestSubmit();
  });

  $('#noteForm')?.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const input = $('input', ev.target);
    try { await post(`${base}/note`, { text: input.value }); reload(); } catch (e) { toast(e.message, true); }
  });
})();
