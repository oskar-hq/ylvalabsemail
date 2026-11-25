// Chat: Verlauf ans Ende scrollen, Kanal für die Antwort wählen, Gespräche filtern.
(() => {
  'use strict';
  const verlauf = document.querySelector('[data-ch-verlauf]');
  if (verlauf) verlauf.scrollTop = verlauf.scrollHeight;
  const knoepfe = document.querySelectorAll('[data-ch-kanal]');
  knoepfe.forEach((btn) => btn.addEventListener('click', () => {
    knoepfe.forEach((b) => b.classList.toggle('on', b === btn));
    document.querySelectorAll('[data-ch-form]').forEach((f) => { f.hidden = f.dataset.chForm !== btn.dataset.chKanal; });
    const feld = document.querySelector(`[data-ch-form="${btn.dataset.chKanal}"] textarea`);
    if (feld) feld.focus();
  }));
  const suche = document.querySelector('[data-ch-suche]');
  if (suche) suche.addEventListener('input', () => {
    const q = suche.value.trim().toLowerCase();
    document.querySelectorAll('.ch-ul > li').forEach((li) => { li.hidden = q && !li.textContent.toLowerCase().includes(q); });
  });
})();
