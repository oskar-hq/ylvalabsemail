// Konto-Menü schließen, wenn man daneben tippt oder Esc drückt
(() => {
  const menu = document.querySelector('.account');
  if (!menu) return;
  document.addEventListener('click', (ev) => { if (menu.open && !menu.contains(ev.target)) menu.open = false; });
  document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') menu.open = false; });
})();
