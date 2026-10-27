// Betreff antippen: setzt ihn ins Feld (mit Firmenname statt {firma}, wo einer bekannt ist).
document.addEventListener('click', (e) => {
  const btn = e.target.closest('.subject-picks [data-subject]');
  if (!btn) return;
  const box = btn.closest('.subject-picks');
  const input = document.getElementById(box.dataset.for);
  if (!input || input.readOnly) return;
  const firma = box.dataset.firma;
  input.value = firma !== undefined && firma !== '' ? btn.dataset.subject.replace('{firma}', firma) : btn.dataset.subject;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.focus();
});
