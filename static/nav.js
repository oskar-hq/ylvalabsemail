// Konto-Menü schließen, wenn man daneben tippt oder Esc drückt
(() => {
  const menu = document.querySelector('.account');
  if (!menu) return;
  document.addEventListener('click', (ev) => { if (menu.open && !menu.contains(ev.target)) menu.open = false; });
  document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') menu.open = false; });
})();

// Darstellung: Hell, Dunkel oder wie das Gerät (Auto). Gemerkt im Cookie, damit jede Seite gleich richtig startet.
document.querySelectorAll('[data-theme-set]').forEach((btn) => btn.addEventListener('click', () => {
  const theme = btn.dataset.themeSet;
  document.documentElement.dataset.theme = theme;
  document.cookie = `theme=${theme}; path=/; max-age=31536000; SameSite=Lax`;
  document.querySelectorAll('[data-theme-set]').forEach((b) => {
    b.classList.toggle('on', b === btn);
    b.setAttribute('aria-pressed', b === btn ? 'true' : 'false');
  });
  const dark = theme === 'dark' || (theme === 'auto' && matchMedia('(prefers-color-scheme: dark)').matches);
  document.querySelectorAll('meta[name="theme-color"]').forEach((m) => { m.removeAttribute('media'); m.content = dark ? '#0b0b0c' : '#ffffff'; });
}));
