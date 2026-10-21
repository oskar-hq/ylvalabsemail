"""Nischen für die Suche: welche Betriebe die Kartensuche holt und wie viel Anteil jede Nische an einem Lauf bekommt.

Jede Nische hat OpenStreetMap-Merkmale (z. B. craft=carpenter) und, wo die Karte kaum eigene Merkmale kennt, ein
Namensmuster („Werbeagentur“, „Design“ …). Ihr wählt beliebig viele Nischen aus und verteilt 100 % auf sie;
ohne eigene Verteilung bekommt jede gleich viel. Ein Lauf bewertet die Betriebe dann in genau diesem Verhältnis.
"""
import re

from . import leaddb

GROUPS = [
    ("handwerk", "Handwerk"), ("pflege", "Pflege"), ("gesundheit", "Gesundheit und Praxen"),
    ("landwirtschaft", "Landwirtschaft"), ("dienstleistung", "Dienstleistung und Büros"),
    ("kreativ", "Design, Werbung und Medien"), ("it", "IT und Digitales"), ("industrie", "Industrie und Werften"),
    ("handel", "Handel"), ("gastgewerbe", "Gastgewerbe und Tourismus"), ("bildung", "Bildung und Soziales"),
]

# (Schlüssel, Name, Gruppe = Branche, OSM-Merkmale, Namensmuster oder None). Namensmuster gehen auch an die
# Kartensuche (Overpass), deshalb nur einfache Muster: kein (?:…), kein \b.
_N = [
    ("sanitaer", "Sanitär und Heizung", "handwerk", ["craft=hvac", "craft=plumber", "craft=heating_engineer"], None),
    ("elektro", "Elektro", "handwerk", ["craft=electrician"], None),
    ("tischlerei", "Tischlerei und Schreinerei", "handwerk", ["craft=carpenter", "craft=joiner", "craft=cabinet_maker"], None),
    ("metallbau", "Metallbau und Schlosserei", "handwerk", ["craft=metal_construction", "craft=blacksmith", "craft=welder"], None),
    ("dach", "Dachdeckerei", "handwerk", ["craft=roofer"], None),
    ("bau", "Bauunternehmen", "handwerk", ["craft=builder"], None),
    ("fenster", "Fensterbau und Glaserei", "handwerk", ["craft=window_construction", "craft=glaziery"], None),
    ("galabau", "Garten- und Landschaftsbau", "handwerk", ["craft=gardener", "craft=landscaper"], None),
    ("maler", "Maler und Lackierer", "handwerk", ["craft=painter"], None),
    ("fliesen", "Fliesen und Böden", "handwerk", ["craft=tiler", "craft=floorer"], None),
    ("stuck", "Stuck, Putz und Trockenbau", "handwerk", ["craft=plasterer", "craft=drywall"], None),
    ("daemmung", "Dämmung und Isolierung", "handwerk", ["craft=insulation"], None),
    ("geruest", "Gerüstbau", "handwerk", ["craft=scaffolder"], None),
    ("steinmetz", "Steinmetz", "handwerk", ["craft=stonemason"], None),
    ("schornstein", "Schornsteinfeger", "handwerk", ["craft=chimney_sweeper"], None),
    ("kfz", "Kfz-Werkstatt", "handwerk", ["shop=car_repair", "craft=car_repair"], None),
    ("polster", "Polsterei und Raumausstattung", "handwerk", ["craft=upholsterer"], None),
    ("baecker", "Bäckerei und Konditorei", "handwerk", ["craft=baker", "craft=confectionery"], None),
    ("fleischer", "Fleischerei", "handwerk", ["craft=butcher"], None),
    ("brauerei", "Brauerei und Brennerei", "handwerk", ["craft=brewery", "craft=distillery"], None),
    ("saegewerk", "Sägewerk und Holz", "handwerk", ["craft=sawmill"], None),
    ("goldschmied", "Goldschmiede", "handwerk", ["craft=jeweller", "craft=goldsmith"], None),
    ("schneiderei", "Schneiderei", "handwerk", ["craft=tailor", "craft=dressmaker"], None),
    ("friseur", "Friseur und Kosmetik", "handwerk", ["craft=hairdresser", "shop=hairdresser", "shop=beauty"], None),
    ("optiker", "Optik und Hörakustik", "handwerk", ["shop=optician", "craft=optician", "shop=hearing_aids"], None),

    ("ambulant", "Ambulante Pflege", "pflege", ["healthcare=home_care"], None),
    ("pflegeheim", "Pflegeheim", "pflege", ["healthcare=nursing_home", "amenity=nursing_home",
                                           "social_facility=nursing_home"], None),
    ("betreut", "Betreutes Wohnen", "pflege", ["social_facility=assisted_living"], None),
    ("tagespflege", "Tagespflege", "pflege", ["social_facility=day_care"], None),

    ("arzt", "Arztpraxis", "gesundheit", ["amenity=doctors"], None),
    ("zahnarzt", "Zahnarztpraxis", "gesundheit", ["amenity=dentist"], None),
    ("physio", "Physiotherapie", "gesundheit", ["healthcare=physiotherapist"], None),
    ("ergo", "Ergotherapie und Logopädie", "gesundheit", ["healthcare=occupational_therapist",
                                                          "healthcare=speech_therapist"], None),
    ("tierarzt", "Tierarztpraxis", "gesundheit", ["amenity=veterinary"], None),
    ("apotheke", "Apotheke", "gesundheit", ["amenity=pharmacy"], None),
    ("sanitaetshaus", "Sanitätshaus und Orthopädie", "gesundheit", ["shop=medical_supply", "healthcare=orthopaedics"], None),

    ("hof", "Höfe und Betriebe", "landwirtschaft", ["place=farm"], None),
    ("landmaschinen", "Landmaschinen", "landwirtschaft", ["craft=agricultural_engines"], None),
    ("agrarhandel", "Agrarhandel", "landwirtschaft", ["shop=agrarian"], None),
    ("gaertnerei", "Gärtnerei und Baumschule", "landwirtschaft", ["shop=garden_centre", "landuse=plant_nursery"], None),
    ("reiterhof", "Reiterhof und Pferdepension", "landwirtschaft", ["leisure=horse_riding"], None),

    ("hausverwaltung", "Hausverwaltung", "dienstleistung", ["office=property_management"], None),
    ("ingenieur", "Ingenieurbüro", "dienstleistung", ["office=engineer"], None),
    ("vermessung", "Vermessung", "dienstleistung", ["office=surveyor"], None),
    ("logistik", "Logistik, Spedition und Umzüge", "dienstleistung", ["office=logistics", "office=moving_company"], None),
    ("steuer", "Steuerberatung und Buchhaltung", "dienstleistung", ["office=tax_advisor", "office=accountant"], None),
    ("kanzlei", "Kanzlei und Notariat", "dienstleistung", ["office=lawyer", "office=notary"], None),
    ("immobilien", "Immobilien", "dienstleistung", ["office=estate_agent"], None),
    ("versicherung", "Versicherung und Finanzen", "dienstleistung", ["office=insurance", "office=financial_advisor"], None),
    ("architekt", "Architekturbüro", "dienstleistung", ["office=architect"], None),
    ("personal", "Personalvermittlung", "dienstleistung", ["office=employment_agency"], None),
    ("beratung", "Unternehmensberatung", "dienstleistung", ["office=consulting"], None),
    ("reinigung", "Gebäudereinigung", "dienstleistung", ["craft=cleaning", "office=cleaning"], r"gebäudereinigung|reinigungsservice"),
    ("sicherheit", "Sicherheitsdienst", "dienstleistung", ["office=security"], r"sicherheitsdienst|wachdienst"),
    ("bestattung", "Bestattung", "dienstleistung", ["shop=funeral_directors"], None),
    ("fahrschule", "Fahrschule", "dienstleistung", ["amenity=driving_school"], None),
    ("reisebuero", "Reisebüro", "dienstleistung", ["shop=travel_agency"], None),
    ("event", "Eventagentur und Veranstaltungstechnik", "dienstleistung", ["office=event_management"],
     r"eventagentur|veranstaltungstechnik|eventservice|event-service"),

    ("werbeagentur", "Werbeagentur", "kreativ", ["office=advertising_agency"], r"werbeagentur|kommunikationsagentur"),
    ("marketing", "Marketingagentur", "kreativ", [], r"marketing"),
    ("grafik", "Grafikdesign", "kreativ", ["office=graphic_design", "craft=graphic_design"], r"grafik|graphic"),
    ("design", "Designbüro und Kommunikationsdesign", "kreativ", ["office=design"], r"designbüro|designstudio|designer"),
    ("craftdesign", "Craft Design und Kunsthandwerk", "kreativ", ["craft=pottery", "craft=handicraft", "shop=craft"],
     r"kunsthandwerk|manufaktur|töpferei|keramik"),
    ("template", "Template Design und Vorlagen", "kreativ", [], r"template|vorlagen"),
    ("video", "Video und Film", "kreativ", ["office=film_production"], r"video|filmproduktion|filmstudio"),
    ("foto", "Fotografie", "kreativ", ["craft=photographer", "shop=photo"], r"fotograf|photograph|fotostudio"),
    ("druck", "Druckerei und Copyshop", "kreativ", ["craft=printer", "shop=copyshop"], r"druckerei"),
    ("schilder", "Schilder und Beschriftung", "kreativ", ["craft=signmaker"], r"werbetechnik|beschriftung"),
    ("innenarchitektur", "Innenarchitektur", "kreativ", ["office=interior_design"], r"innenarchitekt|interior"),
    ("musik", "Tonstudio und Musik", "kreativ", ["amenity=studio"], r"tonstudio|musikproduktion"),
    ("verlag", "Verlag und Redaktion", "kreativ", ["office=publisher", "office=newspaper"], r"verlag"),

    ("itservice", "IT-Service und EDV", "it", ["office=it"], r"it-service|edv|systemhaus|computer"),
    ("webdesign", "Webdesign und Webagentur", "it", [], r"webdesign|webagentur|internetagentur|digitalagentur"),
    ("software", "Softwareentwicklung", "it", ["office=software"], r"software"),

    ("werft", "Werft", "industrie", ["craft=shipbuilder", "industrial=shipyard"], None),
    ("bootsbau", "Bootsbau", "industrie", ["craft=boatbuilder"], None),
    ("produktion", "Produktion und Fertigung", "industrie", ["man_made=works"], None),
    ("maschinenbau", "Maschinenbau", "industrie", ["industrial=machine_shop"], r"maschinenbau"),
    ("lebensmittel", "Lebensmittelherstellung", "industrie", ["industrial=food_industry"], r"feinkost|räucherei|molkerei"),

    ("autohaus", "Autohaus", "handel", ["shop=car"], None),
    ("moebel", "Möbel und Küchen", "handel", ["shop=furniture", "shop=kitchen"], None),
    ("baustoffe", "Baustoffe und Fachhandel", "handel", ["shop=trade", "shop=building_materials", "shop=hardware"], None),
    ("fahrrad", "Fahrräder und Motorräder", "handel", ["shop=bicycle", "shop=motorcycle"], None),
    ("boote", "Bootshandel", "handel", ["shop=boat"], None),
    ("reifen", "Reifenhandel", "handel", ["shop=tyres"], None),
    ("elektronik", "Elektronik und Haushaltsgeräte", "handel", ["shop=electronics", "shop=appliance"], None),
    ("baeder", "Bäder und Fliesen", "handel", ["shop=bathroom_furnishing", "shop=tiles"], None),

    ("hotel", "Hotel", "gastgewerbe", ["tourism=hotel"], None),
    ("pension", "Pension und Ferienwohnungen", "gastgewerbe", ["tourism=guest_house", "tourism=apartment"], None),
    ("camping", "Camping und Wohnmobilplatz", "gastgewerbe", ["tourism=camp_site", "tourism=caravan_site"], None),
    ("restaurant", "Restaurant", "gastgewerbe", ["amenity=restaurant"], None),
    ("cafe", "Café", "gastgewerbe", ["amenity=cafe"], None),
    ("catering", "Catering", "gastgewerbe", ["craft=caterer"], r"catering"),

    ("kita", "Kita und Kindergarten", "bildung", ["amenity=kindergarten"], None),
    ("musikschule", "Musikschule", "bildung", ["amenity=music_school"], None),
    ("sprachschule", "Sprach- und Nachhilfeschule", "bildung", ["amenity=language_school", "amenity=prep_school"], None),
    ("weiterbildung", "Weiterbildung und Seminare", "bildung", [], r"akademie|seminare|weiterbildung"),
]
NICHES = {k: {"key": k, "label": label, "branche": b, "tags": tags, "name": name} for k, label, b, tags, name in _N}
GROUP_LABELS = dict(GROUPS)
# Was bisher fest gesucht wurde: die Nischen, die zu Ylva Labs passen (Stufe A und B im Gehirn)
DEFAULT_KEYS = ["sanitaer", "elektro", "tischlerei", "metallbau", "dach", "bau", "fenster", "galabau",
                "ambulant", "pflegeheim", "betreut", "tagespflege", "hof", "landmaschinen", "agrarhandel",
                "hausverwaltung", "ingenieur", "vermessung", "logistik", "werft", "bootsbau"]
_TAG_TO_NICHE = {t: k for k, n in NICHES.items() for t in n["tags"]}
KEY_ORDER = ("craft", "healthcare", "office", "social_facility", "amenity", "shop", "industrial", "man_made",
             "tourism", "place", "landuse", "leisure")


class NicheError(ValueError):
    pass


def equal(keys):
    """100 % gleichmäßig verteilen; der Rest der Teilung geht an die ersten."""
    keys = list(keys)
    if not keys:
        return {}
    base, rest = divmod(100, len(keys))
    return {k: base + (1 if i < rest else 0) for i, k in enumerate(keys)}


def load(c):
    """Gewählte Nischen in Katalog-Reihenfolge mit Prozent: {schlüssel: prozent}, Summe 100."""
    stored = leaddb.kv_get(c, "niches") or {}
    weights = {k: int(v) for k, v in (stored.get("weights") or {}).items() if k in NICHES}
    if not weights or sum(weights.values()) != 100:
        return equal(DEFAULT_KEYS if not weights else [k for k in NICHES if k in weights])
    return {k: weights[k] for k in NICHES if k in weights}


def is_custom(c):
    stored = leaddb.kv_get(c, "niches") or {}
    return bool(stored.get("custom"))


def parse(keys, weights=None):
    """Formular → {schlüssel: prozent}. Ohne Gewichte: gleichmäßig. Mit Gewichten: jede mindestens 1 %, zusammen
    genau 100 %, nicht mehr und nicht weniger."""
    keys = [k for k in NICHES if k in set(keys)]
    if not keys:
        raise NicheError("Bitte mindestens eine Nische wählen.")
    if not weights:
        return equal(keys)
    out = {}
    for k in keys:
        try:
            out[k] = int(str(weights.get(k, "")).strip())
        except ValueError as exc:
            raise NicheError(f"{NICHES[k]['label']}: bitte eine ganze Zahl in Prozent.") from exc
        if not 1 <= out[k] <= 100:
            raise NicheError(f"{NICHES[k]['label']}: zwischen 1 und 100 %.")
    total = sum(out.values())
    if total != 100:
        raise NicheError(f"Zusammen sind es {total} %, es müssen genau 100 % sein.")
    return out


def save(c, weights):
    leaddb.kv_set(c, "niches", {"weights": weights, "custom": weights != equal(list(weights))})


def selectors(keys):
    """Overpass-Teile für die gewählten Nischen: je OSM-Schlüssel ein Ausdruck, dazu die Namensmuster."""
    by_key, names = {}, []
    for k in keys:
        n = NICHES[k]
        for tag in n["tags"]:
            key, value = tag.split("=", 1)
            by_key.setdefault(key, [])
            if value not in by_key[key]:
                by_key[key].append(value)
        if n["name"]:
            names.append(n["name"])
    out = []
    for key, values in by_key.items():
        out.append(f'["{key}"="{values[0]}"]' if len(values) == 1 else f'["{key}"~"^({"|".join(values)})$"]')
    if names:  # nur Betriebe (Handwerk, Büro, Laden), deren Name passt
        out.append(f'[~"^(craft|office|shop)$"~"."]["name"~"{"|".join(names)}",i]')
    return out


def match(tags, keys):
    """Zu welcher gewählten Nische gehört ein Karteneintrag? (schlüssel, kategorie) oder (None, kategorie)."""
    chosen = set(keys)
    category = ""
    for key in KEY_ORDER:
        value = tags.get(key)
        if not value:
            continue
        tag = f"{key}={value}"
        category = category or tag
        if _TAG_TO_NICHE.get(tag) in chosen:
            return _TAG_TO_NICHE[tag], tag
    name = (tags.get("name") or "").lower()
    for k in keys:
        pattern = NICHES[k]["name"]
        if pattern and re.search(pattern, name):
            return k, category
    return None, category


def of_category(category):
    return _TAG_TO_NICHE.get(category or "")


def by_label(text):
    """Für den CSV-Import: Nische nach Name oder Schlüssel."""
    t = (text or "").strip().lower()
    for k, n in NICHES.items():
        if t in (k, n["label"].lower()):
            return k
    return None


def backfill(c):
    """Betriebe aus früheren Suchen bekommen ihre Nische nach der Kategorie (einmal je Betrieb, kostenlos)."""
    for k, n in NICHES.items():
        if n["tags"]:
            marks = ",".join("?" * len(n["tags"]))
            c.execute(f"UPDATE leads SET niche=? WHERE niche IS NULL AND category IN ({marks})", (k, *n["tags"]))
    c.commit()


def pick(weights, taken, available):
    """Aus welcher Nische kommt der nächste Betrieb? Die, die am weitesten hinter ihrem Anteil liegt."""
    total = sum(taken.values()) + 1
    best, best_gap = None, None
    for k, w in weights.items():
        if k not in available:
            continue
        gap = w / 100 * total - taken.get(k, 0)
        if best_gap is None or gap > best_gap:
            best, best_gap = k, gap
    return best


def describe(weights, limit=4):
    items = [f"{NICHES[k]['label']} {w} %" for k, w in sorted(weights.items(), key=lambda x: -x[1])]
    more = f" und {len(items) - limit} weitere" if len(items) > limit else ""
    return ", ".join(items[:limit]) + more


def catalog():
    """Für die Auswahlseite: Gruppen mit ihren Nischen."""
    return [(g, label, [n for n in NICHES.values() if n["branche"] == g]) for g, label in GROUPS]
