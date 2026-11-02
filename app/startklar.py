"""Startklar: Was fehlt noch, damit die Firma mit dem OS arbeiten kann?

Eine Liste von Punkten aus den Einstellungen und Daten, jeder mit Status und dem Link zur Stelle, an der man ihn
erledigt (Einstellungen → Startklar). Prüft nur, was schon gespeichert ist; es wird nichts verschickt und keine KI
gefragt. Verbindungen zu Postfach, KI und Karte testet der Systemcheck.

Wichtigkeit: „pflicht“ (ohne das läuft der Alltag nicht richtig) und „empfohlen“ (geht auch ohne, ist aber besser).
"""
import os
from datetime import datetime, timedelta

PFLICHT, EMPFOHLEN = "pflicht", "empfohlen"


def _env(key):
    return os.environ.get(key, "").strip()


def _punkt(key, titel, ok, text, ziel, knopf="Einrichten", wichtig=PFLICHT):
    return {"key": key, "titel": titel, "ok": bool(ok), "text": text, "ziel": ziel, "knopf": knopf,
            "wichtig": wichtig}


def _liste(namen):
    namen = list(namen)
    if len(namen) <= 1:
        return "".join(namen)
    return ", ".join(namen[:-1]) + " und " + namen[-1]


def punkte(c, url_for, host_backups=False):
    """Alle Punkte in der Reihenfolge, in der man sie am besten abarbeitet."""
    from . import ai, finanzen, firma, kalender
    out = []

    # Personen: jede aktive Person kann sich anmelden (Passwort oder offene Einladung)
    personen = firma.aktive_personen(c)
    ohne = [p["name"] for p in personen if not p["has_password"] and not p["einladung_offen"]]
    eingeladen = [p["name"] for p in personen if not p["has_password"] and p["einladung_offen"]]
    if not personen:
        text, ok = "Noch niemand angelegt.", False
    elif ohne:
        text, ok = f"Noch ohne Zugang: {_liste(ohne)}. In der Person auf „Einladen“ tippen.", False
    elif eingeladen:
        text, ok = f"Alle haben einen Zugang. Eingeladen, aber noch nicht angemeldet: {_liste(eingeladen)}.", True
    else:
        text, ok = f"{len(personen)} Personen, alle können sich anmelden.", True
    out.append(_punkt("personen", "Alle können sich anmelden", ok, text, url_for("personen.liste"), "Personen"))

    # Bereiche: jeder hat eine aktive verantwortliche Person (dort landen Anfragen ohne Person)
    aktiv = {p["key"] for p in personen}
    offen = [b["voll"] for b in firma.bereiche(c) if b["verantwortlich"] not in aktiv]
    out.append(_punkt("bereiche", "Jeder Bereich hat eine verantwortliche Person", not offen,
                      f"Ohne aktive verantwortliche Person: {_liste(offen)}." if offen
                      else "Anfragen und Aufgaben ohne Person landen immer bei jemandem.",
                      url_for("personen.bereiche"), "Bereiche"))

    # Postfach für Antworten, Angebote, Rechnungen und den Kunden-Bereich
    fehlt = [label for key, label in (("SMTP_HOST", "Postausgang"), ("IMAP_HOST", "Posteingang"),
                                      ("OUTREACH_USER", "Adresse"), ("OUTREACH_PASSWORD", "Passwort")) if not _env(key)]
    out.append(_punkt("postfach", "Postfach verbunden", not fehlt,
                      f"Es fehlt noch: {_liste(fehlt)}. Ohne Postfach gehen keine Antworten, Angebote und Rechnungen "
                      "raus." if fehlt else f"{_env('OUTREACH_USER')}. Verbindung testen: Systemcheck.",
                      url_for("settings.group", gid="postfach")))

    an = _env("OUTREACH_EMAIL").lower() in ("1", "true", "yes", "on")
    out.append(_punkt("erstkontakt", "Erstkontakt per Mail eingeschaltet", an,
                      "Freigegebene Erstkontakte gehen per Mail raus (jede Nachricht nur nach Freigabe)." if an
                      else "Ist aus: freigegebene Erstkontakte gehen nur als Brief oder werden von Hand markiert.",
                      url_for("settings.page", gid="postfach", pid="versand")))

    out.append(_punkt("adresse", "Adresse der App eingetragen", bool(_env("BASE_URL")),
                      "Steht in Einladungen, Meldungen und Links für Kunden, z. B. https://os.ylvalabs.de."
                      if not _env("BASE_URL") else _env("BASE_URL"),
                      url_for("settings.group", gid="hinweise")))

    # Firmendaten auf der Rechnung (§ 14 UStG) und Bankverbindung
    r = finanzen.regeln(c)
    fehlt = finanzen.fehlende_angaben(r) + ([] if r["iban"] else ["IBAN"])
    out.append(_punkt("rechnung", "Firmendaten für Rechnungen", not fehlt,
                      f"Es fehlt noch: {_liste(fehlt)}." if fehlt
                      else f"{r['firma_name']}, {finanzen.STEUER[r['steuer']].split(' (')[0]}.",
                      url_for("finanzen.einstellungen")))

    # Sicherung: auf dem Server automatisch oder zuletzt in den letzten 30 Tagen heruntergeladen
    if host_backups:
        ok, text = True, "Der Server sichert automatisch."
    else:
        row = c.execute("SELECT at FROM ereignisse WHERE text='Sicherung heruntergeladen.' ORDER BY id DESC LIMIT 1"
                        ).fetchone()
        grenze = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
        ok = bool(row and row["at"] >= grenze)
        text = (f"Zuletzt am {row['at'][8:10]}.{row['at'][5:7]}.{row['at'][:4]} heruntergeladen." if ok
                else "Einmal herunterladen und außerhalb des Servers ablegen, danach monatlich.")
    out.append(_punkt("sicherung", "Sicherung vorhanden", ok, text, url_for("settings.backup_page"), "Sicherung"))

    # Empfohlen: KI, Google-Kalender, Meldungen aufs Handy
    out.append(_punkt("ki", "KI-Schlüssel eingetragen", ai.available(),
                      "Für Assistent, Entwürfe, Stimmung im Postfach und Lead-Recherche. Ohne Schlüssel läuft alles "
                      "andere nach festen Regeln." if not ai.available() else "Assistent und Entwürfe sind bereit.",
                      url_for("settings.group", gid="ki"), wichtig=EMPFOHLEN))

    if not kalender.google_cfg():
        ok, text = False, "Ohne Google-Zugang laufen Termine nur im OS, ohne Meet-Link und Einladung an Gäste."
    else:
        offen = [p["name"] for p in personen if not kalender.verbunden(c, p["key"])]
        ok = not offen
        text = (f"Noch nicht verbunden: {_liste(offen)} (jede Person unter „Mein Kalender“)." if offen
                else "Alle haben ihren Kalender verbunden.")
    out.append(_punkt("google", "Google-Kalender verbunden", ok, text, url_for("kalender.einstellungen"),
                      wichtig=EMPFOHLEN))

    mit_push = {r["person"] for r in c.execute("SELECT DISTINCT person FROM push_subs WHERE person IS NOT NULL")}
    offen = [p["name"] for p in personen if p["key"] not in mit_push]
    out.append(_punkt("push", "Meldungen aufs Handy", not offen,
                      f"Noch ohne Meldungen: {_liste(offen)}. Die App auf dem Handy zum Home-Bildschirm hinzufügen "
                      "und unter „Mein Profil“ Meldungen erlauben." if offen else "Alle bekommen Meldungen aufs Handy.",
                      url_for("team.profile") + "#meldungen", "Mein Profil", wichtig=EMPFOHLEN))
    return out


def zusammenfassung(liste):
    offen_pflicht = [p for p in liste if not p["ok"] and p["wichtig"] == PFLICHT]
    offen_empfohlen = [p for p in liste if not p["ok"] and p["wichtig"] == EMPFOHLEN]
    return {"pflicht": len(offen_pflicht), "empfohlen": len(offen_empfohlen),
            "fertig": sum(1 for p in liste if p["ok"]), "gesamt": len(liste)}
