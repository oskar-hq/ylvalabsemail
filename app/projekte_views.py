"""Seiten „Kunden“ (Projekte entlang der Customer Journey), ein Projekt (nächster Schritt, Anforderung, Abnahme,
Änderungswünsche, Zeiten, Kosten), Zeiterfassung und Einstellungen → Projekte."""
from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, session, url_for

from . import assistent, firma, projekte
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
    from . import aufgaben, finanzen, portal
    return render_template("projekt.html", account=g.account, p=p, status=projekte.STATUS,
                           aenderungen=projekte.aenderungen(c, pid), zeiten=projekte.zeiten(c, pid, 20),
                           kosten=projekte.kosten(c, pid), regeln=projekte.regeln(c),
                           projekt_aufgaben=aufgaben.zum_objekt(c, "projekt", pid),
                           p_angebote=assistent.angebote(c, projekt_id=pid),
                           laeuft=projekte.laufend(c, me()), abrechnung=projekte.ABRECHNUNG,
                           komm=aufgaben.kommentare(c, "lead", p["lead_id"]), os_personen=firma.aktive_personen(c),
                           verlauf=firma.protokoll(c, objekt=("projekt", pid), limit=25),
                           namen={x["key"]: x["name"] for x in firma.personen(c, mit_archiv=True)},
                           verwalten=darf("kunden", firma.VERWALTEN), personen=firma.aktive_personen(c),
                           demo=SETTINGS["DEMO_MODE"], zugaenge=portal.zugaenge(c, pid),
                           dokumente=portal.dokumente(c, pid), dok_arten=portal.DOK_ARTEN,
                           berichte=portal.berichte(c, pid), portal_darf=portal.darf_zugang(c, me(), p),
                           portal_link=session.pop("portal_link", None),
                           rechnungen=finanzen.liste(c, projekt_id=pid) if darf("finanzen") else [])


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

# ---------------------------------------------------------------- Kunden-Bereich (Stufe 7): Zugang, Dokumente, Berichte


def _portal_fertig(fn, pid, anker, ok_text):
    from . import portal
    ziel = url_for("kunden.detail", pid=pid) + anker
    try:
        text = fn() or ok_text
    except (portal.PortalError, projekte.ProjektError) as exc:
        return _fertig(str(exc), ziel, ok=False)
    return _fertig(text, ziel)


@bp.post("/kunden/<int:pid>/zugang")
@login_required
def zugang(pid):
    from . import portal
    _projekt_or_404(pid)
    senden = request.form.get("senden", "1") == "1" and not g.account.get("demo")

    def tun():
        link = portal.zugang_schicken(db(), me(), pid, request.form.get("email", ""), request.form.get("name", ""),
                                      senden=senden, basis=request.host_url)
        if senden:
            return "Zugang geschickt."
        session["portal_link"] = link  # einmal auf der Projektseite zum Kopieren zeigen
        return "Link erzeugt. Er steht im Abschnitt Kunden-Bereich zum Kopieren."
    return _portal_fertig(tun, pid, "#portal", "")


@bp.post("/kunden/zugang/<int:zid>")
@login_required
def zugang_aendern(zid):
    from . import portal
    row = db().execute("SELECT projekt_id FROM kunden_zugaenge WHERE id=?", (zid,)).fetchone()
    if not row:
        abort(404)
    gesperrt = request.form.get("gesperrt") == "1"
    return _portal_fertig(lambda: portal.zugang_sperren(db(), me(), zid, gesperrt), row["projekt_id"], "#portal",
                          "Zugang gesperrt." if gesperrt else "Zugang wieder frei.")


@bp.post("/kunden/<int:pid>/dokument")
@login_required
def dokument_hochladen(pid):
    from . import portal
    _projekt_or_404(pid)
    f = request.form
    return _portal_fertig(lambda: portal.dokument_speichern(db(), me(), pid, request.files.get("datei"), f.get("titel", ""),
                                                            f.get("art", ""), f.get("sichtbar") == "1",
                                                            SETTINGS["DATA_DIR"]) and None,
                          pid, "#dokumente", "Dokument hochgeladen.")


@bp.post("/kunden/dokument/<int:did>")
@login_required
def dokument_aendern(did):
    from . import portal
    d = portal.dokument(db(), did)
    if not d:
        abort(404)
    loeschen = request.form.get("loeschen") == "1"
    sichtbar = request.form.get("sichtbar") == "1"
    return _portal_fertig(lambda: portal.dokument_aendern(db(), me(), did, sichtbar, loeschen, SETTINGS["DATA_DIR"]),
                          d["projekt_id"], "#dokumente", "Gelöscht." if loeschen else "Gespeichert.")


@bp.get("/kunden/dokument/<int:did>")
@login_required
def dokument_laden(did):
    from flask import send_from_directory
    from . import portal
    d = portal.dokument(db(), did)
    if not d:
        abort(404)
    return send_from_directory(portal.dok_ordner(SETTINGS["DATA_DIR"]), d["datei"], as_attachment=True,
                               download_name=d["dateiname"] or d["datei"])


@bp.post("/kunden/<int:pid>/bericht")
@login_required
def bericht_jetzt(pid):
    from . import kpis, portal
    p = _projekt_or_404(pid)

    def tun():
        if not portal.darf_zugang(db(), me(), p):
            raise portal.PortalError("Das macht die verantwortliche Person (oder wer Kunden verwaltet).")
        if not portal.bericht_anlegen(db(), pid, kpis.woche_von(), me()):
            raise portal.PortalError("Für diese Woche gibt es schon einen Bericht, oder es fehlt eine Mailadresse.")
        return "Bericht vorbereitet. Er steht in der Freigabe-Zentrale."
    return _portal_fertig(tun, pid, "#berichte", "")


@bp.post("/kunden/bericht/<int:bid>")
@login_required
def bericht_bearbeiten(bid):
    from . import portal
    b = portal.bericht_get(db(), bid)
    if not b:
        abort(404)
    return _portal_fertig(lambda: portal.bericht_bearbeiten(db(), me(), bid, request.form.get("betreff", ""),
                                                            request.form.get("text", "")),
                          b["projekt_id"], "#berichte", "Bericht gespeichert.")


@bp.get("/einstellungen/kunden-bereich")
@login_required
def portal_einstellungen():
    from . import freigaben, portal
    c = db()
    return render_template("portal_einstellungen.html", account=g.account, regeln=portal.regeln(c),
                           labels=portal.REGELN, wochentage=portal.WOCHENTAGE, verwalten=darf("kunden", firma.VERWALTEN),
                           stufen=freigaben.stufen(c))


@bp.post("/einstellungen/kunden-bereich")
@login_required
def portal_einstellungen_speichern():
    from . import portal
    if g.account.get("demo") or not darf("kunden", firma.VERWALTEN):
        abort(403, "Dafür brauchst du „Kunden, Projekte und Änderungen: Verwalten“.")
    try:
        geaendert = portal.regeln_speichern(db(), me(), request.form)
    except portal.PortalError as exc:
        return _fertig(str(exc), url_for("kunden.portal_einstellungen"), ok=False)
    return _fertig("Gespeichert." if geaendert else "Nichts geändert.", url_for("kunden.portal_einstellungen"))
