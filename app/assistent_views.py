"""Seiten des Assistenten: Fragen an die Firmendaten (mit Verlauf und Quellen), „Was zuerst?“, Entwürfe für
Antwort, Briefing und Case Study, Einstellungen → Assistent. Dazu Angebote (Entwurf, PDF, Freigabe, Ergebnis);
die zählen zum Rechte-Modul „Kunden“."""
import re

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, send_file, url_for
from markupsafe import Markup, escape

from . import assistent, firma
from .leads_views import db, me
from .main import darf, login_required, safe_next

bp = Blueprint("assistent", __name__)
bp_angebote = Blueprint("angebote", __name__)


def _ajax():
    return request.headers.get("X-Requested-With") == "fetch" or request.accept_mimetypes.best == "application/json"


def _fertig(message, ziel, ok=True, **extra):
    if _ajax():
        return (jsonify(ok=True, message=message, **extra), 200) if ok else (jsonify(error=message), 400)
    flash(message if ok else f"Nicht gespeichert: {message}")
    return redirect(ziel)


@bp.app_template_filter("assistent_text")
def _text(text):
    """Antwort des Assistenten sicher als HTML: Absätze, Listen, **fett**, Links nur auf Seiten der App."""
    zeilen, html, liste = (text or "").split("\n"), [], False
    for z in zeilen:
        roh = str(escape(z.strip()))
        roh = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", roh)
        roh = re.sub(r"\[([^\]]+)\]\((/[A-Za-z0-9_/?=&;.#%-]*)\)", r'<a href="\2">\1</a>', roh)
        if roh.startswith(("- ", "* ")):
            if not liste:
                html.append("<ul>")
                liste = True
            html.append(f"<li>{roh[2:]}</li>")
            continue
        if liste:
            html.append("</ul>")
            liste = False
        if roh:
            html.append(f"<p>{roh}</p>")
    if liste:
        html.append("</ul>")
    return Markup("".join(html))


@bp.app_context_processor
def _globals():
    return {"assistent_an": lambda: bool(g.get("account")) and assistent.regeln(db())["an"]}

# ---------------------------------------------------------------- Fragen


BEISPIELE = ["Was ist bei meinen Kunden gerade offen?", "Welche Leads warten auf eine Antwort?",
             "Wie war diese Woche im Vergleich zur letzten?", "Welche Rechnungen sind überfällig?",
             "Was steht morgen an und was muss ich vorbereiten?"]


@bp.get("/assistent")
@login_required
def seite():
    c = db()
    ok, warum = assistent.bereit(c, me())
    alle = request.args.get("alle") == "1" and darf("assistent", firma.VERWALTEN)
    return render_template("assistent.html", account=g.account, bereit=ok, warum=warum,
                           verlauf=assistent.verlauf(c, me(), limit=30, alle=alle), alle=alle, beispiele=BEISPIELE,
                           monat=assistent.monatskosten(c, me()), regeln=assistent.regeln(c), eur=_eur)


def _eur(usd):
    from . import ai
    return ai.eur(usd or 0)


@bp.post("/assistent/frage")
@login_required
def frage():
    try:
        fid = assistent.fragen(db(), me(), request.form.get("frage", ""))
    except assistent.AssistentError as exc:
        return _fertig(str(exc), url_for("assistent.seite"), ok=False)
    return _fertig("Antwort ist da.", url_for("assistent.seite") + f"#frage-{fid}", url=url_for("assistent.seite"))


@bp.post("/assistent/was-zuerst")
@login_required
def was_zuerst():
    nxt = safe_next(request.form.get("next", "")) or url_for("aufgaben.mein_tag")
    try:
        assistent.prioritaeten_ki(db(), me())
    except assistent.AssistentError as exc:
        return _fertig(str(exc), nxt, ok=False)
    return _fertig("Reihenfolge ist da.", nxt)


@bp.post("/assistent/entwurf/antwort/<int:lead_id>")
@login_required
def entwurf_antwort(lead_id):
    try:
        e = assistent.entwurf_antwort(db(), me(), lead_id)
    except assistent.AssistentError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(ok=True, betreff=e["betreff"], text=e["text"],
                   message="Entwurf eingesetzt. Bitte lesen und anpassen, dann senden.")


@bp.post("/assistent/entwurf/briefing/<int:tid>")
@login_required
def entwurf_briefing(tid):
    try:
        assistent.entwurf_briefing(db(), me(), tid)
    except assistent.AssistentError as exc:
        return _fertig(str(exc), url_for("kalender.detail", tid=tid) + "#briefing", ok=False)
    return _fertig("Briefing-Entwurf steht in den Notizen. Bitte ergänzen und speichern.",
                   url_for("kalender.detail", tid=tid) + "#briefing")


@bp.post("/assistent/entwurf/case-study/<int:pid>")
@login_required
def entwurf_case_study(pid):
    try:
        assistent.entwurf_case_study(db(), me(), pid)
    except assistent.AssistentError as exc:
        return _fertig(str(exc), url_for("kunden.detail", pid=pid) + "#case-study", ok=False)
    return _fertig("Case-Study-Entwurf steht am Projekt.", url_for("kunden.detail", pid=pid) + "#case-study")


@bp.post("/assistent/case-study/<int:pid>")
@login_required
def case_study_speichern(pid):
    if not darf("kunden", firma.BEARBEITEN):
        abort(403)
    text = request.form.get("case_study", "").strip()[:20000]
    db().execute("UPDATE projekte SET case_study=? WHERE id=?", (text or None, pid))
    firma.log(db(), me(), "projekt", "projekt", pid, "Case Study bearbeitet.")
    return _fertig("Gespeichert.", url_for("kunden.detail", pid=pid) + "#case-study")

# ---------------------------------------------------------------- Einstellungen → Assistent


@bp.get("/einstellungen/assistent")
@login_required
def einstellungen():
    c = db()
    from . import ai
    return render_template("assistent_einstellungen.html", account=g.account, regeln=assistent.regeln(c),
                           labels=assistent.REGELN, modelle=assistent.MODELLE, schluessel=ai.available(),
                           monat=assistent.monatskosten(c), verwalten=darf("assistent", firma.VERWALTEN),
                           eur=ai.eur)


@bp.post("/einstellungen/assistent")
@login_required
def einstellungen_speichern():
    if not darf("assistent", firma.VERWALTEN):
        abort(403, "Dafür brauchst du „Assistent: Verwalten“.")
    try:
        assistent.regeln_speichern(db(), me(), request.form)
    except assistent.AssistentError as exc:
        return _fertig(str(exc), url_for("assistent.einstellungen"), ok=False)
    return _fertig("Gespeichert.", url_for("assistent.einstellungen"))

# ---------------------------------------------------------------- Angebote (Rechte-Modul „Kunden“)


def _angebot_or_404(aid):
    a = assistent.angebot_get(db(), aid)
    if not a:
        abort(404)
    return a


@bp_angebote.get("/angebote")
@login_required
def liste():
    status = request.args.get("status") or None
    return render_template("angebote.html", account=g.account, angebote=assistent.angebote(db(), status=status),
                           status=status, labels=assistent.STATUS)


@bp_angebote.post("/angebote/neu")
@login_required
def neu():
    f = request.form
    lead_id = f.get("lead_id", type=int)
    ziel = safe_next(f.get("next", "")) or url_for("angebote.liste")
    try:
        aid = assistent.angebot_anlegen(db(), me(), lead_id, f.get("projekt_id", type=int) or None,
                                        f.get("aenderung_id", type=int) or None, mit_ki=f.get("ki") == "1")
    except assistent.AssistentError as exc:
        return _fertig(str(exc), ziel, ok=False)
    return _fertig("Angebot angelegt. Bitte lesen, Preise prüfen und zur Freigabe geben.",
                   url_for("angebote.detail", aid=aid), url=url_for("angebote.detail", aid=aid))


@bp_angebote.get("/angebote/<int:aid>")
@login_required
def detail(aid):
    from . import assistent as a_
    a = _angebot_or_404(aid)
    return render_template("angebot.html", account=g.account, a=a, labels=assistent.STATUS,
                           technik=a_._technik(db()) == me(), verantwortlich=a_._verantwortlich(db(), a),
                           verlauf=firma.protokoll(db(), objekt=("angebot", aid), limit=20),
                           namen={p["key"]: p["name"] for p in firma.personen(db(), mit_archiv=True)})


@bp_angebote.get("/angebote/<int:aid>/pdf")
@login_required
def pdf(aid):
    _angebot_or_404(aid)
    from io import BytesIO
    daten = assistent.pdf_bytes(db(), aid)
    a = assistent.angebot_get(db(), aid)
    return send_file(BytesIO(daten), mimetype="application/pdf", download_name=f"{a['nummer'] or 'Angebot-Entwurf'}.pdf",
                     as_attachment=request.args.get("laden") == "1")


@bp_angebote.post("/angebote/<int:aid>")
@login_required
def aktion(aid):
    c = db()
    _angebot_or_404(aid)
    f = request.form
    ziel = url_for("angebote.detail", aid=aid)
    was = f.get("was", "")
    try:
        if was == "speichern":
            texte, betraege = f.getlist("pos_text"), f.getlist("pos_betrag")
            monatlich = set(f.getlist("pos_monatlich"))
            pos = [{"text": t, "betrag": b, "monatlich": str(i) in monatlich}
                   for i, (t, b) in enumerate(zip(texte, betraege))]
            assistent.angebot_bearbeiten(c, me(), aid, f.get("titel", ""), f.get("text", ""), pos, f.get("email", ""),
                                         f.get("gueltig_bis"))
            text = "Gespeichert."
        elif was == "schaetzung":
            assistent.schaetzung_setzen(c, me(), aid, f.get("stunden"))
            text = "Schätzung gespeichert."
        elif was == "freigabe":
            assistent.zur_freigabe(c, me(), aid)
            text = "Zur Freigabe gegeben."
        elif was == "angenommen":
            assistent.ergebnis(c, me(), aid, True)
            text = "Angenommen. Der Betrieb ist jetzt Kunde, das Projekt steht unter Kunden."
        elif was == "abgelehnt":
            assistent.ergebnis(c, me(), aid, False, f.get("grund", ""))
            text = "Als abgelehnt vermerkt."
        elif was == "verwerfen":
            assistent.verwerfen(c, me(), aid)
            text = "Verworfen."
        else:
            abort(400)
    except assistent.AssistentError as exc:
        return _fertig(str(exc), ziel, ok=False)
    return _fertig(text, ziel)

