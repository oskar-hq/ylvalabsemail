"""Kreise in Schleswig-Holstein: Grenzen, Statistik (Gewerbeanmeldungen, Baugenehmigungen) und Wachstumssignal.

- Grenzen: einmal über die Overpass-API (OpenStreetMap, admin_level=6) laden, vereinfachen und speichern.
  Damit bekommt jeder Betrieb seinen Kreis (Punkt-in-Polygon), ganz ohne externe Dienste zur Laufzeit.
- Statistik: als CSV-Export aus der Regionaldatenbank Deutschland bzw. vom Statistikamt Nord importieren
  (Datenlizenz Deutschland – Namensnennung 2.0). Je Kreis zählt die letzte Zahl in der Zeile.
- Wachstum: Werte je 1.000 Einwohner, die stärksten Kreise bekommen in der Bewertung einen kleinen Zuschlag.
"""
import json
import re
import urllib.parse
import urllib.request

from . import finder

# Amtlicher Kreisschlüssel → Name, Kreisstadt-Koordinaten (für Kreise ohne geladene Grenzen und als Beschriftung)
KREISE = {
    "01001": ("Flensburg", 54.784, 9.437), "01002": ("Kiel", 54.323, 10.123), "01003": ("Lübeck", 53.866, 10.686),
    "01004": ("Neumünster", 54.073, 9.984), "01051": ("Dithmarschen", 54.196, 9.093),
    "01053": ("Herzogtum Lauenburg", 53.700, 10.757), "01054": ("Nordfriesland", 54.477, 9.052),
    "01055": ("Ostholstein", 54.137, 10.618), "01056": ("Pinneberg", 53.660, 9.796), "01057": ("Plön", 54.162, 10.421),
    "01058": ("Rendsburg-Eckernförde", 54.305, 9.664), "01059": ("Schleswig-Flensburg", 54.521, 9.566),
    "01060": ("Segeberg", 53.936, 10.309), "01061": ("Steinburg", 53.925, 9.516), "01062": ("Stormarn", 53.811, 10.374),
}
# Einwohner, gerundet (ungefähr, Stand um 2023). Nur zum Gewichten „je 1.000 Einwohner“; per CSV überschreibbar.
EINWOHNER_UNGEFAEHR = {
    "01001": 92000, "01002": 248000, "01003": 218000, "01004": 80000, "01051": 134000, "01053": 202000,
    "01054": 167000, "01055": 201000, "01056": 320000, "01057": 130000, "01058": 276000, "01059": 203000,
    "01060": 282000, "01061": 132000, "01062": 248000,
}
KINDS = {"gewerbe": "Gewerbeanmeldungen", "bau": "Baugenehmigungen", "einwohner": "Einwohner"}
TOP = 5  # die stärksten 5 von 15 Kreisen gelten als Wachstumsregion
# Gewerke, die von vielen Baugenehmigungen direkt profitieren
BAU_CATEGORIES = {f"craft={c}" for c in ("builder", "roofer", "carpenter", "joiner", "cabinet_maker", "electrician",
                                          "hvac", "plumber", "heating_engineer", "window_construction",
                                          "metal_construction", "gardener", "landscaper")}

# ---------------------------------------------------------------- Grenzen


def boundary_query(region="DE-SH"):
    return (f'[out:json][timeout:180];area["ISO3166-2"="{region}"]->.land;'
            'rel(area.land)["boundary"="administrative"]["admin_level"="6"];out geom;')


def _stitch(ways):
    """Wege (Listen von Punkten) zu geschlossenen Ringen zusammensetzen."""
    ways = [list(w) for w in ways if len(w) >= 2]
    rings = []
    while ways:
        ring = ways.pop(0)
        changed = True
        while ring[0] != ring[-1] and changed:
            changed = False
            for i, w in enumerate(ways):
                if w[0] == ring[-1]:
                    ring += w[1:]
                elif w[-1] == ring[-1]:
                    ring += w[-2::-1]
                elif w[-1] == ring[0]:
                    ring = w[:-1] + ring
                elif w[0] == ring[0]:
                    ring = w[:0:-1] + ring
                else:
                    continue
                ways.pop(i)
                changed = True
                break
        if len(ring) >= 4:
            rings.append(ring)
    return rings


def _simplify(points, eps):
    """Ramer-Douglas-Peucker: weniger Punkte, gleiche Form (eps in Grad, 0.003 ≈ 300 m)."""
    if len(points) < 3:
        return points
    (y1, x1), (y2, x2) = points[0], points[-1]
    dmax, index = 0.0, 0
    for i in range(1, len(points) - 1):
        y, x = points[i]
        num = abs((y2 - y1) * x - (x2 - x1) * y + x2 * y1 - y2 * x1)
        den = ((y2 - y1) ** 2 + (x2 - x1) ** 2) ** 0.5 or 1e-12
        d = num / den if den > 1e-12 else ((y - y1) ** 2 + (x - x1) ** 2) ** 0.5
        if d > dmax:
            dmax, index = d, i
    if dmax <= eps:
        return [points[0], points[-1]]
    return _simplify(points[:index + 1], eps)[:-1] + _simplify(points[index:], eps)


def parse_boundaries(payload, eps=0.003):
    """Overpass-Antwort (Relationen mit `out geom`) → {Kreisschlüssel: {name, rings: [[[lat, lon], …]]}}."""
    result = {}
    for el in payload.get("elements", []):
        if el.get("type") != "relation":
            continue
        tags = el.get("tags") or {}
        key = (tags.get("de:amtlicher_gemeindeschluessel") or tags.get("de:regionalschluessel") or "")[:5]
        if key not in KREISE:
            continue
        ways = [[(round(p["lat"], 5), round(p["lon"], 5)) for p in m.get("geometry") or []]
                for m in el.get("members", []) if m.get("type") == "way" and m.get("role") in ("outer", "inner", "")]
        rings = [[list(p) for p in _simplify(r, eps)] for r in _stitch(ways)]
        rings = [r for r in rings if len(r) >= 4]
        if rings:
            result[key] = {"name": KREISE[key][0], "rings": rings}
    return result


def load_boundaries(overpass_url, region="DE-SH"):
    data = urllib.parse.urlencode({"data": boundary_query(region)}).encode()
    req = urllib.request.Request(overpass_url, data=data, headers={"User-Agent": finder.USER_AGENT})
    with urllib.request.urlopen(req, timeout=240) as resp:
        return parse_boundaries(json.load(resp))


def _inside(rings, lat, lon):
    """Gerade-ungerade-Regel über alle Ringe (Inseln und Löcher werden richtig behandelt)."""
    inside = False
    for ring in rings:
        j = len(ring) - 1
        for i in range(len(ring)):
            yi, xi = ring[i]
            yj, xj = ring[j]
            if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
                inside = not inside
            j = i
    return inside


def kreis_of(geo, lat, lon):
    if lat is None or lon is None:
        return None
    for key, k in (geo or {}).items():
        if _inside(k["rings"], lat, lon):
            return key
    return None

# ---------------------------------------------------------------- Statistik


_CODE = re.compile(r"^010(?:0[1-4]|5[1-9]|6[0-2])$")
_NUM = re.compile(r"^-?\d{1,3}(?:\.\d{3})*(?:,\d+)?$|^-?\d+(?:,\d+)?$")
_SUFFIX = re.compile(r",?\s*(kreis|kreisfreie stadt|landeshauptstadt|hansestadt|stadt)$")


def parse_stats_csv(raw):
    """CSV-Export (Regionaldatenbank / GENESIS / Statistikamt Nord) → {Kreisschlüssel: Wert}.

    Je Zeile wird der Kreis am Schlüssel (01001–01062) oder am Namen erkannt; der Wert ist die letzte Zahl
    in den Spalten danach (deutsches Format, z. B. 1.234 oder 12,5). Darum am besten eine Tabelle mit genau
    einem Merkmal und einem Zeitraum exportieren. Fehlende Werte („-“, „.“) werden übersprungen."""
    text = raw.decode("utf-8-sig", "replace") if isinstance(raw, bytes) else raw
    names = {name.lower(): key for key, (name, _, _) in KREISE.items()}
    result = {}
    for line in text.splitlines():
        sep = ";" if ";" in line else "\t" if "\t" in line else ","
        cells = [c.strip().strip('"').strip() for c in line.split(sep)]
        key, start = None, 0
        for i, cell in enumerate(cells):
            code = cell.split()[0] if cell else ""
            name = _SUFFIX.sub("", cell.lower()).strip()
            if _CODE.match(code):
                key, start = code, i + 1
            elif name in names:
                key, start = names[name], i + 1
            if key:
                break
        if not key:
            continue
        numbers = [float(c.replace(".", "").replace(",", ".")) for c in cells[start:] if _NUM.match(c)]
        if numbers:
            result[key] = numbers[-1]
    return result


def summary(store):
    """Je Kreis: Werte, Werte je 1.000 Einwohner, Rang, ob Wachstumsregion. store = kv „regionen“."""
    store = store or {}
    pop = {**EINWOHNER_UNGEFAEHR, **(store.get("einwohner") or {})}
    rows = []
    for key, (name, lat, lon) in KREISE.items():
        row = {"key": key, "name": name, "lat": lat, "lon": lon, "einwohner": pop.get(key)}
        for kind in ("gewerbe", "bau"):
            value = (store.get(kind) or {}).get(key)
            row[kind] = value
            row[f"{kind}_1000"] = round(value / pop[key] * 1000, 2) if value is not None and pop.get(key) else None
        rows.append(row)
    for kind in ("gewerbe", "bau"):
        ranked = sorted((r for r in rows if r[f"{kind}_1000"] is not None), key=lambda r: -r[f"{kind}_1000"])
        for i, r in enumerate(ranked):
            r[f"{kind}_rang"] = i + 1
            r[f"{kind}_top"] = i < TOP
    return rows


def bonus_info(store):
    """Für die Bewertung: {Kreisschlüssel: {name, gewerbe_top, bau_top}}."""
    return {r["key"]: {"name": r["name"], "gewerbe_top": r.get("gewerbe_top", False), "bau_top": r.get("bau_top", False)}
            for r in summary(store)}
