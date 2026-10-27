// Anmelden: zwischen „Name und Passwort“ und „E-Mail-Postfach“ wechseln
document.querySelectorAll('[data-mode]').forEach((a) => a.addEventListener('click', (e) => {
  e.preventDefault();
  const team = a.dataset.mode === 'team';
  document.getElementById('teamLogin').hidden = !team;
  document.getElementById('boxLogin').hidden = team;
  document.querySelector(team ? '#teamLogin input[type=password]' : '#boxLogin input[name=user]')?.focus();
}));

// Die zuletzt auf diesem Gerät angemeldete Person kommt ohne Passwort rein, alle anderen brauchen es
(() => {
  const form = document.getElementById('teamLogin');
  if (!form || !form.dataset.known) return;
  const pw = document.getElementById('teamPw'), input = pw.querySelector('input'), go = document.getElementById('teamGo');
  const update = () => {
    const picked = form.querySelector('input[name=member]:checked');
    const skip = picked && picked.value === form.dataset.known;
    pw.hidden = skip; input.required = !skip;
    if (skip) input.value = '';
    go.textContent = skip ? `Weiter als ${form.dataset.knownName}` : 'Anmelden';
    if (!skip) input.focus();
  };
  form.querySelectorAll('input[name=member]').forEach((r) => r.addEventListener('change', update));
})();
