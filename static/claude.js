// Recherche mit Claude: Prompt in die Zwischenablage
document.getElementById('copyPrompt').addEventListener('click', async () => {
  const box = document.getElementById('promptBox');
  try {
    await navigator.clipboard.writeText(box.value);
  } catch (e) {
    box.select();
    document.execCommand('copy');
  }
  document.getElementById('copied').textContent = 'Kopiert. Jetzt in Claude einfügen.';
});
