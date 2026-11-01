"""Kleiner PDF-Schreiber für Rechnungen (Firmen-OS, Stufe 8), ohne zusätzliche Bibliothek.

Nur, was eine Rechnung braucht: A4-Seiten, Text in Helvetica (normal und fett, Umlaute über WinAnsiEncoding),
rechtsbündiger Text, Linien. Mehrere Seiten, wenn der Platz nicht reicht. Ergebnis: Bytes einer gültigen PDF-Datei.
"""
import zlib

BREITE, HOEHE = 595.28, 841.89  # A4 in Punkt

# Breiten der Helvetica-Zeichen (1/1000 em) für rechtsbündigen Text; unbekannte Zeichen wie „o“
_W = {" ": 278, "!": 278, '"': 355, "#": 556, "$": 556, "%": 889, "&": 667, "'": 191, "(": 333, ")": 333, "*": 389,
      "+": 584, ",": 278, "-": 333, ".": 278, "/": 278, ":": 278, ";": 278, "<": 584, "=": 584, ">": 584, "?": 556,
      "@": 1015, "[": 278, "\\": 278, "]": 278, "^": 469, "_": 556, "`": 333, "{": 334, "|": 260, "}": 334, "~": 584,
      "€": 556, "§": 556, "°": 400, "–": 556, "„": 333, "“": 333, "·": 278}
_W.update({ch: 556 for ch in "0123456789"})
_W.update(dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", [667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833,
                                                  722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611])))
_W.update(dict(zip("abcdefghijklmnopqrstuvwxyz", [556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833,
                                                  556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500])))
_W.update({"ä": 556, "ö": 556, "ü": 556, "Ä": 667, "Ö": 778, "Ü": 722, "ß": 611, "é": 556, "è": 556})


def textbreite(text, groesse, fett=False):
    w = sum(_W.get(ch, 556) for ch in text) * groesse / 1000
    return w * (1.05 if fett else 1)


def _pdf_text(text):
    raw = text.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def umbrechen(text, groesse, breite, fett=False):
    """Text in Zeilen, die in `breite` passen (an Leerzeichen, harte Umbrüche bleiben)."""
    out = []
    for absatz in (text or "").split("\n"):
        zeile = ""
        for wort in absatz.split(" "):
            probe = f"{zeile} {wort}".strip()
            if zeile and textbreite(probe, groesse, fett) > breite:
                out.append(zeile)
                zeile = wort
            else:
                zeile = probe
        out.append(zeile)
    return out


class Dokument:
    def __init__(self):
        self.seiten = []
        self.neue_seite()

    def neue_seite(self):
        self.seiten.append([])

    def text(self, x, y, text, groesse=10, fett=False, rechts=False, grau=False):
        """y von oben gemessen (wie man liest)."""
        if rechts:
            x -= textbreite(text, groesse, fett)
        farbe = b"0.45 g " if grau else b"0 g "
        self.seiten[-1].append(farbe + b"BT /%s %.2f Tf %.2f %.2f Td (" % (b"F2" if fett else b"F1", groesse, x, HOEHE - y)
                               + _pdf_text(text) + b") Tj ET")

    def linie(self, x1, y, x2, staerke=0.5):
        self.seiten[-1].append(b"0.75 G %.2f w %.2f %.2f m %.2f %.2f l S" % (staerke, x1, HOEHE - y, x2, HOEHE - y))

    def bytes(self):
        objekte = [b"<< /Type /Catalog /Pages 2 0 R >>", None,
                   b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
                   b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]
        kids = []
        for inhalt in self.seiten:
            daten = zlib.compress(b"\n".join(inhalt))
            objekte.append(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(daten) + daten + b"\nendstream")
            inhalt_nr = len(objekte)
            objekte.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Contents %d 0 R "
                           b"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> >>" % (BREITE, HOEHE, inhalt_nr))
            kids.append(len(objekte))
        objekte[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % k for k in kids), len(kids))
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for nr, obj in enumerate(objekte, 1):
            offsets.append(len(out))
            out += b"%d 0 obj\n" % nr + obj + b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objekte) + 1)
        out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
        out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objekte) + 1, xref)
        return bytes(out)
