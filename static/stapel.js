// Aufgabenstapel: Tastenkürzel wie im Cockpit (Enter, Esc, ←, J, W, 1–3) und „Ablehnen mit Grund“ aufklappen.
(() => {
  'use strict';
  const tippt = (el) => el && (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT' || el.tagName === 'SELECT' || el.isContentEditable);
  document.addEventListener('keydown', (ev) => {
    if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
    const imFeld = tippt(document.activeElement);
    if (imFeld && ev.key !== 'Escape') return;
    if (imFeld && ev.key === 'Escape') { document.activeElement.blur(); return; }
    const taste = ev.key.length === 1 ? ev.key.toLowerCase() : ev.key;
    const ziel = document.querySelector(`[data-taste="${CSS.escape(taste)}"]`);
    if (!ziel || ziel.closest('[hidden]')) return;
    ev.preventDefault();
    ziel.click();
  });
  document.querySelectorAll('[data-zeigen]').forEach((btn) => btn.addEventListener('click', () => {
    const box = document.getElementById(btn.dataset.zeigen);
    if (!box) return;
    box.hidden = false;
    const feld = box.querySelector('textarea, input:not([type=hidden])');
    if (feld) feld.focus();
  }));
})();
