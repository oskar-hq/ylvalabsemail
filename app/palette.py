"""Schnellsuche (⌘K / Strg+K): Seiten, Einstellungen und Betriebe finden und direkt hinspringen.

Die Seiten kommen einmal beim Öffnen und werden im Browser gefiltert; Betriebe sucht der Server beim Tippen.
"""
from flask import Blueprint, jsonify, request, url_for

from . import config_store
from .main import login_required

bp = Blueprint("palette", __name__)

# (Titel, Bereich, Endpoint, Argumente, Stichworte)
PAGES = [
    ("Freigaben", "Start", "stapel.start", {}, "heute stapel freigaben fokus start entscheiden ja nein"),
    ("Aufgaben", "Erinnerungen", "aufgaben.start", {}, "aufgaben erinnerungen todo abhaken erledigen"),
    ("Alle Aufgaben", "Aufgaben", "aufgaben.liste", {}, "liste board aufgaben erledigt delegiert"),
    ("Alle Freigaben", "Freigaben", "freigaben.zentrale", {}, "freigabe zentrale genehmigen entscheiden"),
    ("Pipeline", "Überblick", "pipeline.seite", {}, "agents projekte phase überblick aktivität"),
    ("Chat", "Kunden", "crm.chat", {}, "nachrichten mails slack whatsapp linkedin antworten gespräch"),
    ("Agents", "Agents", "agenten.einstellungen", {}, "grok bots ki agenten mcp"),
    ("Gehirn und Vorlage", "Leads", "leads.brain", {}, "vorlage template text nachricht wen wir suchen"),
    ("Meine Vorlage", "Profil", "team.template_editor", {}, "vorlage template design vorschau text bearbeiten"),
    ("Mein Profil und Passwort", "Profil", "team.profile", {}, "passwort ändern profil unterschrift gruß konto"),
    ("Team und Zahlen", "Team", "team.overview", {}, "statistik stats team zahlen angeschrieben antworten kunden"),
    ("Kosten", "Konto", "leads.costs", {}, "budget geld ki tokens euro"),
    ("Systemcheck", "Konto", "leads.system", {}, "status fehler prüfen verbindung"),
    ("CRM", "CRM · Board", "crm.board", {}, "kanban board kunden leads"),
    ("CRM als Tabelle", "CRM", "crm.board", {"ansicht": "tabelle"}, "tabelle liste sortieren"),
    ("Sonstige Mails", "Chat", "crm.inbox", {"ordner": "sonstige"}, "postfach inbox fehler sonstige"),
    ("Neue Mail schreiben", "Postfach", "crm.compose_mail", {}, "mail senden schreiben email neue nachricht"),
    ("CRM exportieren", "CRM", "crm.export_csv", {}, "export csv excel download"),
    ("Mailer", "Mailer", "compose", {}, "mail schreiben newsletter verschicken"),
    ("Verlauf des Mailers", "Mailer", "history", {}, "gesendet history verlauf"),
    ("Einstellungen", "Einstellungen", "settings.settings", {}, "settings konfiguration"),
    ("Zugang und Sicherheit", "Einstellungen", "settings.access", {}, "login admins domains sicherheit"),
    ("Software und Update", "Einstellungen", "settings.software", {}, "update version aktualisieren server"),
    ("Sicherung", "Einstellungen", "settings.backup_page", {}, "backup sicherung wiederherstellen"),
]
THEMES = [("Darstellung: Hell", "light"), ("Darstellung: Dunkel", "dark"), ("Darstellung: wie das Gerät", "auto")]


def pages():
    out = [{"t": t, "s": sub, "u": url_for(ep, **kw), "k": kw_text} for t, sub, ep, kw, kw_text in PAGES]
    # Jede Unterseite der Einstellungen, mit den Namen ihrer Felder als Stichworte (z. B. „SMTP“ → Postausgang)
    groups = {gid: title for gid, title, _ in config_store.GROUPS}
    for gid, items in config_store.PAGES.items():
        for pid, title, keys in items:
            fields = " ".join(f"{config_store.FIELDS[k]['label']} {k}" for k in keys if k in config_store.FIELDS)
            out.append({"t": title, "s": f"Einstellungen · {groups.get(gid, gid)}",
                        "u": url_for("settings.page", gid=gid, pid=pid), "k": f"{groups.get(gid, '')} {fields}"})
    out += [{"t": t, "s": "Aktion", "theme": key, "k": "theme hell dunkel modus dark light"} for t, key in THEMES]
    return out


@bp.get("/suche.json")
@login_required
def search():
    from .leads_views import db
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify(pages=pages())
    like = f"%{q}%"
    rows = db().execute(
        "SELECT id, name, category_label, city, stage, status FROM leads "
        "WHERE arbeitsbereich=ab() AND (name LIKE ? OR city LIKE ? OR email LIKE ?) AND IFNULL(rejected_by,'') != 'filter' "
        "ORDER BY stage IS NULL, name LIKE ? DESC, updated_at DESC LIMIT 6",
        (like, like, like, f"{q}%")).fetchall()
    leads = [{"t": r["name"], "s": " · ".join(x for x in ("Betrieb", r["category_label"], r["city"]) if x),
              "u": url_for("leads.detail", lead_id=r["id"])} for r in rows]
    return jsonify(leads=leads)
