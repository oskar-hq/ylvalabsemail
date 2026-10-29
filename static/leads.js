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

  // ---------------------------------------------------------------- Läufe und Kosten
  const euro = (v) => v.toFixed(2).replace('.', ',') + ' €';
  const runForm = $('#runForm');
  if (runForm) {
    const est = $('#estimate');
    let timer;
    runForm.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const q = new URLSearchParams({ entwuerfe: runForm.entwuerfe.disabled ? 0 : (runForm.entwuerfe.value || 0),
                                        erinnerungen: runForm.erinnerungen?.checked ? '1' : '0' });
        try {
          const e = await (await fetch('/leads/schaetzung?' + q)).json();
          const budget = parseFloat(String(runForm.budget_eur.value).replace(',', '.')) || 0;
          const per = JSON.parse(runForm.dataset.per || '{}');
          const web = runForm.web?.checked && !runForm.web.disabled;
          const eur = (e.eur || 0) + (web ? (per.websuche || 0) : 0);
          est.innerHTML = (eur ? `KI geschätzt: <b>ca. ${euro(eur)}</b>` : 'Ohne KI-Kosten.')
            + (web ? ' · Websuche' : '') + (e.entwuerfe ? ` · ${e.entwuerfe} Entwürfe` : '')
            + (e.erinnerungen ? ` · ${e.erinnerungen} Erinnerungen` : '')
            + ' Passende Betriebe warten danach unter <a href="/leads/pruefen">„Zu prüfen“</a>.'
            + (eur > budget ? ' <span class="err">Das KI-Budget reicht dafür vermutlich nicht, der Lauf stoppt dann vorher.</span>' : '');
        } catch {}
      }, 250);
    });
    const webBox = $('#webOpts');
    const syncWeb = () => { if (webBox && runForm.web) webBox.hidden = !runForm.web.checked; };
    runForm.web?.addEventListener('change', syncWeb);
    syncWeb();
    runForm.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const btn = $('button.primary', runForm);
      busy(btn, true, 'Startet …');
      try {
        const fd = new FormData(runForm);
        toast((await post('/leads/run', fd)).message);
        setTimeout(() => location.reload(), 1200);
      } catch (e) { toast(e.message, true); busy(btn, false); }
    });
  }
  $('#stopRun')?.addEventListener('click', async (ev) => {
    busy(ev.target, true, 'Stoppt …');
    try { toast((await post('/leads/stop')).message); } catch (e) { toast(e.message, true); busy(ev.target, false); }
  });

  $('#learnNow')?.addEventListener('click', async (ev) => {
    busy(ev.target, true);
    try { toast((await post('/leads/gehirn/lernen')).message); setTimeout(() => location.reload(), 60000); }
    catch (e) { toast(e.message, true); busy(ev.target, false); }
  });

  // Aktivität und Kosten live; wenn ein Lauf endet oder neue Einträge da sind: Hinweis bzw. neu laden
  const agentBox = $('#agent');
  if (agentBox) {
    const initial = agentBox.dataset.counts;
    const wasRunning = !!agentBox.dataset.running;
    setInterval(async () => {
      if (document.hidden) return;
      try {
        const resp = await fetch('/leads/status.json', { headers: { Accept: 'application/json' } });
        if (!resp.ok) return;
        const st = await resp.json();
        if (wasRunning && !st.run) { location.reload(); return; }
        if (!wasRunning && st.run) { location.reload(); return; }
        const text = st.activity !== 'Wartet' ? st.activity : (st.run ? 'Startet gleich …' : 'Bereit.');
        $('#activity').textContent = text;
        if ($('#progress')) $('#progress').textContent = st.activity;
        $('#runEur').textContent = euro(st.run_eur);
        $('#runTokens').textContent = st.run_tokens.toLocaleString('de-DE') + ' Tokens';
        $('#todayEur').textContent = euro(st.today_eur);
        $('#monthEur').textContent = euro(st.month_eur);
        if ($('#monthBar') && st.cap_eur) $('#monthBar').style.width = Math.min(100, st.month_eur / st.cap_eur * 100) + '%';
        $('.agent .dot').className = 'dot ' + (st.run || st.activity !== 'Wartet' ? 'busy' : '');
        if (JSON.stringify(st.counts) !== JSON.stringify(JSON.parse(initial)) && !$('#reloadHint')) {
          const a = document.createElement('a');
          a.id = 'reloadHint'; a.href = location.href; a.className = 'btn small primary';
          a.textContent = 'Neue Einträge · neu laden';
          $('.agent-line').append(a);
        }
      } catch {}
    }, wasRunning ? 4000 : 15000);
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

  // ---------------------------------------------------------------- Wiedervorlagen abhaken (CRM und Akte)
  $$('[data-task]').forEach(btn => btn.addEventListener('click', async () => {
    busy(btn, true);
    try {
      await post(`/crm/aufgabe/${btn.dataset.task}/erledigt`);
      btn.closest('li').classList.add('done');
      toast('Erledigt.');
    } catch (e) { toast(e.message, true); busy(btn, false); }
  }));

  // ---------------------------------------------------------------- CRM: Pipeline-Board
  const board = $('#board');
  if (board) {
    async function move(card, stage) {
      const from = card.closest('.cards');
      const target = $(`.cards[data-stage="${stage}"]`, board);
      if (!target || target === from) return;
      target.prepend(card);
      $('.card-move', card).value = stage;
      recount();
      try { await post(`/crm/${card.dataset.id}/stufe`, { stage }); }
      catch (e) { from.prepend(card); $('.card-move', card).value = from.dataset.stage; recount(); toast(e.message, true); }
    }
    function recount() {
      $$('.col', board).forEach(col => { $('.col-head h2 .muted', col).textContent = $$('.card:not([hidden])', col).length; });
    }
    // Ziehen und Ablegen mit Maus und Finger (Pointer Events): Maus sofort, Finger nach kurzem Gedrückthalten,
    // damit normales Scrollen weiter geht. Eine Kopie der Karte folgt dem Zeiger, die Spalte darunter leuchtet.
    let drag = null;
    const colAt = (x, y) => document.elementFromPoint(x, y)?.closest('.col');
    function startDrag(d) {
      const r = d.card.getBoundingClientRect();
      d.ghost = d.card.cloneNode(true);
      d.ghost.classList.add('drag-ghost');
      Object.assign(d.ghost.style, { width: r.width + 'px', left: r.left + 'px', top: r.top + 'px' });
      d.dx = d.x - r.left; d.dy = d.y - r.top;
      document.body.append(d.ghost);
      d.card.classList.add('dragging');
      d.active = true;
      navigator.vibrate?.(15);
    }
    function moveDrag(d, x, y) {
      d.ghost.style.left = (x - d.dx) + 'px';
      d.ghost.style.top = (y - d.dy) + 'px';
      const col = colAt(x, y);
      $$('.col.over', board).forEach(c => { if (c !== col) c.classList.remove('over'); });
      col?.classList.add('over');
      // am Rand des Boards seitlich weiterscrollen
      const br = board.getBoundingClientRect();
      d.scroll = x < br.left + 40 ? -14 : x > br.right - 40 ? 14 : 0;
      if (d.scroll && !d.timer) d.timer = setInterval(() => { board.scrollLeft += d.scroll; }, 16);
      if (!d.scroll && d.timer) { clearInterval(d.timer); d.timer = null; }
    }
    function endDrag(d, x, y) {
      clearTimeout(d.press);
      if (d.timer) clearInterval(d.timer);
      if (!d.active) return;
      const col = colAt(x, y);
      d.ghost.remove();
      d.card.classList.remove('dragging');
      $$('.col.over', board).forEach(c => c.classList.remove('over'));
      if (col) move(d.card, col.dataset.stage);
      d.card.dataset.justDragged = '1';
      setTimeout(() => delete d.card.dataset.justDragged, 300);
    }
    board.addEventListener('pointerdown', e => {
      const card = e.target.closest('.card');
      if (!card || e.target.closest('select,button') || e.button > 0) return;
      drag = { card, x: e.clientX, y: e.clientY, touch: e.pointerType !== 'mouse', active: false };
      if (drag.touch) drag.press = setTimeout(() => { if (drag) { startDrag(drag); moveDrag(drag, drag.x, drag.y); } }, 280);
    });
    window.addEventListener('pointermove', e => {
      if (!drag) return;
      const far = Math.hypot(e.clientX - drag.x, e.clientY - drag.y) > 8;
      if (!drag.active) {
        if (drag.touch) { if (far) { clearTimeout(drag.press); drag = null; } return; }  // Finger bewegt: scrollen
        if (!far) return;
        startDrag(drag);
      }
      moveDrag(drag, e.clientX, e.clientY);
    });
    window.addEventListener('pointerup', e => { if (drag) { endDrag(drag, e.clientX, e.clientY); drag = null; } });
    window.addEventListener('pointercancel', () => {
      if (!drag) return;
      clearTimeout(drag.press);
      if (drag.active) { drag.ghost.remove(); drag.card.classList.remove('dragging'); }
      drag = null;
    });
    // Beim Ziehen mit dem Finger nicht die Seite scrollen
    board.addEventListener('touchmove', e => { if (drag?.active) e.preventDefault(); }, { passive: false });
    // Nach dem Ablegen nicht versehentlich die Akte öffnen
    board.addEventListener('click', e => { if (e.target.closest('.card')?.dataset.justDragged) e.preventDefault(); }, true);
    board.addEventListener('contextmenu', e => { if (drag) e.preventDefault(); });
    // Ohne Maus (Handy): Stufe über die Auswahl auf der Karte
    $$('.card-move', board).forEach(sel => sel.addEventListener('change', () => move(sel.closest('.card'), sel.value)));
    $('#boardFilter')?.addEventListener('input', e => {
      const q = e.target.value.trim().toLowerCase();
      $$('.card', board).forEach(c => { c.hidden = q && !c.dataset.search.includes(q); });
      recount();
    });
  }
  // ---------------------------------------------------------------- CRM: Tabelle (sortieren, filtern, Stufe ändern)
  const table = $('#crmTable');
  if (table) {
    $$('.row-move', table).forEach(sel => {
      sel.dataset.was = sel.value;
      sel.addEventListener('change', async () => {
        const row = sel.closest('tr');
        try {
          await post(`/crm/${row.dataset.id}/stufe`, { stage: sel.value });
          sel.dataset.was = sel.value;
          sel.closest('td').dataset.v = sel.selectedIndex;
          toast('Stufe geändert.');
        } catch (e) { sel.value = sel.dataset.was; toast(e.message, true); }
      });
    });
    $$('th[data-sort]', table).forEach((th, _) => th.addEventListener('click', () => {
      const idx = [...th.parentNode.children].indexOf(th);
      const dir = th.dataset.dir === 'asc' ? 'desc' : 'asc';
      $$('th', table).forEach(h => delete h.dataset.dir);
      th.dataset.dir = dir;
      const num = th.dataset.sort === 'num';
      const val = (tr) => { const td = tr.children[idx]; const v = td.dataset.v ?? td.textContent.trim().toLowerCase(); return num ? parseFloat(v) || 0 : v; };
      const rows = $$('tbody tr', table).sort((a, b) => (val(a) > val(b) ? 1 : val(a) < val(b) ? -1 : 0) * (dir === 'asc' ? 1 : -1));
      $('tbody', table).append(...rows);
    }));
    $('#boardFilter')?.addEventListener('input', e => {
      const q = e.target.value.trim().toLowerCase();
      $$('tbody tr', table).forEach(r => { r.hidden = q && !r.dataset.search.includes(q); });
    });
  }
  // Nach Mail oder Brief aus dem Mail-Fenster: Board neu laden, damit die Karte in „Kontaktiert“ steht
  if (board || table) window.addEventListener('ylva:contacted', () => setTimeout(() => location.reload(), 900));
  $('#newLead')?.addEventListener('click', () => $('#newDlg').showModal());
  $('#importBtn')?.addEventListener('click', () => $('#importDlg').showModal());
  $('#newForm')?.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const btn = $('button.primary', ev.target);
    busy(btn, true);
    try { const r = await post('/crm/neu', new FormData(ev.target)); location.href = `/leads/${r.lead_id}`; }
    catch (e) { toast(e.message, true); busy(btn, false); }
  });
  $('#importForm')?.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const btn = $('button.primary', ev.target);
    busy(btn, true, 'Importiert …');
    try {
      const r = await post('/crm/import', new FormData(ev.target));
      const more = r.skipped_count ? ` ${r.skipped_count} übersprungen: ${r.skipped.slice(0, 3).join(' · ')}${r.skipped_count > 3 ? ' …' : ''}` : '';
      toast(`${r.created} Firmen importiert.${more}`, false);
      $('#importDlg').close();
      setTimeout(() => location.reload(), r.skipped_count ? 6000 : 1500);
    } catch (e) { toast(e.message, true); } finally { busy(btn, false); }
  });
  // Karte: Zahlen je Kreis importieren, Kreisgrenzen laden
  $('#regionForm')?.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const btn = $('button.primary', ev.target);
    busy(btn, true, 'Importiert …');
    try {
      const r = await post('/crm/regionen/import', new FormData(ev.target));
      toast(`${r.count} Kreise übernommen.` + (r.missing.length ? ` Ohne Wert: ${r.missing.join(', ')}.` : ''));
      setTimeout(() => location.reload(), r.missing.length ? 4000 : 1200);
    } catch (e) { toast(e.message, true); busy(btn, false); }
  });
  $('#loadBorders')?.addEventListener('click', async (ev) => {
    busy(ev.target, true);
    try { toast((await post('/crm/regionen/grenzen')).message); } catch (e) { toast(e.message, true); }
    finally { busy(ev.target, false); }
  });
  $$('.drop input[type=file]').forEach(inp => inp.addEventListener('change', () => {
    inp.nextElementSibling.textContent = inp.files[0]?.name || 'CSV-Datei auswählen';
  }));

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
    if (act === 'design') {  // erst speichern, dann im Mailer gestalten
      await post(`${base}/save`, draftData()).catch(() => {});
      location.href = btn.href;
      return;
    }
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
        toast('Die Webseite wird gleich neu gelesen und bewertet.');
        setTimeout(reload, 6000);
      } else if (act === 'template') {
        const body = $('[name=body]', draft);
        if (body.value.trim() && !confirm('Den jetzigen Text durch die Vorlage ersetzen?')) return;
        busy(btn, true);
        await post(`${base}/entwurf/vorlage`);
        reload();
      } else if (act === 'ai-draft') {
        const body = $('[name=body]', draft);
        if (body.value.trim() && !confirm('Den jetzigen Text durch einen neuen Entwurf der KI ersetzen?')) return;
        busy(btn, true, 'Schreibt …');
        const r = await post(`${base}/entwurf/ki`);
        toast(`Entwurf geschrieben. Kosten: ${euro(r.cost_eur || 0)}`);
        setTimeout(reload, 900);
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

  // Erinnerung (Nachfassen)
  const fu = $('#followup');
  const fuData = () => {
    const fd = new FormData();
    if (fu) for (const el of $$('input,textarea', fu)) if (!el.readOnly) fd.append(el.name, el.value);
    return fd;
  };
  $$('[data-fu]').forEach(btn => btn.addEventListener('click', async () => {
    const act = btn.dataset.fu;
    try {
      if (act === 'write') {
        busy(btn, true, 'Schreibt …');
        const r = await post(`${base}/erinnerung/schreiben`);
        toast(`Erinnerung geschrieben. Kosten: ${euro(r.cost_eur)}`);
        setTimeout(reload, 900);
      } else if (act === 'save') {
        busy(btn, true);
        const r = await post(`${base}/erinnerung/speichern`, fuData());
        toast(r.changed ? 'Gespeichert.' : 'Keine Änderung.');
      } else if (act === 'send') {
        busy(btn, true, 'Sendet …');
        await post(`${base}/erinnerung/senden`, fuData());
        reload();
      } else if (act === 'mark') {
        const dlg = $('#markDlg');
        dlg.returnValue = '';
        dlg.showModal();
        dlg.addEventListener('close', async () => {
          if (!['brief', 'telefon', 'email'].includes(dlg.returnValue)) return;
          const fd = fuData(); fd.append('channel', dlg.returnValue);
          try { await post(`${base}/erinnerung/erledigt`, fd); reload(); } catch (e) { toast(e.message, true); }
        }, { once: true });
      } else if (act === 'skip') {
        await post(`${base}/erinnerung/auslassen`);
        reload();
      }
    } catch (e) { toast(e.message, true); }
    finally { busy(btn, false); }
  }));

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
      bubble('ai', `KI · jetzt · ${euro(r.cost_eur || 0)}`, r.reply);
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

  // Pipeline, Auftragswert, Wiedervorlagen, Kontaktdaten
  $('#stageSelect')?.addEventListener('change', async (ev) => {
    try { const r = await post(`/crm/${page.dataset.lead}/stufe`, { stage: ev.target.value }); toast(`Stufe: ${r.label}`); setTimeout(reload, 700); }
    catch (e) { toast(e.message, true); }
  });
  $('#ownerSelect')?.addEventListener('change', async (ev) => {
    try { const r = await post(`/crm/${page.dataset.lead}/zustaendig`, { owner: ev.target.value }); toast(`Zuständig: ${r.name}`); }
    catch (e) { toast(e.message, true); }
  });
  $('#dealForm')?.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    try { const r = await post(`/crm/${page.dataset.lead}/kontakt`, new FormData(ev.target)); toast(r.changed ? 'Gespeichert.' : 'Keine Änderung.'); }
    catch (e) { toast(e.message, true); }
  });
  const taskForm = $('#taskForm');
  taskForm?.quick.addEventListener('change', () => { taskForm.due.hidden = !!taskForm.quick.value; });
  taskForm?.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    try { await post(`/crm/${page.dataset.lead}/aufgabe`, new FormData(taskForm)); reload(); }
    catch (e) { toast(e.message, true); }
  });
  const contactForm = $('#contactForm');
  const toggleContact = (edit) => { contactForm.hidden = !edit; $('#contactView').hidden = edit; };
  $('#editContact')?.addEventListener('click', () => toggleContact(contactForm.hidden));
  $('#cancelContact')?.addEventListener('click', () => toggleContact(false));
  contactForm?.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    try { await post(`/crm/${page.dataset.lead}/kontakt`, new FormData(contactForm)); reload(); }
    catch (e) { toast(e.message, true); }
  });

  $('#noteForm')?.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const input = $('input', ev.target);
    try { await post(`${base}/note`, { text: input.value }); reload(); } catch (e) { toast(e.message, true); }
  });

  // „Finden“ im Ablauf: Lauf-Formular aufklappen
  const openRun = () => { if (location.hash === '#lauf' && $('#lauf')) { $('#lauf').open = true; $('#lauf').scrollIntoView({ block: 'start' }); } };
  window.addEventListener('hashchange', openRun);
  openRun();
})();

// Leads-Liste: mehrere auswählen und auf einmal ins CRM schicken (Reiter und Person wählbar)
(() => {
  const bar = document.getElementById('bulkBar');
  if (!bar) return;
  const csrf = document.querySelector('meta[name=csrf-token]').content;
  const boxes = () => [...document.querySelectorAll('.lead-sel')];
  const picked = () => boxes().filter((b) => b.checked);
  const update = () => {
    const n = picked().length;
    document.getElementById('bulkCount').textContent = n;
    bar.hidden = !n;
    document.body.classList.toggle('has-bulk', n > 0);
    document.querySelectorAll('[data-sel-all]').forEach((btn) => {
      const list = [...document.querySelectorAll(`[data-section="${btn.dataset.selAll}"] .lead-sel`)];
      btn.textContent = list.length && list.every((b) => b.checked) ? 'keine auswählen' : 'alle auswählen';
    });
  };
  document.addEventListener('change', (e) => { if (e.target.classList?.contains('lead-sel')) update(); });
  document.querySelectorAll('[data-sel-all]').forEach((btn) => btn.addEventListener('click', () => {
    const list = [...document.querySelectorAll(`[data-section="${btn.dataset.selAll}"] .lead-sel`)];
    const on = !list.every((b) => b.checked);
    list.forEach((b) => { b.checked = on; });
    update();
  }));
  document.getElementById('bulkClear').addEventListener('click', () => { boxes().forEach((b) => { b.checked = false; }); update(); });
  document.getElementById('bulkGo').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    const ids = [...new Set(picked().map((b) => b.value))];
    const body = new FormData();
    ids.forEach((id) => body.append('id', id));
    body.append('stage', document.getElementById('bulkStage').value);
    body.append('owner', document.getElementById('bulkOwner').value);
    btn.disabled = true;
    try {
      const resp = await fetch('/leads/ins-crm', { method: 'POST', body, headers: { 'X-CSRF-Token': csrf } });
      const d = await resp.json().catch(() => ({ error: `Fehler ${resp.status}` }));
      if (!resp.ok || d.error) throw new Error(d.error || `Fehler ${resp.status}`);
      picked().forEach((b) => {
        b.checked = false;
        b.closest('li').classList.add('moved');
        const name = b.closest('li').querySelector('.lead-name');
        if (name && !name.querySelector('.in-crm')) name.insertAdjacentHTML('beforeend', ' <em class="in-crm">CRM</em>');
      });
      update();
      const where = d.owner ? `im CRM von ${d.owner}` : 'im Gesamt-CRM';
      const note = document.getElementById('bulkDone') || Object.assign(document.createElement('p'), { id: 'bulkDone', className: 'note small bulk-done' });
      note.textContent = `${d.moved} ${d.moved === 1 ? 'Betrieb' : 'Betriebe'} ${where} unter „${d.stage}“. `;
      if (d.kept.length) note.textContent += `Schon zugeordnet, bleiben bei ihrer Person: ${d.kept.join(', ')}. `;
      const a = Object.assign(document.createElement('a'), { href: d.url, textContent: 'Im CRM ansehen →' });
      note.append(a);
      document.querySelector('.kpis').before(note);
      note.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } catch (err) {
      alert(err.message);
    } finally {
      btn.disabled = false;
    }
  });
})();
