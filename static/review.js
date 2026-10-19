// Betriebe prüfen: Karte nach rechts wischen = gut (ins CRM), nach links = nicht gut. Auch per Knopf und Pfeiltasten.
(() => {
  const root = document.getElementById('review');
  if (!root) return;
  const csrf = document.querySelector('meta[name=csrf-token]').content;
  const stack = document.getElementById('stack');
  const undoBtn = document.getElementById('reviewUndo');
  const leftEl = document.getElementById('reviewLeft');
  const history = [];
  let busy = false;

  const top = () => stack.querySelector('.swipe-card:not(.gone)');
  const post = async (url, data) => {
    const body = new FormData();
    Object.entries(data || {}).forEach(([k, v]) => body.append(k, v));
    const resp = await fetch(url, { method: 'POST', body, headers: { 'X-CSRF-Token': csrf, Accept: 'application/json' } });
    const json = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(json.error || 'Hat nicht geklappt.');
    return json;
  };
  function layout() {
    const cards = [...stack.querySelectorAll('.swipe-card:not(.gone)')];
    cards.forEach((c, i) => { c.hidden = i > 2; c.style.zIndex = String(10 - i); c.dataset.depth = String(i); });
    stack.querySelector('.stack-empty').hidden = cards.length > 0;
    document.getElementById('verdict')?.toggleAttribute('hidden', cards.length === 0);
  }
  async function decide(good) {
    const card = top();
    if (!card || busy) return;
    busy = true;
    card.classList.add('gone', good ? 'to-right' : 'to-left');
    setTimeout(() => { if (card.classList.contains('gone')) card.hidden = true; }, 320);
    history.push(card);
    undoBtn.hidden = false;
    layout();
    try {
      const r = await post(`/leads/${card.dataset.id}/urteil`, { gut: good ? '1' : '0' });
      leftEl.textContent = r.left;
    } catch (e) {
      card.classList.remove('gone', 'to-right', 'to-left');
      history.pop();
      layout();
      alert(e.message);
    } finally { busy = false; }
  }
  async function undo() {
    const card = history.pop();
    if (!card || busy) return;
    busy = true;
    try {
      const r = await post(`/leads/${card.dataset.id}/urteil/zurueck`);
      card.classList.remove('gone', 'to-right', 'to-left');
      card.style.transform = '';
      stack.prepend(card);
      leftEl.textContent = r.left;
      layout();
    } catch (e) { history.push(card); alert(e.message); }
    finally { busy = false; undoBtn.hidden = history.length === 0; }
  }

  // Wischen mit Finger oder Maus
  let drag = null;
  stack.addEventListener('pointerdown', (e) => {
    const card = e.target.closest('.swipe-card');
    if (!card || card !== top() || e.target.closest('a,button') || e.button > 0) return;
    drag = { card, x: e.clientX, y: e.clientY, dx: 0, moved: false };
    card.setPointerCapture(e.pointerId);
    card.classList.add('dragging');
  });
  stack.addEventListener('pointermove', (e) => {
    if (!drag) return;
    drag.dx = e.clientX - drag.x;
    const dy = e.clientY - drag.y;
    if (!drag.moved && Math.abs(dy) > Math.abs(drag.dx) && Math.abs(dy) > 10) {  // eher senkrecht: Seite scrollen
      drag.card.classList.remove('dragging'); drag = null; return;
    }
    if (Math.abs(drag.dx) > 6) drag.moved = true;
    drag.card.style.transform = `translateX(${drag.dx}px) rotate(${drag.dx / 22}deg)`;
    drag.card.classList.toggle('hint-yes', drag.dx > 60);
    drag.card.classList.toggle('hint-no', drag.dx < -60);
  });
  const end = () => {
    if (!drag) return;
    const { card, dx } = drag;
    card.classList.remove('dragging', 'hint-yes', 'hint-no');
    drag = null;
    if (Math.abs(dx) > 100) { card.style.transform = ''; decide(dx > 0); } else card.style.transform = '';
  };
  stack.addEventListener('pointerup', end);
  stack.addEventListener('pointercancel', end);

  root.querySelectorAll('[data-verdict]').forEach((b) => b.addEventListener('click', () => decide(b.dataset.verdict === '1')));
  undoBtn.addEventListener('click', undo);
  document.addEventListener('keydown', (e) => {
    if (e.target.closest('input,textarea,select')) return;
    if (e.key === 'ArrowRight') decide(true);
    else if (e.key === 'ArrowLeft') decide(false);
    else if (e.key.toLowerCase() === 'z') undo();
  });
  // Direkt aus der Karte gemailt oder Brief verschickt: Karte ist erledigt (steht jetzt als „Kontaktiert“ im CRM)
  window.addEventListener('ylva:contacted', (e) => {
    const card = stack.querySelector(`.swipe-card[data-id="${e.detail.id}"]`);
    if (!card || card.classList.contains('gone')) return;
    card.classList.add('gone', 'to-right');
    setTimeout(() => { card.hidden = true; }, 320);
    leftEl.textContent = Math.max(0, parseInt(leftEl.textContent, 10) - 1);
    layout();
  });
  layout();
})();
