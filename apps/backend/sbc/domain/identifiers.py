"""GTIN/EAN/UPC-Normalisierung und Prüfziffernvalidierung.

Alle GTINs werden intern auf 14 Stellen (GTIN-14) aufgefüllt. Die Prüfziffer ist
unabhängig von führenden Nullen, daher sind EAN-13 "0123456789012" und UPC-12
"123456789012" dieselbe GTIN. So gehen führende Nullen (z. B. durch Excel-Zahlenformat
verloren) nicht verloren.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_NON_DIGIT = re.compile(r"\D")
_ASIN = re.compile(r"^(B0[0-9A-Z]{8}|\d{9}[\dX])$")


def gtin_check_digit(body: str) -> int:
    """Prüfziffer für die GTIN ohne letzte Stelle (beliebige Länge)."""
    total = 0
    for i, ch in enumerate(reversed(body)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - total % 10) % 10


def is_valid_gtin(code: str) -> bool:
    if not code or not code.isdigit() or len(code) not in (8, 12, 13, 14):
        return False
    return gtin_check_digit(code[:-1]) == int(code[-1])


def raw_to_digits(value: object) -> str | None:
    """Wandelt Zell-Inhalte in eine Ziffernfolge um, ohne Präzisionsverlust.

    Excel liefert große Zahlen gern als float (4.006381333931e12) oder als
    "4006381333931.0" – beides wird korrekt behandelt.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value <= 0:  # NaN
            return None
        return str(int(Decimal(repr(value))))
    s = str(value).strip()
    if not s:
        return None
    if re.fullmatch(r"\d+(\.0+)?", s):
        return s.split(".")[0]
    if re.fullmatch(r"\d+(\.\d+)?[eE]\+?\d+", s):
        try:
            return str(int(Decimal(s)))
        except InvalidOperation:
            return None
    digits = _NON_DIGIT.sub("", s)
    # Nur akzeptieren, wenn der Wert im Wesentlichen numerisch war (Leerzeichen/Bindestriche)
    if digits and len(re.sub(r"[\s\-]", "", s)) == len(digits):
        return digits
    return None


def normalize_gtin(value: object) -> tuple[str | None, str | None]:
    """Gibt (gtin14, fehler) zurück. gtin14 ist None, wenn ungültig."""
    digits = raw_to_digits(value)
    if not digits:
        return None, "leer oder nicht numerisch" if value not in (None, "") else None
    if len(digits) > 14:
        return None, f"zu lang ({len(digits)} Stellen)"
    if len(digits) < 8:
        return None, f"zu kurz ({len(digits)} Stellen)"
    # Excel-Verlust führender Nullen: 11-stellige Werte stammen meist von UPC-12/EAN-13
    padded = digits.zfill(14)
    if not is_valid_gtin(padded):
        return None, "Prüfziffer ungültig"
    if set(padded) == {"0"}:
        return None, "nur Nullen"
    return padded, None


def display_gtin(gtin14: str) -> str:
    """Kürzeste übliche Darstellung (EAN-13 für europäische Codes, sonst 14)."""
    if gtin14.startswith("000000"):
        return gtin14[6:]  # EAN-8
    if gtin14.startswith("0"):
        return gtin14[1:]  # EAN-13 (UPC-12 erscheint mit führender 0)
    return gtin14


def normalize_mpn(value: object) -> str | None:
    """Herstellerartikelnummer vergleichbar machen: Großschreibung, ohne Trennzeichen."""
    if value is None:
        return None
    s = str(value).strip().upper()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    s = re.sub(r"[\s\-_./]", "", s)
    return s or None


def is_asin(value: str) -> bool:
    return bool(_ASIN.match(value.strip().upper()))
