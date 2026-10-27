"""Team: wer man ist, eigenes Passwort und eigene Einstellungen, eigene Vorlage mit Live-Vorschau, Team-Zahlen."""
import json

from flask import Blueprint, Response, abort, flash, g, jsonify, redirect, render_template, request, url_for

from . import agent, emailbuild, leaddb, pixel, team
from .leads_views import db, me, status_payload, who
from .main import SETTINGS, login_required, safe_next, update_session

bp = Blueprint("team", __name__)


def _guard_demo():
    if g.account.get("demo"):
        abort(403, "Im Demo-Modus lassen sich Konten nicht ändern.")


@bp.route("/profil/wer", methods=["GET", "POST"])
@login_required
def who_am_i():
    """Nach der Anmeldung mit dem Postfach: Wer bist du? Danach gilt alles für diese Person."""
    nxt = safe_next(request.values.get("next", "")) or url_for("index")
    people = team.members(db())
    if request.method == "POST":
        key = request.form.get("member", "")
        member = team.get(db(), key)
        if not member:
            abort(400)
        update_session(team=key, name=member["name"])
        agent.log.info("%s arbeitet als %s", g.account.get("user"), member["name"])
        if not member["pw_hash"] and not g.account.get("demo"):
            flash("Lege jetzt dein Passwort fest. Danach meldest du dich einfach mit deinem Namen an.")
            return redirect(url_for("team.profile"))
        return redirect(nxt)
    return render_template("who.html", account=g.account, people=people, nxt=nxt, st=status_payload(),
                           demo=SETTINGS["DEMO_MODE"])


@bp.get("/profil")
@login_required
def profile():
    from .settings_views import is_admin
    key = me()
    if not key:
        return redirect(url_for("team.who_am_i", next=url_for("team.profile")))
    own = team.prefs(db(), key)
    fields = [{"key": k, "label": label, "value": own.get(k, ""),
               "team": SETTINGS.get(k, None), "step": "1" if typ is int else "0.5", "min": lo, "max": hi}
              for k, label, typ, lo, hi in team.PERSONAL]
    return render_template("profile.html", account=g.account, member=team.get(db(), key), pref_fields=fields,
                           month_eur=agent.person_month_eur(db(), key),
                           settings=team.settings(db(), key, SETTINGS["ORG_NAME"]), people=team.members(db()),
                           admin=is_admin(g.account), min_pw=team.MIN_PASSWORD, st=status_payload(),
                           demo=SETTINGS["DEMO_MODE"])


@bp.post("/profil/passwort")
@login_required
def password():
    """Eigenes Passwort ändern (mit dem bisherigen). Für eine Person ohne Passwort darf jede im Team das erste
    festlegen; ein bestehendes fremdes Passwort neu setzen dürfen nur Admins."""
    from .settings_views import is_admin
    _guard_demo()
    c = db()
    key = request.form.get("member") or me()
    member = team.get(c, key)
    if not member:
        abort(400)
    new, again = request.form.get("new", ""), request.form.get("again", "")
    if key == me() and member["pw_hash"] and not team.check_password(c, key, request.form.get("current", "")):
        flash("Nicht geändert: Das bisherige Passwort stimmt nicht.")
        return redirect(url_for("team.profile"))
    if key != me() and member["pw_hash"] and not is_admin(g.account):
        abort(403, "Ein bestehendes Passwort einer anderen Person ändern nur Admins.")
    if new != again:
        flash("Nicht geändert: Die beiden Passwörter sind nicht gleich.")
        return redirect(url_for("team.profile"))
    try:
        team.set_password(c, key, new)
    except team.TeamError as exc:
        flash(f"Nicht geändert: {exc}")
        return redirect(url_for("team.profile"))
    agent.log.info("Passwort für %s gesetzt von %s", member["name"], who())
    flash(f"Passwort für {member['name']} gespeichert." if key != me() else
          "Passwort gespeichert. Ab jetzt meldest du dich mit deinem Namen und diesem Passwort an.")
    return redirect(url_for("team.profile"))


@bp.post("/profil/einstellungen")
@login_required
def save_profile():
    key = me()
    if not key:
        abort(400)
    team.save_settings(db(), key, gruss=request.form.get("gruss", "").strip()[:80] or "Viele Grüße",
                       signatur=request.form.get("signatur", "").replace("\r\n", "\n").strip()[:600])
    flash("Gruß und Unterschrift gespeichert.")
    return redirect(request.form.get("back") if (request.form.get("back") or "").startswith("/vorlage")
                    else url_for("team.profile"))


@bp.post("/profil/meine-einstellungen")
@login_required
def save_prefs():
    """Eigene Werte für Bewertung, Lauf und KI-Budget. Leer = Team-Standard aus den Einstellungen."""
    key = me()
    if not key:
        abort(400)
    try:
        values = team.parse_prefs(request.form)
    except team.TeamError as exc:
        flash(f"Nicht gespeichert: {exc}")
        return redirect(url_for("team.profile") + "#meine-einstellungen")
    team.save_settings(db(), key, einstellungen=values)
    agent.log.info("Eigene Einstellungen von %s: %s", who(), values)
    flash("Deine Einstellungen sind gespeichert. Sie gelten für deine Läufe und deine KI-Aktionen."
          if values else "Für dich gelten wieder überall die Team-Standards.")
    return redirect(url_for("team.profile") + "#meine-einstellungen")


@bp.get("/team")
@login_required
def overview():
    people, total = team.stats(db())
    return render_template("team.html", account=g.account, people=people, total=total, me=me(),
                           st=status_payload(), demo=SETTINGS["DEMO_MODE"])

# ---------------------------------------------------------------- Eigene Vorlage mit Live-Vorschau


SAMPLE = {"id": 0, "name": "Tischlerei Hansen", "category_label": "Tischlerei", "branche": "handwerk", "city": "Kappeln",
          "contact_name": "Jens Hansen", "email": "jens@beispiel.example", "subject": "", "greeting": "", "body": "",
          "research": {"zeitfresser": ["Angebote, Aufmaß und Nachkalkulation", "Anfragen, die alle übers Telefon laufen"],
                       "import": {"aufhaenger": "Auf Ihrer Webseite habe ich gesehen, dass Sie gerade eine Bürokraft "
                                                "suchen."}},
          "mail_design": None}


def _sample(lead_id):
    """Beispiel-Betrieb für die Vorschau: ein echter aus dem CRM oder der eingebaute."""
    if lead_id and str(lead_id).isdigit():
        lead = leaddb.get_lead(db(), int(lead_id))
        if lead:
            return lead
    return dict(SAMPLE)


def _design(form, fallback):
    d = {**agent.DESIGN_DEFAULT, **(fallback or {})}
    if form.get("template") in emailbuild.TEMPLATES:
        d["template"] = form["template"]
    if form.get("graphic") in agent.GRAPHICS:
        d["graphic"] = form["graphic"]
    if form.get("icon") in pixel.ICONS:
        d["icon"] = form["icon"]
    try:
        d["band_seed"] = max(1, min(50, int(form.get("band_seed") or d["band_seed"])))
    except ValueError:
        pass
    return d


@bp.get("/vorlage")
@login_required
def template_editor():
    key = me()
    if not key:
        return redirect(url_for("team.who_am_i", next=url_for("team.template_editor")))
    c = db()
    s = team.settings(c, key, SETTINGS["ORG_NAME"])
    examples = [dict(r) for r in c.execute(
        "SELECT id, name, category_label FROM leads WHERE stage IS NOT NULL OR status IN ('passt','entwurf') "
        "ORDER BY (owner = ?) DESC, updated_at DESC LIMIT 12", (key,))]
    return render_template("template_editor.html", account=g.account, text=agent.template_text(c, key),
                           own=bool((s["vorlage"] or "").strip()), settings=s,
                           design={**agent.DESIGN_DEFAULT, **(s["design"] or {})},
                           templates={k: v["name"] for k, v in emailbuild.TEMPLATES.items()}, graphics=agent.GRAPHICS,
                           icons={k: v[0] for k, v in pixel.ICONS.items()}, examples=examples,
                           placeholders=PLACEHOLDERS, st=status_payload(), demo=SETTINGS["DEMO_MODE"])


PLACEHOLDERS = [("{firma}", "Firma"), ("{anrede}", "Anrede"), ("{kategorie}", "Branche"), ("{ort}", "Ort"),
                ("{zeitfresser}", "Zeitfresser"), ("{aufhaenger}", "Aufhänger"), ("{verstaendnis}", "Verständnis-Satz"),
                ("{beispiel}", "Beispiel"), ("{absender}", "Dein Name")]


@bp.post("/vorlage/vorschau")
@login_required
def template_preview():
    """Live-Vorschau: Vorlage mit einem Beispiel-Betrieb ausgefüllt, im gewählten Design, mit deiner Unterschrift."""
    key = me()
    c = db()
    s = team.settings(c, key, SETTINGS["ORG_NAME"])
    lead = _sample(request.form.get("beispiel"))
    name = (team.get(c, key) or {}).get("name") or SETTINGS["LEADS_SENDER_NAME"]
    fields = agent.fill_template(lead, request.form.get("text", ""), name)
    lead = {**lead, **fields, "mail_design": json.dumps(_design(request.form, s["design"]))}
    snd = {**team.sender(c, key, SETTINGS["ORG_NAME"]), "gruss": request.form.get("gruss") or s["gruss"]}
    rcpt = {"name": lead.get("contact_name") or "", "first": "", "email": lead.get("email") or ""}
    html, _, _ = emailbuild.render(agent.lead_form(lead, snd), rcpt, SETTINGS)
    resp = Response(html, mimetype="text/html")
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    return resp


@bp.post("/vorlage")
@login_required
def template_save():
    key = me()
    if not key:
        abort(400)
    c = db()
    s = team.settings(c, key, SETTINGS["ORG_NAME"])
    if request.form.get("zuruecksetzen") == "1":
        team.save_settings(c, key, vorlage="")
        flash("Deine Vorlage ist wieder die gemeinsame aus dem Gehirn.")
        return redirect(url_for("team.template_editor"))
    text = request.form.get("text", "").replace("\r\n", "\n").strip()
    if not text:
        return jsonify(error="Die Vorlage ist leer."), 400
    team.save_settings(c, key, vorlage=text[:8000], design=_design(request.form, s["design"]),
                       gruss=request.form.get("gruss", "").strip()[:80] or s["gruss"])
    agent.log.info("Vorlage von %s gespeichert", who())
    return jsonify(ok=True, message="Deine Vorlage ist gespeichert. Neue Mails nutzen sie ab jetzt.")
