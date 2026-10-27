// Anmelden: zwischen „Name und Passwort“ und „E-Mail-Postfach“ wechseln
document.querySelectorAll('[data-mode]').forEach((a) => a.addEventListener('click', (e) => {
  e.preventDefault();
  const team = a.dataset.mode === 'team';
  document.getElementById('teamLogin').hidden = !team;
  document.getElementById('boxLogin').hidden = team;
  document.querySelector(team ? '#teamLogin input[type=password]' : '#boxLogin input[name=user]')?.focus();
}));
