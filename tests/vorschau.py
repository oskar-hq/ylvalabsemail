"""Lokale Vorschau zum Entwickeln (ersetzt den früheren Demo-Modus): legt eine Datenbank mit erfundenen Beispieldaten
an und gibt Oskar das Passwort „vorschau123“. Nie auf dem Server benutzen.

    python tests/vorschau.py <ordner>
    DATA_DIR=<ordner> SECRET_KEY=x LEADS_WORKER=false OUTREACH_EMAIL=false flask --app app.main run -p 8097

Dann /login → „Mit Namen“ → Oskar, Passwort vorschau123. Ohne Postfach in den Einstellungen geht keine Mail raus.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

if len(sys.argv) != 2:
    print(__doc__)
    sys.exit(2)
ordner = Path(sys.argv[1]).resolve()
ordner.mkdir(parents=True, exist_ok=True)
os.environ.update(DATA_DIR=str(ordner), LEADS_WORKER="false")

from app import leaddb, team  # noqa: E402
import beispieldaten  # noqa: E402

c = leaddb.connect(ordner / "mailer.db")
beispieldaten.seed(c)
team.set_password(c, "oskar", "vorschau123")
c.commit()
print(f"Fertig: {ordner}. Anmelden als Oskar mit vorschau123.")
