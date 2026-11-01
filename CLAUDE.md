# Hinweise für die Arbeit an diesem Repository

Diese App wird zum **Firmen-OS von Ylva Labs**: Das Programm ist die Firma, die Menschen sind seine Angestellten.
Vor jeder größeren Änderung lesen:

1. [docs/FIRMEN-OS.md](docs/FIRMEN-OS.md) – Bauplan: Leitbild, Grundsätze, Freigabe-Stufen (Human in the Loop),
   Module, Datenmodell, Baustufen. Erledigte Baustufen dort abhaken, Abweichungen dort eintragen.
2. [docs/ROLLEN-UND-ABLAEUFE.md](docs/ROLLEN-UND-ABLAEUFE.md) – Bereiche, Personen, Entscheidungsrechte, Lernstufen,
   Customer Journey. Das OS setzt diese Regeln durch.
3. [UEBERGABE.md](UEBERGABE.md) und [README.md](README.md) – was heute schon läuft und wie es betrieben wird.

Kurz zur Technik: Python/Flask, SQLite (`app/leaddb.py`, Schema + `MIGRATIONS`), genau ein Gunicorn-Worker mit
Hintergrund-Arbeiter (`app/agent.py`). Tests: `python -m unittest discover tests`. Oberfläche, Doku und
Fachbegriffe auf Deutsch. Erstkontakt per Mail bleibt gesperrt, bis § 7 UWG geklärt ist; nichts geht ohne Freigabe
an Kunden.
