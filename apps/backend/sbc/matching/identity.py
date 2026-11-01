"""Identitätskritische Merkmale eines Lieferantenangebots.

Dient zwei Zwecken:
1. Wiederverwendung bekannter Zuordnungen (Stufe 1): Ein Angebot erbt eine bestätigte oder
   abgelehnte ASIN-Zuordnung seines Produkts, solange es ihr nicht widerspricht.
2. Erkennung von Identitätsänderungen beim erneuten Import: Weichen die Merkmale ab, muss das
   Matching erneut geprüft werden.

Fehlende Werte widersprechen nie (z. B. ein Lieferant nennt keine Farbe), nur abweichende
vorhandene Werte gelten als Konflikt.
"""

from __future__ import annotations

import hashlib
import json

from sbc.domain.identifiers import normalize_mpn
from sbc.domain.text import extract_attributes, normalize_text
from sbc.models import SupplierOffer

KEYS = ("gtin", "mpn", "brand", "unit_quantity", "condition", "colors", "sizes", "measures", "materials")


def identity_attrs(offer: SupplierOffer) -> dict:
    ta = extract_attributes(offer.title, offer.brand)
    return {
        "gtin": offer.gtin,
        "mpn": normalize_mpn(offer.mpn),
        "brand": normalize_text(offer.brand).replace(" ", "") or None,
        "unit_quantity": offer.unit_quantity or 1,
        "condition": offer.condition or "new",
        "colors": sorted(ta.colors),
        "sizes": sorted(ta.sizes),
        "measures": sorted(ta.measures),
        "materials": sorted(ta.materials),
    }


def fingerprint(attrs: dict) -> str:
    return hashlib.sha256(json.dumps({k: attrs.get(k) for k in KEYS}, sort_keys=True).encode()).hexdigest()


def conflicts(old: dict, new: dict) -> list[str]:
    """Merkmale, die in beiden Ständen vorhanden sind und sich unterscheiden."""
    out = []
    for k in KEYS:
        a, b = old.get(k), new.get(k)
        if a in (None, "", []) or b in (None, "", []):
            continue
        if isinstance(a, list) and isinstance(b, list):
            if not set(a) & set(b):
                out.append(k)
        elif a != b:
            out.append(k)
    return out


def refresh(offer: SupplierOffer) -> tuple[dict, bool]:
    """Berechnet die Merkmale neu. Rückgabe: (Konflikte als dict alt/neu, Identität geändert?)."""
    new = identity_attrs(offer)
    old = offer.identity_attrs or {}
    changed = bool(old) and bool(conflicts(old, new))
    diff = {k: {"alt": old.get(k), "neu": new.get(k)} for k in conflicts(old, new)} if old else {}
    offer.identity_attrs = new
    offer.identity_fingerprint = fingerprint(new)
    return diff, changed
