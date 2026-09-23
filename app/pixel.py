"""Pixelsprache der Marke als E-Mail-taugliche Tabellen.

Die Generatoren sind eine direkte Portierung aus den Brand Guidelines
(Ylva Labs Brand.dc.html). E-Mail-Clients kennen weder CSS-Grid noch SVG
zuverlässig, deshalb wird jeder Pixel als Tabellenzelle mit bgcolor gebaut.
"""
import math
from functools import lru_cache
from pathlib import Path

PARTIALS = Path(__file__).resolve().parent.parent / "email_templates" / "partials"

INK = "#1d2126"
BLUE = "#5aa9e6"
SPORE = "#f6d36d"
ZONES = ["#9fd4ff", "#5aa9e6", "#2f5bd3", "#1d2126", "#8a929c"]
BAYER = [(v + 0.5) / 16 for v in (0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5)]

ICONS = {
    "werkstatt": ("Werkstatt", [".....##.....", "....####....", "...######...", "..########..", ".##########.", "############", ".#........#.", ".#.++..##.#.", ".#.++..##.#.", ".#.....##.#.", ".#.....##.#.", "############"]),
    "mikro": ("Sprache", ["....####....", "...#++++#...", "...#++++#...", "...#++++#...", "...#++++#...", "...######...", ".#.######.#.", ".#..####..#.", "..#......#..", "...######...", ".....##.....", "...######..."]),
    "aehre": ("Landwirtschaft", [".....#......", "....#.#.....", "...#.#.#....", "....###.....", "...#.#.#....", "....###.....", "...#.#.#....", "....###.....", ".....#......", ".....#......", "...#####....", "..#######..."]),
    "blitz": ("Talente", [".......####.", "......####..", ".....####...", "....####....", "...########.", "..########..", ".....####...", "....####....", "...###......", "..##........", ".#..........", "............"]),
    "herz": ("Förderer", ["............", ".###....###.", "#####..#####", "############", "#####++#####", "####++++####", ".##########.", "..########..", "...######...", "....####....", ".....##.....", "............"]),
}


def _imul(a, b):
    r = (a * b) & 0xFFFFFFFF
    return r - 0x100000000 if r & 0x80000000 else r


def _h(x, y):
    h = (_imul(int(x), 374761393) + _imul(int(y), 668265263)) & 0xFFFFFFFF
    h = _imul(h ^ (h >> 13), 1274126177) & 0xFFFFFFFF
    return (h ^ (h >> 16)) / 4294967295


def _vn(x, y, seed):
    xi, yi = math.floor(x), math.floor(y)
    u, v = x - xi, y - yi
    s = lambda t: t * t * (3 - 2 * t)
    a, b = s(u), s(v)
    lerp = lambda p, q, t: p + (q - p) * t
    o = seed * 131
    return lerp(lerp(_h(xi + o, yi), _h(xi + 1 + o, yi), a),
                lerp(_h(xi + o, yi + 1), _h(xi + 1 + o, yi + 1), a), b)


@lru_cache(maxsize=64)
def band(cols, rows, seed, amp=2):
    """Pixel-Organismus-Band, Farben zeilenweise (None = leer)."""
    out = []
    for r in range(rows):
        row = []
        for c in range(cols):
            nx, ny = c / cols, r / rows
            if amp == 1:
                cy = .08 + .8 * nx + .07 * math.sin(nx * 10)
                d = (ny - cy) / (.36 - .12 * nx)
            else:
                cy = .5 + .18 * math.sin(nx * 7 + seed)
                d = (ny - cy) / .42
            n = _vn(c * .13, r * .13, seed) * .65 + _vn(c * .3, r * .3, seed) * .35
            f = (1 - d * d) * .8 + (n - .5) * 1.3
            lv = (f - .3) / .35
            col = None
            if lv > 0:
                if lv * 1.4 > BAYER[(r & 3) * 4 + (c & 3)]:
                    z = d * .55 + (n - .5) * 2.2
                    col = ZONES[max(0, min(4, math.floor((z + 1) / 2 * 5)))]
            elif lv > -.7 and _h(c + seed, r + 99) < .014:
                col = SPORE
            row.append(col)
        out.append(row)
    return out


def band_table(seed, cols=60, rows=6, cell=10):
    rows_html = []
    for row in band(cols, rows, seed):
        cells = []
        for col in row:
            if col:
                cells.append(f'<td width="{cell}" height="{cell}" bgcolor="{col}" style="border:1px solid #fff;font-size:0;line-height:0"></td>')
            else:
                cells.append(f'<td width="{cell}" height="{cell}" style="font-size:0;line-height:0"></td>')
        rows_html.append("<tr>" + "".join(cells) + "</tr>")
    width = cols * cell
    return (f'<table role="presentation" class="band" cellpadding="0" cellspacing="0" border="0" width="{width}" '
            f'style="width:{width}px;max-width:100%">' + "".join(rows_html) + "</table>")


def icon_table(key, cell=8, color=BLUE):
    _, bitmap = ICONS[key]
    size = len(bitmap[0]) * cell
    rows_html = []
    for line in bitmap:
        cells = []
        for ch in line:
            if ch == "#":
                cells.append(f'<td width="{cell}" height="{cell}" bgcolor="{color}" style="width:{cell}px;height:{cell}px;border:1px solid #f1f4f7;font-size:0;line-height:0"></td>')
            else:
                cells.append(f'<td width="{cell}" height="{cell}" style="width:{cell}px;height:{cell}px;font-size:0;line-height:0"></td>')
        rows_html.append("<tr>" + "".join(cells) + "</tr>")
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="{size}" '
            f'style="width:{size}px">' + "".join(rows_html) + "</table>")


@lru_cache(maxsize=1)
def original_band():
    return (PARTIALS / "pixelband.html").read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def logo_mark():
    return (PARTIALS / "logo.html").read_text(encoding="utf-8")


def band_preview_svg(seed, cols=60, rows=6):
    """Kleines SVG für die Auswahl in der Oberfläche (nicht für die E-Mail)."""
    rects = []
    for r, row in enumerate(band(cols, rows, seed)):
        for c, col in enumerate(row):
            if col:
                rects.append(f'<rect x="{c}" y="{r}" width=".86" height=".86" fill="{col}"/>')
    return f'<svg viewBox="0 0 {cols} {rows}" xmlns="http://www.w3.org/2000/svg" shape-rendering="crispEdges">{"".join(rects)}</svg>'


def icon_preview_svg(key):
    _, bitmap = ICONS[key]
    rects = [f'<rect x="{c}" y="{r}" width=".88" height=".88" fill="{BLUE}"/>'
             for r, line in enumerate(bitmap) for c, ch in enumerate(line) if ch == "#"]
    return f'<svg viewBox="0 0 12 12" xmlns="http://www.w3.org/2000/svg" shape-rendering="crispEdges">{"".join(rects)}</svg>'
