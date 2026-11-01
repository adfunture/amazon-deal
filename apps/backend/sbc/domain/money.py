"""Geld- und Zahlparsing mit Decimal. Keine float-Arithmetik für Geldbeträge."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
_CURRENCY_SYMBOLS = {"€": "EUR", "$": "USD", "£": "GBP", "zł": "PLN", "pln": "PLN", "chf": "CHF"}


def D(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(repr(value))
    return Decimal(str(value))


def q2(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def parse_decimal(value: object, decimal_comma: bool | None = None) -> Decimal | None:
    """Parst '1.234,56', '1,234.56', '12,5', '€ 9.99', 12.5 → Decimal.

    decimal_comma=None: automatische Erkennung je Wert.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, Decimal)):
        return D(value)
    if isinstance(value, float):
        if value != value:
            return None
        return D(value)
    s = str(value).strip()
    if not s or s in {"-", "–", "n/a", "N/A", "na", "#N/A"}:
        return None
    s = re.sub(r"[€$£]|eur|usd|gbp|pln|chf|zł", "", s, flags=re.I).strip()
    s = s.replace(" ", "").replace(" ", "").replace("'", "")
    if not re.fullmatch(r"-?[\d.,]+", s):
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        if decimal_comma is False and re.fullmatch(r"-?\d{1,3}(,\d{3})+", s):
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def detect_currency(text: str | None) -> str | None:
    if not text:
        return None
    low = text.lower()
    for sym, code in _CURRENCY_SYMBOLS.items():
        if sym in low:
            return code
    m = re.search(r"\b(EUR|USD|GBP|PLN|CHF|SEK|CZK|DKK)\b", text.upper())
    return m.group(1) if m else None


def parse_int(value: object) -> int | None:
    d = parse_decimal(value)
    if d is None:
        if isinstance(value, str):
            m = re.search(r"\d+", value)
            return int(m.group(0)) if m else None
        return None
    try:
        return int(d)
    except (ValueError, InvalidOperation):
        return None


def parse_lead_time_days(value: object) -> int | None:
    """'1 DAY', '3 DAYS', '2-3 Wochen', 'approx. 7-8 weeks' → Tage (oberer Wert, konservativ)."""
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return int(value)
    s = str(value).lower()
    nums = [int(n) for n in re.findall(r"\d+", s)]
    if not nums:
        return None
    n = max(nums)
    if re.search(r"week|woche|wk", s):
        return n * 7
    if re.search(r"month|monat", s):
        return n * 30
    return n
