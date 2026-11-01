"""Freigabe-Zentrale, Freigabe-Stufen, Entscheidungsrechte und Entscheidungs-Log (Firmen-OS, Stufe 6).

- **Freigabe-Zentrale:** Alles, was auf eine Person wartet, an einer Stelle. Jede Art steht einmal in `ARTEN`
  (Titel, Link, wer prüft, wie entschieden wird). Eine neue Art ist ein Eintrag dort, kein neuer Bildschirm (G10).
  Die meisten Arten liegen in der Tabelle `freigaben` (Lead-Prüfung, Termin-Einladung, Lernstufe, Entscheidung),
  zwei werden aus ihren Daten abgeleitet (Anforderung im Projekt, Erstkontakt-Entwurf).
- **Freigabe-Stufen A–D je Aktion** (docs/FIRMEN-OS.md, Abschnitt 3) stehen als Einstellung in `kv`
  („regel:stufen“). Jede Aktion hat die erlaubten Stufen; was fest ist, steht im Katalog und lässt sich nicht ändern.
  Hart im Code und nie per Einstellung abschaltbar: Erstkontakt per Mail gesperrt (`OUTREACH_EMAIL`, § 7 UWG),
  Sperrliste, nichts an Kunden ohne Freigabe, solange die Aktion auf C oder D steht. Stufe B: Das OS kündigt an und
  führt nach der Einspruchsfrist aus (Takt), wenn niemand stoppt.
- **Entscheidungsrechte** aus docs/ROLLEN-UND-ABLAEUFE.md als Daten (Tabelle `entscheidungsrechte`): wer allein
  entscheidet, was nur gemeinsam geht, Betragsgrenze 200 €.
- **Entscheidungs-Log** (Tabelle `entscheidungen`): was, wer, warum, auf welcher Grundlage. Braucht eine
  Entscheidung nach den Rechten weitere Personen, landet sie bei ihnen in der Zentrale. Änderungen an Regeln,
  Rollen und Rechten trägt das OS selbst ein (`aus_protokoll`, aufgerufen von `firma.log`).
"""
import json
import re
from datetime import datetime, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS entscheidungsrechte (
    key TEXT PRIMARY KEY, titel TEXT NOT NULL, beschreibung TEXT, wer TEXT NOT NULL DEFAULT '[]',
    modus TEXT NOT NULL DEFAULT 'eine', gefragt TEXT NOT NULL DEFAULT '[]', grenze_eur REAL, ab_grenze TEXT,
    sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS entscheidungen (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, wer TEXT NOT NULL, titel TEXT NOT NULL,
    begruendung TEXT, grundlage TEXT, recht TEXT, betrag REAL, status TEXT NOT NULL DEFAULT 'gilt',
    noetig TEXT NOT NULL DEFAULT '[]', bestaetigt TEXT NOT NULL DEFAULT '[]', quelle TEXT NOT NULL DEFAULT 'hand',
    objekt_typ TEXT, objekt_id TEXT, entschieden_am TEXT
);
CREATE INDEX IF NOT EXISTS entscheidungen_at ON entscheidungen(at);
"""

# Startwerte aus docs/ROLLEN-UND-ABLAEUFE.md („Wer was allein entscheiden darf“), einmalig; danach in der App gepflegt.
# wer: verantwortlich (für den eigenen Bereich), selbst (die Person), bereich:<key>, rolle:<key>, person:<key>
SEED = [
    """INSERT OR IGNORE INTO entscheidungsrechte (key, titel, beschreibung, wer, modus, gefragt, grenze_eur, ab_grenze, sort)
       VALUES ('eigener_bereich', 'Alles im eigenen Bereich',
               'Solange es unter der Betragsgrenze bleibt und Kunden nichts Neues verspricht.',
               '["verantwortlich"]', 'eine', '[]', 200, 'gruenderteam', 1)""",
    """INSERT OR IGNORE INTO entscheidungsrechte (key, titel, beschreibung, wer, modus, gefragt, sort)
       VALUES ('lernstufe', 'Innerhalb der eigenen Lernstufe', 'Was die Lernstufe der Person erlaubt.',
               '["selbst"]', 'eine', '[]', 2)""",
    """INSERT OR IGNORE INTO entscheidungsrechte (key, titel, beschreibung, wer, modus, gefragt, sort)
       VALUES ('kundenversprechen', 'Kundenversprechen, die Technikzeit kosten',
               'Zum Beispiel eine Sonderanpassung. Nie ohne die Aufwandsschätzung der Technik.',
               '["bereich:markt", "bereich:technik"]', 'alle', '[]', 3)""",
    """INSERT OR IGNORE INTO entscheidungsrechte (key, titel, beschreibung, wer, modus, gefragt, sort)
       VALUES ('preis', 'Preisänderungen und Garantien', 'Die Technik wird vorher gefragt.',
               '["bereich:markt"]', 'eine', '["bereich:technik"]', 4)""",
    """INSERT OR IGNORE INTO entscheidungsrechte (key, titel, beschreibung, wer, modus, gefragt, grenze_eur, sort)
       VALUES ('gruenderteam', 'Gründerteam',
               'Hauptzielgruppe nach dem Branchentest, Ausgaben ab der Betragsgrenze, Verträge, Einstellungen, Anteile, neue Bereiche.',
               '["rolle:gruender"]', 'alle', '[]', 200, 5)""",
]

STUFEN = {"A": ("Automatisch", "OS macht es, nur Protokoll"),
          "B": ("Automatisch mit Einspruch", "OS kündigt an, führt nach der Frist aus, wenn niemand stoppt"),
          "C": ("Vorschlag und Freigabe", "OS bereitet vor, ein Mensch gibt frei"),
          "D": ("Nur Mensch", "OS legt eine Aufgabe an und sammelt Unterlagen")}


def _a(key, label, standard, erlaubt=None, hinweis="", kommt_mit=None, bereich="Kunden"):
    return {"key": key, "label": label, "standard": standard, "erlaubt": erlaubt or [standard], "hinweis": hinweis,
            "kommt_mit": kommt_mit, "bereich": bereich}


# Was das OS tun kann und auf welcher Stufe. Nur „erlaubt“ mit mehr als einer Stufe ist einstellbar.
AKTIONEN = [
    _a("termin_einladung", "Einladung zu einem Kundentermin verschicken", "C", ["A", "B", "C"],
       "Geht über den Google-Kalender der verantwortlichen Person an den Kunden. Auf B kündigt das OS die Einladung "
       "an und verschickt sie nach der Frist, wenn niemand stoppt."),
    _a("erstkontakt", "Nachricht an einen Lead oder Kunden", "C",
       hinweis="Hart im Code: nie ohne Freigabe. Erstkontakt per Mail bleibt gesperrt, solange § 7 UWG nicht geklärt "
               "ist; die Sperrliste gilt immer."),
    _a("kundenversprechen", "Anforderung freigeben (Versprechen mit Technikzeit)", "C",
       hinweis="Verantwortlich und Technik geben beide frei, nie ohne Aufwandsschätzung (Entscheidungsrechte).",
       bereich="Projekte"),
    _a("abnahme", "Abnahme vor der Übergabe", "D", hinweis="Das letzte Wort hat die verantwortliche Person.",
       bereich="Projekte"),
    _a("lead_pruefung", "Lead für den Kontakt freigeben", "C",
       hinweis="Gesteuert über die Lernstufen: Stufe 1 jeder Lead, Stufe 2 und 3 Stichproben (Branchentest → Regeln).",
       bereich="Akquise"),
    _a("lernstufe", "Aufstieg in die nächste Lernstufe", "C", hinweis="Die führende Person gibt frei.",
       bereich="Team"),
    _a("team_meldungen", "Erinnerungen und Meldungen an das Team", "A", bereich="Team"),
    _a("aufgabe_aus_ereignis", "Aufgaben aus Ereignissen anlegen", "A",
       hinweis="Zum Beispiel AV-Vertrag nach dem Auftrag, Zwischenstand nach der halben Bauzeit.", bereich="Team"),
    _a("lead_aussortieren", "Lead nach den Bewertungsregeln aussortieren", "A",
       hinweis="Läuft nach den Regeln; aussortierte Leads lassen sich zurückholen.", bereich="Akquise"),
    _a("erinnerung_kunde", "Termin-Erinnerung an Kunden (24 h vorher)", "A", ["A", "C"],
       "Mail an die Gäste eines Kundentermins. Auf C wartet sie in der Zentrale, bis jemand freigibt."),
    _a("wochenbericht", "Wochenbericht an Kunden", "B", ["B", "C"],
       "Aus Projektstand, Änderungen und Terminen, am eingestellten Tag (Einstellungen → Kunden-Bereich). Vor dem "
       "Versand am Projekt bearbeitbar."),
    _a("angebot", "Angebot verschicken", "C", ["C", "D"], kommt_mit=8),
    _a("rechnung", "Rechnung verschicken", "C", ["B", "C"], kommt_mit=8),
]
AKTION = {a["key"]: a for a in AKTIONEN}
FRIST_STANDARD = 24  # Stunden, Einspruchsfrist auf Stufe B (Einstellungen → Freigabe-Stufen)

STATUS_LABEL = {"gilt": "Gilt", "wartet": "Wartet auf Zustimmung", "abgelehnt": "Abgelehnt"}
QUELLE_LABEL = {"hand": "Von Hand", "wochengespraech": "Wochengespräch", "regel": "Regel geändert"}


class FreigabeError(ValueError):
    pass


def now():
    return datetime.now().isoformat(timespec="seconds")


def _json(raw, default):
    try:
        v = json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default
    return v if isinstance(v, type(default)) else default


def _kv_get(c, key, default=None):
    row = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return _json(row["value"], default) if row else default


def _kv_set(c, key, value):
    c.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (key, json.dumps(value)))

# ---------------------------------------------------------------- Freigabe-Stufen


def stufen(c):
    """{aktion: stufe} mit den gespeicherten Werten, nur erlaubte Stufen; dazu die Einspruchsfrist."""
    stored = _kv_get(c, "regel:stufen", {})
    out = {}
    for a in AKTIONEN:
        v = stored.get(a["key"], a["standard"])
        out[a["key"]] = v if v in a["erlaubt"] else a["standard"]
    try:
        out["frist_h"] = max(1, min(168, int(stored.get("frist_h", FRIST_STANDARD))))
    except (TypeError, ValueError):
        out["frist_h"] = FRIST_STANDARD
    return out


def stufe(c, aktion):
    return stufen(c)[aktion]


def stufen_speichern(c, wer, form):
    from . import firma
    alt = stufen(c)
    neu = {}
    for a in AKTIONEN:
        if len(a["erlaubt"]) < 2 or a["kommt_mit"]:
            continue
        v = (form.get(a["key"]) or alt[a["key"]]).strip().upper()
        if v not in a["erlaubt"]:
            raise FreigabeError(f"{a['label']}: erlaubt ist {', '.join(a['erlaubt'])}.")
        neu[a["key"]] = v
    raw = str(form.get("frist_h", alt["frist_h"])).strip()
    if not raw.isdigit() or not 1 <= int(raw) <= 168:
        raise FreigabeError("Einspruchsfrist: 1 bis 168 Stunden.")
    neu["frist_h"] = int(raw)
    geaendert = {k: v for k, v in neu.items() if alt.get(k) != v}
    _kv_set(c, "regel:stufen", neu)
    text = "Freigabe-Stufen geändert: " + (", ".join(
        f"{AKTION[k]['label']} → {v}" if k in AKTION else f"Einspruchsfrist {v} h" for k, v in geaendert.items())
        or "nichts geändert") + "."
    firma.log(c, wer, "einstellung", "regel", "stufen", text, geaendert or None)
    return geaendert


def frist(c):
    return (datetime.now() + timedelta(hours=stufen(c)["frist_h"])).isoformat(timespec="seconds")

# ---------------------------------------------------------------- Entscheidungsrechte


def rechte(c):
    out = []
    for r in c.execute("SELECT * FROM entscheidungsrechte ORDER BY sort, titel"):
        d = dict(r)
        d["wer"], d["gefragt"] = _json(d["wer"], []), _json(d["gefragt"], [])
        out.append(d)
    return out


def recht(c, key):
    row = c.execute("SELECT * FROM entscheidungsrechte WHERE key=?", (key or "",)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["wer"], d["gefragt"] = _json(d["wer"], []), _json(d["gefragt"], [])
    return d


def wer_optionen(c):
    """Was im Feld „Wer entscheidet“ wählbar ist: Platzhalter, Bereiche, Rollen, Personen."""
    from . import firma
    out = [("verantwortlich", "Verantwortliche Person des eigenen Bereichs"), ("selbst", "Die Person selbst")]
    out += [(f"bereich:{b['key']}", f"Verantwortlich für {b['voll']}") for b in firma.bereiche(c)]
    out += [(f"rolle:{r['key']}", f"Alle mit der Rolle {r['name']}") for r in firma.rollen(c)]
    out += [(f"person:{p['key']}", p["name"]) for p in firma.aktive_personen(c)]
    return out


def wer_text(c, tokens):
    labels = dict(wer_optionen(c))
    return ", ".join(labels.get(t, t) for t in tokens) or "niemand"


def aufloesen(c, tokens, person=None):
    """Platzhalter → Schlüssel aktiver Personen. `person` ist, wer die Entscheidung trifft oder vorschlägt."""
    from . import firma
    out = []
    for t in tokens:
        keys = []
        if t == "selbst" and person:
            keys = [person]
        elif t == "verantwortlich" and person:
            # wer einen Bereich verantwortet, entscheidet dort selbst; sonst die Verantwortlichen der eigenen Bereiche
            if firma.verantwortet(c, person):
                keys = [person]
            else:
                keys = [b["verantwortlich"] for b in (firma.bereich(c, k) for k in firma.bereiche_von(c, person))
                        if b and b["verantwortlich"]]
        elif t.startswith("bereich:"):
            b = firma.bereich(c, t[8:])
            keys = [b["verantwortlich"]] if b and b["verantwortlich"] else []
        elif t.startswith("rolle:"):
            keys = [p["key"] for p in firma.aktive_personen(c) if p["rolle"] == t[6:]]
        elif t.startswith("person:"):
            keys = [t[7:]]
        out += [k for k in keys if k and k not in out and firma.aktive_person(c, k)]
    return out


def recht_speichern(c, wer, key, titel, beschreibung, wer_tokens, modus, gefragt, grenze_eur=None, ab_grenze=None):
    from . import firma
    titel = re.sub(r"\s+", " ", titel or "").strip()
    if not titel or len(titel) > 120:
        raise FreigabeError("Bitte einen Titel (höchstens 120 Zeichen).")
    gueltig = {k for k, _ in wer_optionen(c)}
    wer_tokens = [t for t in dict.fromkeys(wer_tokens) if t]
    gefragt = [t for t in dict.fromkeys(gefragt or []) if t and t not in wer_tokens]
    if not wer_tokens:
        raise FreigabeError("Bitte wählen, wer entscheidet.")
    if any(t not in gueltig for t in wer_tokens + gefragt):
        raise FreigabeError("Unbekannte Auswahl bei „Wer entscheidet“.")
    if modus not in ("eine", "alle"):
        raise FreigabeError("Bitte wählen: eine Person reicht oder alle gemeinsam.")
    grenze = None
    if str(grenze_eur or "").strip():
        try:
            grenze = round(float(str(grenze_eur).replace(".", "").replace(",", ".")), 2)
        except ValueError as exc:
            raise FreigabeError("Betragsgrenze bitte als Zahl in Euro.") from exc
        if grenze < 0:
            raise FreigabeError("Betragsgrenze bitte als positive Zahl.")
    ab_grenze = ab_grenze or None
    if ab_grenze and (ab_grenze == key or not recht(c, ab_grenze)):
        raise FreigabeError("„Ab der Grenze entscheidet“ passt nicht.")
    neu = not key
    if neu:
        key = firma.slug(titel, {r["key"] for r in c.execute("SELECT key FROM entscheidungsrechte")})
        sort = (c.execute("SELECT MAX(sort) FROM entscheidungsrechte").fetchone()[0] or 0) + 1
        c.execute("INSERT INTO entscheidungsrechte (key, titel, beschreibung, wer, modus, gefragt, grenze_eur, "
                  "ab_grenze, sort) VALUES (?,?,?,?,?,?,?,?,?)",
                  (key, titel, (beschreibung or "").strip()[:500], json.dumps(wer_tokens), modus, json.dumps(gefragt),
                   grenze, ab_grenze, sort))
    else:
        if not recht(c, key):
            raise FreigabeError("Dieses Entscheidungsrecht gibt es nicht.")
        c.execute("UPDATE entscheidungsrechte SET titel=?, beschreibung=?, wer=?, modus=?, gefragt=?, grenze_eur=?, "
                  "ab_grenze=? WHERE key=?", (titel, (beschreibung or "").strip()[:500], json.dumps(wer_tokens), modus,
                                              json.dumps(gefragt), grenze, ab_grenze, key))
    firma.log(c, wer, "einstellung", "regel", "entscheidungsrechte",
              f"Entscheidungsrecht „{titel}“ {'angelegt' if neu else 'geändert'}: {wer_text(c, wer_tokens)}"
              f"{' (alle gemeinsam)' if modus == 'alle' else ''}"
              + (f", Grenze {grenze:g} €" if grenze is not None else "") + ".",
              {"recht": key, "wer": wer_tokens, "modus": modus, "gefragt": gefragt, "grenze_eur": grenze})
    return key


def recht_loeschen(c, wer, key):
    from . import firma
    r = recht(c, key)
    if not r:
        raise FreigabeError("Dieses Entscheidungsrecht gibt es nicht.")
    if c.execute("SELECT 1 FROM entscheidungsrechte WHERE ab_grenze=?", (key,)).fetchone():
        raise FreigabeError("Ein anderes Recht verweist ab der Betragsgrenze hierauf. Erst dort ändern.")
    c.execute("DELETE FROM entscheidungsrechte WHERE key=?", (key,))
    firma.log(c, wer, "einstellung", "regel", "entscheidungsrechte", f"Entscheidungsrecht „{r['titel']}“ gelöscht.")


def wer_entscheidet(c, key, person, betrag=None):
    """Welches Recht gilt (bei Betrag über der Grenze das übergeordnete), wer muss zustimmen, wer wird gefragt."""
    r = recht(c, key)
    if not r:
        raise FreigabeError("Bitte wählen, nach welchem Recht entschieden wird.")
    gesehen = set()
    while betrag is not None and r["grenze_eur"] is not None and betrag >= r["grenze_eur"] and r["ab_grenze"] \
            and r["key"] not in gesehen:
        gesehen.add(r["key"])
        r = recht(c, r["ab_grenze"]) or r
    noetig = aufloesen(c, r["wer"], person)
    gefragt = [p for p in aufloesen(c, r["gefragt"], person) if p not in noetig]
    return r, noetig, gefragt

# ---------------------------------------------------------------- Entscheidungs-Log


def entscheidung_get(c, eid):
    row = c.execute("SELECT e.*, t.name AS wer_name, r.titel AS recht_titel FROM entscheidungen e "
                    "LEFT JOIN team t ON t.key = e.wer LEFT JOIN entscheidungsrechte r ON r.key = e.recht "
                    "WHERE e.id=?", (eid,)).fetchone()
    return _entscheidung(c, row) if row else None


def _entscheidung(c, row):
    from . import firma
    d = dict(row)
    d["noetig"], d["bestaetigt"] = _json(d["noetig"], []), _json(d["bestaetigt"], [])
    d["wer_name"] = d.get("wer_name") or d["wer"]
    d["fehlt"] = [firma.name_von(c, p) for p in d["noetig"] if p not in d["bestaetigt"]]
    d["status_label"] = STATUS_LABEL.get(d["status"], d["status"])
    d["quelle_label"] = QUELLE_LABEL.get(d["quelle"], d["quelle"])
    return d


def entscheidungen(c, von=None, bis=None, status=None, limit=200):
    where, args = ["1"], []
    if von:
        where.append("e.at >= ?")
        args.append(von)
    if bis:
        where.append("e.at < ?")
        args.append(bis)
    if status:
        where.append("e.status = ?")
        args.append(status)
    rows = c.execute("SELECT e.*, t.name AS wer_name, r.titel AS recht_titel FROM entscheidungen e "
                     "LEFT JOIN team t ON t.key = e.wer LEFT JOIN entscheidungsrechte r ON r.key = e.recht "
                     f"WHERE {' AND '.join(where)} ORDER BY e.id DESC LIMIT ?", [*args, limit])
    return [_entscheidung(c, r) for r in rows]


def _betrag(v):
    if v in (None, ""):
        return None
    s = str(v).strip().replace(" ", "").replace("€", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        b = round(float(s), 2)
    except ValueError as exc:
        raise FreigabeError("Betrag bitte als Zahl in Euro.") from exc
    if b < 0:
        raise FreigabeError("Betrag bitte als positive Zahl.")
    return b


def entscheiden_festhalten(c, wer, titel, begruendung="", grundlage="", recht_key="", betrag=None, quelle="hand"):
    """Eine Entscheidung festhalten. Reicht die Person allein (nach dem Recht), gilt sie sofort. Sonst wartet sie
    in der Freigabe-Zentrale auf die übrigen (bei „alle“ auf jede, bei „eine“ auf eine von ihnen)."""
    from . import aufgaben, firma
    titel = re.sub(r"\s+", " ", titel or "").strip()
    if not titel or len(titel) > 200:
        raise FreigabeError("Was wurde entschieden? Bitte in einem Satz (höchstens 200 Zeichen).")
    begruendung = (begruendung or "").strip()
    if len(begruendung) < 3:
        raise FreigabeError("Bitte kurz begründen, warum. Daraus lernt das Team später.")
    if quelle not in QUELLE_LABEL:
        quelle = "hand"
    betrag = _betrag(betrag)
    r, noetig, gefragt = wer_entscheidet(c, recht_key, wer, betrag)
    if not noetig:
        raise FreigabeError("Nach diesem Recht entscheidet gerade niemand (keine aktive Person). Erst unter "
                            "Einstellungen → Entscheidungsrechte ändern.")
    bestaetigt = [wer] if wer in noetig else []
    fertig = bool(bestaetigt) and (r["modus"] == "eine" or set(noetig) <= set(bestaetigt))
    cur = c.execute("INSERT INTO entscheidungen (at, wer, titel, begruendung, grundlage, recht, betrag, status, noetig, "
                    "bestaetigt, quelle, entschieden_am) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (now(), wer, titel, begruendung[:2000], (grundlage or "").strip()[:1000] or None, r["key"], betrag,
                     "gilt" if fertig else "wartet", json.dumps(noetig), json.dumps(bestaetigt), quelle,
                     now() if fertig else None))
    eid = cur.lastrowid
    firma.log(c, wer, "entscheidung", "entscheidung", eid, f"Entscheidung {'festgehalten' if fertig else 'vorgeschlagen'}: "
              f"{titel}", {"recht": r["key"], "betrag": betrag, "noetig": noetig}, commit=False)
    name = firma.name_von(c, wer)
    if not fertig:
        offen = [p for p in noetig if p not in bestaetigt]
        for p in offen:
            c.execute("INSERT INTO freigaben (art, objekt_typ, objekt_id, stufe, angefragt_von, angefragt_am, pruefer) "
                      "VALUES ('entscheidung', 'entscheidung', ?, 'C', ?, ?, ?)", (eid, wer, now(), p))
    c.commit()
    if not fertig:
        for p in noetig:
            if p not in bestaetigt:
                aufgaben.melden(c, p, f"Entscheidung braucht dich: {titel}",
                                f"{name} · nach „{r['titel']}“" + (f" · {betrag:g} €" if betrag is not None else ""),
                                "/freigaben", push=True)
    for p in gefragt:
        if p != wer:
            aufgaben.melden(c, p, f"Du wirst gefragt: {titel}", f"{name}: {begruendung[:140]}", "/entscheidungen")
    return eid


def entscheidung_bestaetigen(c, wer, fid, ok, grund=""):
    """Zustimmen oder ablehnen (mit Grund) als eine der nötigen Personen."""
    from . import aufgaben, firma
    f = c.execute("SELECT * FROM freigaben WHERE id=? AND art='entscheidung'", (fid,)).fetchone()
    if not f or f["status"] != "offen":
        raise FreigabeError("Diese Entscheidung wartet nicht (mehr) auf dich.")
    if wer != f["pruefer"]:
        raise FreigabeError("Zustimmen kann nur, wer nach dem Entscheidungsrecht gefragt ist.")
    e = entscheidung_get(c, int(f["objekt_id"]))
    grund = (grund or "").strip()
    if not ok and len(grund) < 3:
        raise FreigabeError("Bitte einen Grund angeben.")
    c.execute("UPDATE freigaben SET status=?, grund=?, entschieden_von=?, entschieden_am=? WHERE id=?",
              ("angenommen" if ok else "abgelehnt", grund or None, wer, now(), fid))
    r = recht(c, e["recht"]) or {"modus": "alle", "titel": e["recht"]}
    if ok:
        bestaetigt = list(dict.fromkeys([*e["bestaetigt"], wer]))
        fertig = r["modus"] == "eine" or set(e["noetig"]) <= set(bestaetigt)
        c.execute("UPDATE entscheidungen SET bestaetigt=?, status=?, entschieden_am=? WHERE id=?",
                  (json.dumps(bestaetigt), "gilt" if fertig else "wartet", now() if fertig else None, e["id"]))
        if fertig:  # bei „eine reicht“ sind die übrigen Anfragen erledigt
            c.execute("UPDATE freigaben SET status='hinfaellig' WHERE art='entscheidung' AND objekt_id=? AND status='offen'",
                      (str(e["id"]),))
    else:
        fertig = True
        c.execute("UPDATE entscheidungen SET status='abgelehnt', entschieden_am=? WHERE id=?", (now(), e["id"]))
        c.execute("UPDATE freigaben SET status='hinfaellig' WHERE art='entscheidung' AND objekt_id=? AND status='offen'",
                  (str(e["id"]),))
    firma.log(c, wer, "entscheidung", "entscheidung", e["id"],
              f"{'Zugestimmt' if ok else 'Abgelehnt'}: {e['titel']}" + (f" ({grund})" if grund else ""), commit=False)
    c.commit()
    if e["wer"] != wer and fertig:
        status = "gilt jetzt" if ok else "abgelehnt"
        aufgaben.melden(c, e["wer"], f"Entscheidung {status}: {e['titel']}",
                        grund or f"{firma.name_von(c, wer)} hat zugestimmt.", "/entscheidungen")
    return entscheidung_get(c, e["id"])


def aus_protokoll(c, wer, art, objekt_typ, objekt_id, text):
    """Von `firma.log` aufgerufen: Änderungen an Regeln, Rollen und Rechten sind selbst Entscheidungen (4.11).
    Schreibt ohne commit, im selben Schritt wie das Protokoll."""
    if not ((art == "einstellung" and objekt_typ in ("regel", "kpi")) or art == "rolle"):
        return
    if not wer or wer in ("System", "Konsole") or not text:
        return
    c.execute("INSERT INTO entscheidungen (at, wer, titel, begruendung, grundlage, status, noetig, bestaetigt, quelle, "
              "objekt_typ, objekt_id, entschieden_am) VALUES (?,?,?,?,?, 'gilt', ?, ?, 'regel', ?, ?, ?)",
              (now(), wer, text[:200], "In den Einstellungen geändert.", "Firmen-Protokoll", json.dumps([wer]),
               json.dumps([wer]), objekt_typ, None if objekt_id is None else str(objekt_id), now()))

# ---------------------------------------------------------------- Freigabe-Zentrale: die Arten


def _person_name(c, key):
    from . import firma
    return firma.name_von(c, key) if key else ""


def _lead_pruefung(c, f):
    row = c.execute("SELECT l.name, l.category_label, l.city, b.name AS branche FROM leads l LEFT JOIN testbranchen b "
                    "ON b.key = l.testbranche WHERE l.id=?", (f["objekt_id"],)).fetchone()
    if not row:
        return None
    teile = [row["category_label"], row["city"], row["branche"]]
    return {"titel": row["name"], "text": " · ".join(x for x in teile if x),
            "eyebrow": "Stichprobe" if f["stufe"] == "stichprobe" else "Lead prüfen",
            "url": f"/leads/{f['objekt_id']}", "frage": "Warum passt er nicht?",
            "folge": "Abgelehnt wird der Lead aussortiert; die recherchierende Person bekommt den Grund."}


def _termin_einladung(c, f):
    from . import kalender
    t = kalender.get(c, f["objekt_id"])
    if not t:
        return None
    return {"titel": t["titel"], "text": f"{t['zeit']} · an {', '.join(t['gaeste'])}", "eyebrow": "Einladung an Kunden",
            "url": f"/kalender/{t['id']}", "frage": "Warum geht die Einladung nicht raus?",
            "folge": "Verworfen wird der Termin abgesagt."}


def _lernstufe(c, f, mit_stand=True):
    v = _json(f["vorschlag"], {})
    text = ""
    if mit_stand:
        from . import kpis
        text = kpis.lernstand(c, v.get("person"))["text"]
    return {"titel": f"{_person_name(c, v.get('person'))}: Lernstufe {v.get('nach')}", "text": text,
            "eyebrow": "Aufstieg", "url": "/wochengespraech", "frage": "Was fehlt noch?",
            "nein": "Noch nicht"}


def _entscheidung_art(c, f):
    e = entscheidung_get(c, int(f["objekt_id"]))
    if not e:
        return None
    teile = [f"nach „{e['recht_titel'] or e['recht']}“", f"{e['betrag']:g} €" if e["betrag"] is not None else "",
             e["begruendung"][:120]]
    return {"titel": e["titel"], "text": " · ".join(x for x in teile if x), "eyebrow": "Entscheidung",
            "url": f"/entscheidungen#e{e['id']}", "frage": "Warum nicht?", "ja": "Zustimmen"}


def _entscheide_lead(c, wer, f, ok, grund):
    from . import branchentest
    if not branchentest.darf_entscheiden(c, wer, dict(f)):
        raise FreigabeError("Prüfen darf die eingetragene Person (oder wer Leads verwaltet).")
    branchentest.entscheiden(c, wer, f["id"], ok, grund)


def _entscheide_termin(c, wer, f, ok, grund):
    from . import kalender
    kalender.freigeben(c, wer, f["objekt_id"], ok, grund)


def _entscheide_lernstufe(c, wer, f, ok, grund):
    from . import kpis
    kpis.aufstieg_entscheiden(c, wer, f["id"], ok, grund)


def _entscheide_entscheidung(c, wer, f, ok, grund):
    entscheidung_bestaetigen(c, wer, f["id"], ok, grund)


def _darf_alle(c, wer, f):
    from . import firma
    return wer == f["pruefer"] or firma.darf(c, wer, ARTEN[f["art"]]["modul"], firma.VERWALTEN)


def _wochenbericht(c, f):
    from . import portal, projekte
    b = portal.bericht_get(c, int(f["objekt_id"]))
    if not b:
        return None
    p = projekte.get(c, b["projekt_id"])
    return {"titel": b["betreff"], "text": f"an {', '.join(portal.empfaenger(c, p)) or 'niemand'} · "
            + b["text"].split("\n")[4][:120], "eyebrow": "Wochenbericht an Kunden",
            "url": f"/kunden/{b['projekt_id']}#berichte", "frage": "Warum geht der Bericht nicht raus?",
            "ja": "Verschicken", "nein": "Nicht schicken"}


def _erinnerung_kunde(c, f):
    from . import kalender
    t = kalender.get(c, f["objekt_id"])
    if not t:
        return None
    return {"titel": f"Erinnerung: {t['titel']}", "text": f"{t['zeit']} · an {', '.join(t['gaeste'])}",
            "eyebrow": "Erinnerung an Kunden", "url": f"/kalender/{t['id']}", "frage": "Warum nicht erinnern?",
            "ja": "Verschicken", "nein": "Nicht schicken"}


def _entscheide_bericht(c, wer, f, ok, grund):
    from . import portal
    try:
        portal.bericht_entscheiden(c, wer, int(f["objekt_id"]), ok, grund)
    except portal.PortalError as exc:
        raise FreigabeError(str(exc)) from exc


def _entscheide_erinnerung(c, wer, f, ok, grund):
    from . import portal
    try:
        portal.erinnerung_entscheiden(c, wer, int(f["objekt_id"]), ok, grund)
    except portal.PortalError as exc:
        raise FreigabeError(str(exc)) from exc


def _darf_selbst(c, wer, f):
    return wer == f["pruefer"]


# Arten mit Zeilen in `freigaben`. Neue Art: Eintrag hier (Beschreibung, Entscheiden-Funktion, Modul für „Verwalten“).
ARTEN = {
    "lead_pruefung": {"label": "Lead-Prüfungen", "modul": "leads", "beschreiben": _lead_pruefung,
                      "entscheiden": _entscheide_lead, "darf": _darf_alle},
    "termin_einladung": {"label": "Einladungen an Kunden", "modul": "kalender", "beschreiben": _termin_einladung,
                         "entscheiden": _entscheide_termin, "darf": _darf_alle},
    "lernstufe": {"label": "Aufstiege", "modul": "personen", "beschreiben": _lernstufe,
                  "entscheiden": _entscheide_lernstufe, "darf": _darf_alle},
    "entscheidung": {"label": "Entscheidungen", "modul": "entscheidungen", "beschreiben": _entscheidung_art,
                     "entscheiden": _entscheide_entscheidung, "darf": _darf_selbst},
    "wochenbericht": {"label": "Wochenberichte an Kunden", "modul": "kunden", "beschreiben": _wochenbericht,
                      "entscheiden": _entscheide_bericht, "darf": _darf_alle},
    "erinnerung_kunde": {"label": "Erinnerungen an Kunden", "modul": "kalender", "beschreiben": _erinnerung_kunde,
                         "entscheiden": _entscheide_erinnerung, "darf": _darf_alle},
}
# Aus den Daten abgeleitet (keine Zeile in `freigaben`): nur mit Link zum Ort, wo entschieden wird
ABGELEITET = {"anforderung": "Anforderungen im Projekt", "erstkontakt": "Erstkontakt-Entwürfe"}
ART_LABEL = {**{k: v["label"] for k, v in ARTEN.items()}, **ABGELEITET}


def _sichtbar(c, wer, f):
    """Sieht `wer` diese Freigabe in „Alle offenen“? (Prüfende Person oder wer das Modul verwaltet.)"""
    from . import firma
    art = ARTEN.get(f["art"])
    return bool(art) and (wer == f["pruefer"] or firma.darf(c, wer, art["modul"], firma.VERWALTEN))


def _projekte_offen(c, person=None):
    rows = c.execute("""SELECT p.*, l.name AS kunde FROM projekte p LEFT JOIN leads l ON l.id = p.lead_id
                        WHERE p.status='anforderung' AND IFNULL(p.anforderung,'') != '' AND p.kunde_bestaetigt_am
                        IS NOT NULL AND p.schaetzung_h IS NOT NULL
                        AND (p.freigabe_markt_von IS NULL OR p.freigabe_technik_von IS NULL)""").fetchall()
    out = []
    for p in rows:
        fehlt = [k for k, feld in (("verantwortlich", "freigabe_markt_von"), ("technik", "freigabe_technik_von"))
                 if not p[feld] and p[k]]
        for k in fehlt:
            if person and p[k] != person:
                continue
            out.append({"art": "anforderung", "oid": p["id"], "pruefer": p[k],
                        "titel": f"Anforderung: {p['titel']}", "eyebrow": "Kundenversprechen",
                        "text": f"{p['kunde'] or 'Kunde'} · {p['schaetzung_h']:g} Stunden geschätzt · "
                                f"{'Was' if k == 'verantwortlich' else 'Wie'} fehlt noch",
                        "url": f"/kunden/{p['id']}", "seit": p["kunde_bestaetigt_am"], "von": None,
                        "ja": "Freigeben", "nein": None})
    return out


def _entwuerfe_offen(c, person=None, alle=False):
    from . import firma, kpis
    out = []
    rows = c.execute("SELECT id, name, category_label, owner, entwurf_von, updated_at FROM leads "
                     "WHERE status='entwurf' AND interest IS NULL ORDER BY updated_at").fetchall()
    if not rows:
        return out
    aktive = [p["key"] for p in firma.aktive_personen(c)]
    verwalten = {p for p in aktive if firma.darf(c, p, "leads", firma.VERWALTEN)}
    freigeben = [p for p in aktive if firma.darf(c, p, "leads", firma.FREIGEBEN)]
    je_autor = {}  # wer prüft die Entwürfe einer Person: wer sie führt oder Leads verwaltet
    for l in rows:
        autor = l["entwurf_von"]
        if autor:
            if autor not in je_autor:
                je_autor[autor] = [p for p in aktive if p != autor and (p in verwalten or kpis.fuehrt(c, p, autor))]
            fuer = je_autor[autor]
        elif l["owner"]:
            fuer = [l["owner"]]
        else:
            fuer = freigeben
        if person and person not in fuer:
            continue
        if not person and not alle:
            continue
        out.append({"art": "erstkontakt", "oid": l["id"], "pruefer": person or (fuer[0] if fuer else None),
                    "titel": l["name"], "eyebrow": "Erstkontakt-Entwurf",
                    "text": " · ".join(x for x in (l["category_label"],
                                                   f"von {firma.name_von(c, l['entwurf_von'])}" if l["entwurf_von"]
                                                   else "Vorlage oder KI") if x),
                    "url": f"/leads/{l['id']}", "seit": l["updated_at"], "von": l["entwurf_von"],
                    "ja": None, "nein": None, "nur_link": "In der Akte prüfen"})
    return out


def offen(c, person, alle=False):
    """Alle offenen Freigaben für `person` (oder mit alle=True: alle, die sie sehen darf), älteste zuerst.
    Einträge auf Stufe B (mit Frist) zuerst, weil sie sonst von selbst passieren."""
    from . import firma
    out = []
    rows = c.execute("SELECT * FROM freigaben WHERE status='offen' ORDER BY id").fetchall()
    for f in rows:
        art = ARTEN.get(f["art"])
        if not art or (not alle and f["pruefer"] != person) or (alle and not _sichtbar(c, person, f)):
            continue
        info = art["beschreiben"](c, f)
        if not info:
            continue
        frist_text = _json(f["vorschlag"], {}).get("frist") if f["stufe"] == "B" else None
        out.append({"art": f["art"], "oid": f["id"], "pruefer": f["pruefer"], "pruefer_name": _person_name(c, f["pruefer"]),
                    "von": f["angefragt_von"], "von_name": _person_name(c, f["angefragt_von"]),
                    "seit": f["angefragt_am"], "stufe": f["stufe"], "frist": frist_text,
                    "ja": info.get("ja", "Freigeben" if f["art"] != "lead_pruefung" else "Annehmen"),
                    "nein": info.get("nein", "Ablehnen"), "darf": art["darf"](c, person, f), **info})
    if alle and firma.darf(c, person, "kunden", firma.VERWALTEN):
        out += _projekte_offen(c)
    else:
        out += _projekte_offen(c, person)
    out += _entwuerfe_offen(c, person if not alle else None, alle=alle and firma.darf(c, person, "leads", firma.VERWALTEN))
    for x in out:
        x.setdefault("pruefer_name", _person_name(c, x.get("pruefer")))
        x.setdefault("von_name", _person_name(c, x.get("von")))
        x.setdefault("darf", x.get("pruefer") == person)
        x.setdefault("stufe", "C")
        x.setdefault("frist", None)
        x["art_label"] = ART_LABEL.get(x["art"], x["art"])
    out.sort(key=lambda x: (x["stufe"] != "B", x.get("seit") or ""))
    return out


def zaehlen(c, person):
    """Für Navigation und Mein Tag: wie viele Freigaben warten auf die Person (ohne aufwändige Beschreibungen)."""
    if not person:
        return 0
    n = c.execute(f"SELECT COUNT(*) FROM freigaben WHERE status='offen' AND pruefer=? AND art IN "
                  f"({','.join('?' * len(ARTEN))})", (person, *ARTEN)).fetchone()[0]
    return n + len(_projekte_offen(c, person)) + len(_entwuerfe_offen(c, person))


def offen_je_art(c):
    """Für das Wochengespräch: {Bezeichnung: Anzahl} aller offenen Freigaben im Team."""
    out = {}
    for r in c.execute("SELECT art, COUNT(*) n FROM freigaben WHERE status='offen' GROUP BY art"):
        out[ART_LABEL.get(r["art"], r["art"])] = r["n"]
    n = len(_projekte_offen(c))
    if n:
        out[ABGELEITET["anforderung"]] = n
    n = c.execute("SELECT COUNT(*) FROM leads WHERE status='entwurf' AND interest IS NULL").fetchone()[0]
    if n:
        out[ABGELEITET["erstkontakt"]] = n
    return out


def entscheiden(c, wer, art, oid, ok, grund=""):
    """Aus der Zentrale annehmen oder ablehnen. Jede Art prüft selbst, wer darf, und protokolliert."""
    if art == "anforderung":
        if not ok:
            raise FreigabeError("Eine Anforderung lehnt man nicht ab: Im Projekt ändern, dann geben beide neu frei.")
        from . import projekte
        projekte.anforderung_freigeben(c, wer, oid)
        return
    if art not in ARTEN:
        raise FreigabeError("Diese Freigabe gibt es nicht.")
    f = c.execute("SELECT * FROM freigaben WHERE id=? AND art=?", (oid, art)).fetchone()
    if not f or f["status"] != "offen":
        raise FreigabeError("Diese Freigabe ist schon entschieden.")
    ARTEN[art]["entscheiden"](c, wer, f, ok, grund)


def stoppen(c, wer, fid):
    """Einspruch auf Stufe B: Die Aktion passiert nicht von selbst, sondern wartet auf eine normale Freigabe (C)."""
    from . import firma
    f = c.execute("SELECT * FROM freigaben WHERE id=?", (fid,)).fetchone()
    if not f or f["status"] != "offen" or f["stufe"] != "B":
        raise FreigabeError("Hier läuft keine Frist.")
    if not _darf_alle(c, wer, f) and wer != f["angefragt_von"]:
        raise FreigabeError("Stoppen darf die prüfende Person (oder wer das Modul verwaltet).")
    v = _json(f["vorschlag"], {})
    v.pop("frist", None)
    c.execute("UPDATE freigaben SET stufe='C', vorschlag=? WHERE id=?", (json.dumps(v), fid))
    firma.log(c, wer, "freigabe", f["objekt_typ"], f["objekt_id"], f"Einspruch: {ART_LABEL.get(f['art'], f['art'])} "
              "wartet jetzt auf eine Freigabe statt auf die Frist.", {"freigabe": fid})


def b_anlegen(c, wer, art, objekt_typ, objekt_id, pruefer, vorschlag=None):
    """Aktion auf Stufe B ankündigen: offene Freigabe mit Frist; der Takt führt sie danach aus."""
    v = dict(vorschlag or {}, frist=frist(c))
    cur = c.execute("INSERT INTO freigaben (art, objekt_typ, objekt_id, stufe, angefragt_von, angefragt_am, pruefer, "
                    "vorschlag) VALUES (?,?,?, 'B', ?, ?, ?, ?)", (art, objekt_typ, objekt_id, wer, now(), pruefer,
                                                                   json.dumps(v)))
    c.commit()
    return cur.lastrowid, v["frist"]


def takt(c, jetzt=None):
    """Hintergrund-Arbeiter: Einträge auf Stufe B, deren Frist ohne Einspruch abgelaufen ist, ausführen."""
    jetzt = jetzt or now()
    n = 0
    for f in c.execute("SELECT * FROM freigaben WHERE status='offen' AND stufe='B'").fetchall():
        v = _json(f["vorschlag"], {})
        if not v.get("frist") or v["frist"] > jetzt or f["art"] not in ARTEN:
            continue
        try:
            ARTEN[f["art"]]["entscheiden"](c, "System", f, True, "")
            n += 1
        except Exception as exc:  # nicht ausführbar (z. B. Termin inzwischen abgesagt): als C stehen lassen
            from . import firma
            v.pop("frist", None)
            c.execute("UPDATE freigaben SET stufe='C', vorschlag=? WHERE id=?", (json.dumps(v), f["id"]))
            firma.log(c, "System", "freigabe", f["objekt_typ"], f["objekt_id"],
                      f"Frist abgelaufen, aber nicht ausführbar ({exc}). Wartet jetzt auf eine Freigabe.")
    return n
