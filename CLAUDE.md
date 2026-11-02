# Hinweise für die Arbeit an diesem Repository

Diese App wird zum **Firmen-OS von Ylva Labs**: Das Programm ist die Firma, die Menschen sind seine Angestellten.
Vor jeder größeren Änderung lesen:

1. [docs/FIRMEN-OS.md](docs/FIRMEN-OS.md) – Bauplan: Leitbild, Grundsätze, Freigabe-Stufen (Human in the Loop),
   Module, Datenmodell, Baustufen. Erledigte Baustufen dort abhaken, Abweichungen dort eintragen.
2. [docs/ROLLEN-UND-ABLAEUFE.md](docs/ROLLEN-UND-ABLAEUFE.md) – Bereiche, Personen, Entscheidungsrechte, Lernstufen,
   Customer Journey. Das OS setzt diese Regeln durch.
3. [UEBERGABE.md](UEBERGABE.md) und [README.md](README.md) – was heute schon läuft und wie es betrieben wird.
4. [docs/BAU-UEBERGABE.md](docs/BAU-UEBERGABE.md) – wo der Bau gerade steht, Arbeitsweise, Muster im Code und der
   Vorschlag für die nächste Stufe. **Wer weiterbaut, fängt hier an.**
5. [docs/DESIGN.md](docs/DESIGN.md) – verbindliches Design („Clean Finance“), hell und dunkel. Bausteine dafür in
   `static/os.css`, `static/os.js` und `templates/_os.html`; neue Seiten nutzen diese statt eigener Stile.

**Grundregel für jeden Code: alles in den Einstellungen, nie auf den Server.** Das OS wird bedient wie eine normale
App. Schlüssel, Zugänge, Personen, Rollen, Rechte, Regeln, Anbindungen und Updates müssen in der Web-App änderbar
sein. Niemand soll für den Alltag per Konsole auf den Server, in die `.env` oder in den Code müssen. Jede neue
Funktion bringt ihre Einstellungen gleich mit (Details: FIRMEN-OS.md, Abschnitt 4.13).

Kurz zur Technik: Python/Flask, SQLite (`app/leaddb.py`, Schema + `MIGRATIONS`), genau ein Gunicorn-Worker mit
Hintergrund-Arbeiter (`app/agent.py`). Tests: `python -m unittest discover tests`. Oberfläche, Doku und
Fachbegriffe auf Deutsch. Erstkontakt per Mail ist erlaubt (§ 7 UWG geklärt, 02.11.2026; Schalter
`OUTREACH_EMAIL` unter Einstellungen → Postfach und Versand); nichts geht ohne Freigabe an Kunden. Einen Demo-Modus
gibt es nicht mehr; lokale Vorschau mit `tests/vorschau.py`.
