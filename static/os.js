// Firmen-OS: Sheets (von unten, am Griff ziehbar), Toast oben, Abhaken mit Animation, @-Vorschläge in Kommentaren.
// Bewegungsregeln aus docs/DESIGN.md: Federn statt fester Dauer, alles unterbrechbar, weniger Bewegung respektiert.
(() => {
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const reduce = matchMedia('(prefers-reduced-motion: reduce)');
  const buzz = (ms) => { try { if (navigator.vibrate) navigator.vibrate(ms); } catch (_) { /* egal */ } };

  // ---------------------------------------------------------------- Toast
  const CHECK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5 10 17l9-10"/></svg>';
  const CROSS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M7 7l10 10M17 7 7 17"/></svg>';
  let toastTimer;
  function toast(text, isError) {
    let el = document.querySelector('.os-toast');
    if (!el) {
      el = document.createElement('div');
      el.className = 'os-toast';
      el.setAttribute('role', 'status');
      el.setAttribute('aria-live', 'polite');
      document.body.appendChild(el);
    }
    el.innerHTML = `<i>${isError ? CROSS : CHECK}</i><span></span>`;
    el.querySelector('span').textContent = text;
    el.classList.toggle('err', !!isError);
    el.classList.remove('out', 'in');
    void el.offsetWidth;
    el.classList.add('in');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.classList.remove('in'); el.classList.add('out'); }, 2500);
  }
  window.osToast = toast;
  document.querySelectorAll('[data-os-toast]').forEach((el) => {
    toast(el.dataset.osToast, el.dataset.osError === '1');
    el.remove();
  });

  // ---------------------------------------------------------------- Sheets
  function openSheet(dlg) {
    if (!dlg || dlg.open) return;
    dlg.showModal();
    document.body.classList.add('os-sheet-open');
    requestAnimationFrame(() => requestAnimationFrame(() => dlg.classList.add('in')));
    const first = dlg.querySelector('[autofocus], textarea.os-ask, input.os-ask');
    if (first) setTimeout(() => first.focus({ preventScroll: true }), reduce.matches ? 0 : 200);
  }
  function closeSheet(dlg) {
    if (!dlg || !dlg.open) return;
    dlg.style.transform = '';
    dlg.classList.remove('in', 'dragging');
    document.body.classList.remove('os-sheet-open');
    const done = () => { if (!dlg.classList.contains('in')) dlg.close(); };
    if (reduce.matches) { done(); return; }
    dlg.addEventListener('transitionend', done, { once: true });
    setTimeout(done, 600);
  }
  window.osSheet = { open: openSheet, close: closeSheet };

  document.addEventListener('click', (ev) => {
    const opener = ev.target.closest('[data-sheet]');
    if (opener) {
      ev.preventDefault();
      const dlg = document.getElementById(opener.dataset.sheet);
      // Werte vorbelegen (z. B. Bereich bei „Anfrage“): data-set-feld="wert"
      Object.entries(opener.dataset).forEach(([k, v]) => {
        if (!k.startsWith('set') || k === 'sheet') return;
        const name = k.slice(3).toLowerCase();
        dlg?.querySelectorAll(`[name="${name}"]`).forEach((f) => {
          if (f.type === 'radio') f.checked = f.value === v; else f.value = v;
          f.dispatchEvent(new Event('change', { bubbles: true }));
        });
      });
      openSheet(dlg);
      return;
    }
    const closer = ev.target.closest('[data-sheet-close]');
    if (closer) { ev.preventDefault(); closeSheet(closer.closest('dialog')); return; }
    // Tippen auf den abgedunkelten Hintergrund schließt
    if (ev.target.matches('dialog.os-sheet')) {
      const r = ev.target.getBoundingClientRect();
      if (ev.clientY < r.top || ev.clientX < r.left || ev.clientX > r.right) closeSheet(ev.target);
    }
  });
  document.querySelectorAll('dialog.os-sheet').forEach((dlg) => {
    dlg.addEventListener('cancel', (ev) => { ev.preventDefault(); closeSheet(dlg); });
    // Am Griff ziehen: folgt dem Finger 1:1, schließt ab 30 % Weg oder schnellem Wischen, nach oben gedämpft
    const grab = dlg.querySelector('.os-sheet-grab');
    if (!grab) return;
    let startY = 0; let lastY = 0; let lastT = 0; let v = 0; let dragging = false;
    grab.addEventListener('pointerdown', (ev) => {
      dragging = true; startY = lastY = ev.clientY; lastT = ev.timeStamp; v = 0;
      dlg.classList.add('dragging');
      grab.setPointerCapture(ev.pointerId);
    });
    grab.addEventListener('pointermove', (ev) => {
      if (!dragging) return;
      const dy = ev.clientY - startY;
      const dt = Math.max(1, ev.timeStamp - lastT);
      v = (ev.clientY - lastY) / dt * 1000;
      lastY = ev.clientY; lastT = ev.timeStamp;
      const y = dy >= 0 ? dy : -Math.sqrt(-dy) * 2; // Gummiband nach oben
      dlg.style.transform = `translateY(${y}px)`;
    });
    const end = () => {
      if (!dragging) return;
      dragging = false;
      dlg.classList.remove('dragging');
      const dy = lastY - startY;
      if (dy > dlg.offsetHeight * 0.3 || v > 500) closeSheet(dlg);
      else dlg.style.transform = '';
    };
    grab.addEventListener('pointerup', end);
    grab.addEventListener('pointercancel', end);
  });
  // Seite mit #neu (z. B. aus „Mein Tag“) öffnet das passende Sheet
  if (location.hash.length > 1) {
    const dlg = document.getElementById('sheet-' + location.hash.slice(1));
    if (dlg && dlg.matches('dialog.os-sheet')) openSheet(dlg);
  }

  // ---------------------------------------------------------------- Formulare per fetch (Toast statt Neuladen)
  async function post(form) {
    const res = await fetch(form.action, {
      method: 'POST', body: new FormData(form),
      headers: { 'X-CSRF-Token': csrf, 'X-Requested-With': 'fetch', Accept: 'application/json' },
    });
    let data = {};
    try { data = await res.json(); } catch (_) { data = { error: 'Unerwartete Antwort. Bitte Seite neu laden.' }; }
    if (!res.ok || data.error) throw new Error(data.error || 'Hat nicht geklappt.');
    return data;
  }

  // Abhaken: Kreis füllt sich (Feder), Zeile gleitet weg, Toast „Erledigt“
  document.addEventListener('submit', async (ev) => {
    const form = ev.target;
    if (ev.defaultPrevented || !form.matches('form[data-os-check]')) return;
    ev.preventDefault();
    const btn = form.querySelector('.os-check');
    const row = form.closest('.os-row, li');
    btn.classList.add('done');
    btn.disabled = true;
    buzz(10);
    try {
      const data = await post(form);
      toast(data.message || 'Erledigt.');
      if (row && form.dataset.osCheck === 'remove') {
        row.style.maxHeight = row.offsetHeight + 'px';
        setTimeout(() => {
          row.classList.add('leaving');
          requestAnimationFrame(() => row.classList.add('gone'));
          setTimeout(() => row.remove(), 600);
        }, reduce.matches ? 0 : 350);
      } else if (form.dataset.osCheck === 'reload') {
        setTimeout(() => location.reload(), 500);
      }
    } catch (err) {
      btn.classList.remove('done');
      btn.disabled = false;
      toast(err.message, true);
      buzz([30, 40, 30]);
    }
  });

  // Formulare im Sheet: Fehler als Toast, Sheet bleibt offen; Erfolg lädt die Seite neu
  document.addEventListener('submit', async (ev) => {
    const form = ev.target;
    if (ev.defaultPrevented || !form.matches('form[data-os-fetch]')) return;
    ev.preventDefault();
    const btn = form.querySelector('button[type="submit"], button:not([type])');
    if (btn) btn.disabled = true;
    try {
      const data = await post(form);
      buzz(15);
      sessionStorageSafe('os-toast', data.message || 'Gespeichert.');
      const to = form.dataset.osFetch;
      if (to && to !== 'reload') location.href = to; else location.reload();
    } catch (err) {
      toast(err.message, true);
      buzz([30, 40, 30]);
      if (btn) btn.disabled = false;
      const dlg = form.closest('dialog');
      if (dlg && !reduce.matches) {
        dlg.animate([{ transform: 'translateX(0)' }, { transform: 'translateX(-8px)' }, { transform: 'translateX(8px)' },
          { transform: 'translateX(-8px)' }, { transform: 'translateX(8px)' }, { transform: 'translateX(0)' }],
        { duration: 320, easing: 'ease-out', composite: 'add' });
      }
    }
  });
  function sessionStorageSafe(key, value) {
    try { sessionStorage.setItem(key, value); } catch (_) { /* privat: dann eben ohne Toast */ }
  }
  try {
    const pending = sessionStorage.getItem('os-toast');
    if (pending) { sessionStorage.removeItem('os-toast'); toast(pending); }
  } catch (_) { /* egal */ }

  // CTA erst aktiv, wenn das Pflichtfeld gefüllt ist (rosa → rot)
  document.querySelectorAll('form[data-os-require]').forEach((form) => {
    const field = form.querySelector(form.dataset.osRequire);
    const btn = form.querySelector('.os-btn[type="submit"]');
    if (!field || !btn) return;
    const sync = () => { btn.disabled = !field.value.trim(); };
    field.addEventListener('input', sync);
    sync();
  });

  // Aufgabe oder Anfrage: bei „Anfrage“ entscheidet der Bereich, wer es macht
  document.querySelectorAll('[data-os-art]').forEach((form) => {
    const sync = () => {
      const anfrage = form.querySelector('input[name="art"]:checked')?.value === 'anfrage';
      form.querySelectorAll('[data-nur-aufgabe]').forEach((el) => { el.hidden = anfrage; });
      form.querySelectorAll('[data-nur-anfrage]').forEach((el) => { el.hidden = !anfrage; });
      const ask = form.querySelector('.os-ask');
      if (ask) ask.placeholder = anfrage ? 'Was brauchst du?' : 'Was ist zu tun?';
      const label = form.querySelector('[data-cta-label]');
      if (label) label.textContent = anfrage ? 'Anfragen' : 'Anlegen';
    };
    form.addEventListener('change', sync);
    sync();
  });

  // Termin-Kurzwahl füllt das Datum
  document.querySelectorAll('[data-os-quick]').forEach((box) => {
    const date = box.closest('form').querySelector('input[type="date"]');
    box.addEventListener('change', (ev) => {
      const d = new Date();
      const k = ev.target.value;
      if (k === 'morgen') d.setDate(d.getDate() + 1);
      if (k === 'woche') d.setDate(d.getDate() + ((8 - d.getDay()) % 7 || 7));
      if (k === '2w') d.setDate(d.getDate() + 14);
      if (date) date.value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    });
    date?.addEventListener('input', () => box.querySelectorAll('input').forEach((i) => { i.checked = false; }));
  });

  // Einladungslink kopieren
  document.querySelectorAll('[data-os-copy]').forEach((btn) => btn.addEventListener('click', async () => {
    const input = document.getElementById(btn.dataset.osCopy);
    try { await navigator.clipboard.writeText(input.value); } catch (_) { input.select(); document.execCommand('copy'); }
    toast('Link kopiert.');
  }));

  // Bestätigen vor folgenreichen Knöpfen
  document.addEventListener('submit', (ev) => {
    const q = ev.submitter?.dataset.confirm || ev.target.dataset.confirm;
    if (q && !confirm(q)) ev.preventDefault();
  }, true);

  // ---------------------------------------------------------------- @-Erwähnung: Vorschläge beim Tippen
  document.querySelectorAll('textarea[data-os-mention]').forEach((area) => {
    let people = [];
    try { people = JSON.parse(area.dataset.osMention); } catch (_) { return; }
    const box = document.createElement('ul');
    box.className = 'os-mentions';
    box.hidden = true;
    area.parentElement.classList.add('os-mention-box');
    area.parentElement.appendChild(box);
    let matches = []; let active = 0;
    const token = () => {
      const before = area.value.slice(0, area.selectionStart);
      const m = before.match(/(^|\s)@([\wäöüÄÖÜß-]*)$/);
      return m ? m[2] : null;
    };
    const render = () => {
      box.innerHTML = '';
      matches.forEach((p, i) => {
        const li = document.createElement('li');
        const b = document.createElement('button');
        b.type = 'button';
        b.className = i === active ? 'on' : '';
        b.innerHTML = '<span class="os-ico"></span><span></span>';
        b.firstChild.textContent = p.name[0];
        b.lastChild.textContent = p.name;
        b.addEventListener('mousedown', (e) => { e.preventDefault(); pick(p); });
        li.appendChild(b);
        box.appendChild(li);
      });
      box.hidden = !matches.length;
    };
    const pick = (p) => {
      const pos = area.selectionStart;
      const before = area.value.slice(0, pos).replace(/@([\wäöüÄÖÜß-]*)$/, '@' + p.first + ' ');
      area.value = before + area.value.slice(pos);
      area.setSelectionRange(before.length, before.length);
      matches = []; render(); area.focus();
    };
    area.addEventListener('input', () => {
      const t = token();
      if (t === null) { matches = []; render(); return; }
      const q = t.toLowerCase();
      matches = people.filter((p) => p.first.toLowerCase().startsWith(q) || p.key.startsWith(q)).slice(0, 5);
      active = 0; render();
    });
    area.addEventListener('keydown', (ev) => {
      if (!matches.length) return;
      if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
        ev.preventDefault();
        active = (active + (ev.key === 'ArrowDown' ? 1 : matches.length - 1)) % matches.length; render();
      } else if (ev.key === 'Enter' || ev.key === 'Tab') {
        ev.preventDefault(); pick(matches[active]);
      } else if (ev.key === 'Escape') { matches = []; render(); }
    });
    area.addEventListener('blur', () => setTimeout(() => { matches = []; render(); }, 150));
  });
})();
