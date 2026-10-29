"""Betriebe nicht nur auf der Karte suchen: Websuche (KI) auf Webseiten, Instagram und LinkedIn.

Die Karte (OpenStreetMap) kennt vor allem Handwerk, Pflege und Läden. Agenturen und Büros stehen dort oft nicht,
sind aber auf Instagram, LinkedIn oder mit eigener Webseite gut zu finden. Die Websuche sucht in den gewählten
Nischen, meldet die Betriebe mit Links und legt sie wie die Claude-Recherche als Kandidaten an. Bewertet werden
sie danach wie alle anderen, und auch sie warten unter „Zu prüfen“. Kostet KI-Budget (Suchanfragen und Tokens).
"""
from . import ai, claude_research, known, niches

CHANNELS = {"webseiten": "Webseiten", "instagram": "Instagram", "linkedin": "LinkedIn"}
MAX_FIRMS = 30


def parse_channels(values):
    return [k for k in CHANNELS if k in set(values or [])]


def build_prompt(settings, weights, channels, count, known_hosts=()):
    plan = claude_research.niche_plan(weights, count)
    lines = "\n".join(f"- {k}: {niches.NICHES[k]['label']} (etwa {n})" for k, n in plan.items() if n)
    where = []
    if "webseiten" in channels:
        where.append("auf ihren eigenen Webseiten und in Branchenverzeichnissen")
    if "instagram" in channels:
        where.append("auf Instagram (Profile von Betrieben, keine einzelnen Beiträge)")
    if "linkedin" in channels:
        where.append("auf LinkedIn (Unternehmensseiten oder Profile der Inhaber)")
    skip = ", ".join(sorted(known_hosts)[:300])
    return f"""Finde bis zu {count} Betriebe {claude_research._region_text(settings)}, die zu Ylva Labs passen.
Ylva Labs ist ein kleines Team aus Kappeln, das Betrieben einfache digitale Werkzeuge baut, die Büroarbeit sparen.

Suche {", ".join(where)}.

Nischen (Schlüssel: Name, ungefähre Anzahl):
{lines}

Wer passt:
- Sitz in der Region, eigenständig (keine Kette, kein Franchise, keine Filiale eines Konzerns), Entscheidung vor Ort.
- Etwa 3 bis 100 Leute, also groß genug für wiederkehrende Abläufe im Büro.
- Noch aktiv (Profil oder Webseite wird gepflegt).

Für jeden Betrieb: Name, Nische (genau einer der Schlüssel oben), Ort, Links zu Webseite, Instagram und LinkedIn,
soweit vorhanden, und eine öffentliche E-Mail, wenn es eine gibt. Lass Felder leer, die du nicht belegen kannst.
Erfinde keine Betriebe, Links oder Adressen.

Diese Webseiten kennen wir schon, sie bitte nicht melden: {skip or "(noch keine)"}

Melde die Betriebe am Ende mit dem Werkzeug betriebe_melden."""


def find(c, settings, weights, channels, count, author, center):
    """Websuche ausführen und die Betriebe anlegen. Gibt (neu, gemeldet, übersprungen) zurück."""
    count = max(1, min(MAX_FIRMS, int(count)))
    prompt = build_prompt(settings, weights, channels, count, known.hosts(c, limit=1000))
    firms = ai.search_firms(prompt, channels, max_searches=min(15, 4 + count // 3))
    rows = []
    for i, f in enumerate(firms[:MAX_FIRMS], start=1):
        key = f.get("nische") if f.get("nische") in weights else niches.by_label(f.get("nische", ""))
        rows.append((i, {
            "name": (f.get("name") or "").strip(), "website": f.get("webseite", ""), "email": f.get("email", ""),
            "phone": f.get("telefon", ""), "contact_name": f.get("ansprechperson", ""),
            "street": f.get("strasse", ""), "postcode": f.get("plz", ""), "city": f.get("ort", ""),
            "niche": niches.NICHES[key]["label"] if key else "", "category_label": f.get("kategorie", ""),
            "taetigkeit": f.get("taetigkeit", ""), "groesse": f.get("mitarbeitende", ""),
            "begruendung": f.get("begruendung", ""), "quelle": f.get("quelle", ""),
            "instagram": f.get("instagram", ""), "linkedin": f.get("linkedin", ""),
        }))
    result = claude_research.import_rows(c, rows, author, center, source="web")
    return result["neu"], len(firms), len(result["uebersprungen"])
