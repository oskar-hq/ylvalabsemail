"""Seiten „Kunden“ (Projekte entlang der Customer Journey), ein Projekt (nächster Schritt, Anforderung, Abnahme,
Änderungswünsche, Zeiten, Kosten), Zeiterfassung und Einstellungen → Projekte."""
from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from . import firma, projekte
from .leads_views import db, me
from .main import SETTINGS, darf, login_required, safe_next

bp = Blueprint("kunden", __name__)


def _ajax():
    return request.headers.get("X-Requested-With") == "fetch" or request.accept_mimetypes.best == "application/json"


def _fertig(message, url, ok=True):
    if _ajax():
        return (jsonify(ok=True, message=message), 200) if ok else (jsonify(error=message), 400)
    flash(message if ok else f"Nicht gespeichert: {message}")
    return redirect(url)


def _projekt_or_404(pid):
    p = projekte.get(db(), pid)
    if not p:
        abort(404)
    return p


@bp.app_context_processor
def _globals():
    return {"zeit_laeuft": lambda: projekte.laufend(db(), g.account.get("team"))
            if g.get("account") and g.account.get("team") else None}

# ---------------------------------------------------------------- Übersicht


@bp.get("/kunden")
@login_required
def uebersicht():
    c = db()
    alle = projekte.liste(c)
    beendet = request.args.get("beendet") == "1"
    if beendet:
        alle = projekte.liste(c, "beendet")
    gruppen = [(k, label, hint, [p for p in alle if p["status"] == k]) for k, label, hint in projekte.STATUS]
    ohne = [dict(r) for r in c.execute("SELECT id, name FROM leads WHERE stage='kunde' AND id NOT IN "
                                       "(SELECT lead_id FROM projekte) ORDER BY name")]
    return render_template("kunden.html", account=g.account, gruppen=[gr for gr in gruppen if gr[3]], ohne=ohne,
                           beendet=beendet, personen=firma.aktive_personen(c), demo=SETTINGS["DEMO_MODE"])


@bp.post("/kunden/neu")
@login_required
def neu():
    f = request.form
    try:
        pid = projekte.anlegen(db(), me(), f.get("lead_id", type=int), f.get("titel", ""), f.get("preis_einmal"),
                               f.get("pauschale_monat"))
    except projekte.ProjektError as exc:
        return _fertig(str(exc), url_for("kunden.uebersicht"), ok=False)
    return _fertig("Projekt angelegt. Die ersten Aufgaben stehen bei der verantwortlichen Person.",
                   url_for("kunden.detail", pid=pid))

# ---------------------------------------------------------------- Projekt


@bp.get("/kunden/<int:pid>")
@login_required
def detail(pid):
    c = db()
    p = _projekt_or_404(pid)
    from . import aufgaben
    return render_template("projekt.html", account=g.account, p=p, status=projekte.STATUS,
                           aenderungen=projekte.aenderungen(c, pid), zeiten=projekte.zeiten(c, pid, 20),
                           kosten=projekte.kosten(c, pid), regeln=projekte.regeln(c),
                           projekt_aufgaben=aufgaben.zum_objekt(c, "projekt", pid),
                           laeuft=projekte.laufend(c, me()), abrechnung=projekte.ABRECHNUNG,
                           komm=aufgaben.kommentare(c, "lead", p["lead_id"]), os_personen=firma.aktive_personen(c),
                           verlauf=firma.protokoll(c, objekt=("projekt", pid), limit=25),
                           namen={x["key"]: x["name"] for x in firma.personen(c, mit_archiv=True)},
                           verwalten=darf("kunden", firma.VERWALTEN), personen=firma.aktive_personen(c),
                           demo=SETTINGS["DEMO_MODE"])


@bp.get("/kunden/aenderung/<int:aid>")
@login_required
def aenderung(aid):
    a = projekte.aenderung_get(db(), aid)
    if not a:
        abort(404)
    return redirect(url_for("kunden.detail", pid=a["projekt_id"]) + f"#aenderung-{aid}")


@bp.post("/kunden/<int:pid>/aktion")
@login_required
def aktion(pid):
    """Alle Schritte am Projekt über eine Adresse: was genau, steht in „was“."""
    c = db()
    _projekt_or_404(pid)
    f = request.form
    was = f.get("was", "")
    ziel = url_for("kunden.detail", pid=pid)
    try:
        if was == "weiter":
            vorher = projekte.get(c, pid)["status"]
            p = projekte.weiter(c, me(), pid, f.get("notiz", ""))
            if vorher == "abnahme" and p["status"] == "bau":
                return _fertig("Nicht alles abgehakt: zurück in den Bau. Die Technik hat eine Aufgabe.", ziel)
            text = f"Weiter: {p['status_label']}."
        elif was == "aendern":
            projekte.aendern(c, me(), pid, **{k: f[k] for k in ("titel", "preis_einmal", "pauschale_monat",
                                                                "verantwortlich", "technik", "anforderung") if k in f})
            text = "Gespeichert."
        elif was == "schaetzen":
            projekte.schaetzen(c, me(), pid, f.get("stunden"))
            text = "Schätzung gespeichert."
        elif was == "bestaetigt":
            projekte.kunde_bestaetigt(c, me(), pid, f.get("ja") == "1")
            text = "Gespeichert."
        elif was == "freigeben":
            projekte.anforderung_freigeben(c, me(), pid)
            text = "Freigegeben."
        elif was == "punkt":
            projekte.punkt(c, me(), pid, f.get("punkt", type=int), f.get("erledigt") == "1")
            text = "Gespeichert."
        elif was == "beenden":
            projekte.beenden(c, me(), pid, f.get("grund", ""))
            text = "Projekt beendet."
        elif was == "aenderung":
            projekte.aenderung_anlegen(c, me(), pid, f.get("text", ""))
            text = "Änderungswunsch angelegt. Die verantwortliche Person ordnet ihn ein."
        elif was == "checkin":
            from . import kalender
            p = projekte.get(c, pid)
            kalender.serie_anlegen(c, me(), f"Check-in {p['kunde']}", f.get("wochentag", "2"), f.get("uhrzeit", "10:00"),
                                   f.get("dauer", "30"), p["verantwortlich"], [p["verantwortlich"]])
            text = "Wöchentlicher Check-in steht im Kalender."
        else:
            abort(400)
    except (projekte.ProjektError, ValueError) as exc:
        return _fertig(str(exc), ziel, ok=False)
    return _fertig(text, ziel)


@bp.post("/kunden/aenderung/<int:aid>")
@login_required
def aenderung_aktion(aid):
    """Aktion an einem Änderungswunsch. Aus den Sheets kommt die ID im Formular (Adresse mit 0)."""
    c = db()
    aid = aid or request.form.get("aid", type=int) or 0
    a = projekte.aenderung_get(c, aid)
    if not a:
        abort(404)
    f = request.form
    ziel = url_for("kunden.detail", pid=a["projekt_id"]) + f"#aenderung-{aid}"
    try:
        was = f.get("was", "")
        if was == "einordnen":
            projekte.einordnen(c, me(), aid, f.get("groesse", ""), f.get("vorrang", 2))
        elif was == "schaetzen":
            projekte.aenderung_schaetzen(c, me(), aid, f.get("minuten"))
        elif was == "erledigt":
            projekte.aenderung_abschliessen(c, me(), aid, True)
        elif was == "ablehnen":
            projekte.aenderung_abschliessen(c, me(), aid, False, f.get("grund", ""))
        else:
            abort(400)
    except projekte.ProjektError as exc:
        return _fertig(str(exc), ziel, ok=False)
    return _fertig("Gespeichert.", ziel)

# ---------------------------------------------------------------- Zeiterfassung


@bp.post("/zeit/start")
@login_required
def zeit_start():
    f = request.form
    nxt = safe_next(f.get("next", "")) or url_for("aufgaben.mein_tag")
    if f.get("typ") in ("projekt", "aenderung") and not darf("kunden"):
        abort(403, "Zeit an Projekten erfasst, wer Kunden und Projekte sehen darf.")
    try:
        projekte.start(db(), me(), f.get("typ", ""), f.get("id", type=int), f.get("notiz", ""))
    except projekte.ProjektError as exc:
        return _fertig(str(exc), nxt, ok=False)
    return _fertig("Zeit läuft.", nxt)


@bp.post("/zeit/stopp")
@login_required
def zeit_stopp():
    nxt = safe_next(request.form.get("next", "")) or url_for("aufgaben.mein_tag")
    minuten = projekte.stopp(db(), me())
    return _fertig(f"{minuten} Minuten erfasst." if minuten else "Es lief keine Zeit.", nxt)


@bp.post("/zeit/nachtragen")
@login_required
def zeit_nachtragen():
    f = request.form
    nxt = safe_next(f.get("next", "")) or url_for("aufgaben.mein_tag")
    if f.get("typ") in ("projekt", "aenderung") and not darf("kunden"):
        abort(403, "Zeit an Projekten erfasst, wer Kunden und Projekte sehen darf.")
    try:
        stunden = (f.get("stunden") or "0").replace(",", ".")
        minuten = int(round(float(stunden) * 60)) + int(f.get("minuten") or 0)
        projekte.nachtragen(db(), me(), f.get("typ", ""), f.get("id", type=int), minuten, f.get("tag") or None,
                            f.get("notiz", ""))
    except (projekte.ProjektError, ValueError) as exc:
        return _fertig(str(exc) if isinstance(exc, projekte.ProjektError) else "Zeit bitte als Zahl.", nxt, ok=False)
    return _fertig("Zeit nachgetragen.", nxt)

# ---------------------------------------------------------------- Einstellungen → Projekte


@bp.get("/einstellungen/projekte")
@login_required
def einstellungen():
    c = db()
    return render_template("projekte_einstellungen.html", account=g.account, regeln=projekte.regeln(c),
                           regel_labels=projekte.REGELN, wochentage=projekte.WOCHENTAGE, bereiche=firma.bereiche(c),
                           verwalten=darf("kunden", firma.VERWALTEN), demo=SETTINGS["DEMO_MODE"])


@bp.post("/einstellungen/projekte")
@login_required
def einstellungen_speichern():
    if g.account.get("demo") or not darf("kunden", firma.VERWALTEN):
        abort(403, "Dafür brauchst du „Kunden, Projekte und Änderungen: Verwalten“.")
    try:
        projekte.regeln_speichern(db(), me(), request.form)
    except projekte.ProjektError as exc:
        return _fertig(str(exc), url_for("kunden.einstellungen"), ok=False)
    return _fertig("Regeln gespeichert.", url_for("kunden.einstellungen"))
