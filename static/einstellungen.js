// Einstellungen: Liste beim Tippen filtern (Titel, Beschreibung und Feldnamen).
(() => {
  const feld = document.querySelector('[data-ein-suche-feld]');
  if (!feld) return;
  feld.addEventListener('input', () => {
    const q = feld.value.trim().toLowerCase();
    let treffer = 0;
    document.querySelectorAll('.ein-seite .menu').forEach((ul) => {
      let n = 0;
      ul.querySelectorAll('li').forEach((li) => { const an = !q || li.dataset.einSuche.includes(q); li.hidden = !an; n += an; });
      ul.hidden = !n; const t = ul.previousElementSibling; if (t && t.classList.contains('menu-title')) t.hidden = !n;
      treffer += n;
    });
    document.querySelectorAll('.ein-blick').forEach((b) => { b.hidden = !!q; });
    document.querySelector('[data-ein-nichts]').hidden = treffer > 0;
  });
})();
