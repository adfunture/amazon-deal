"""Automatische Spaltenerkennung für Lieferantenlisten.

Stufe 1: Synonymwörterbuch (deterministisch, DE/EN/weitere).
Stufe 2 (optional): KI-Zuordnung unbekannter Spalten (sbc.integrations.llm).
Der Nutzer prüft und korrigiert die Zuordnung immer in der Oberfläche; die bestätigte
Zuordnung wird als lieferantenspezifisches Importprofil gespeichert.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

# Zielfelder des normalisierten Lieferantenangebots
FIELDS: dict[str, str] = {
    "gtin": "EAN / GTIN / UPC",
    "supplier_sku": "Lieferanten-Artikelnummer (SKU)",
    "mpn": "Herstellerartikelnummer (MPN)",
    "brand": "Marke / Hersteller",
    "title": "Produktname",
    "description": "Beschreibung",
    "net_price": "Einkaufspreis netto",
    "gross_price": "Einkaufspreis brutto",
    "tier_price": "Staffelpreis (mehrere Spalten möglich)",
    "rrp": "UVP / RRP (brutto)",
    "currency": "Währung",
    "stock": "Bestand / Verfügbarkeit",
    "moq": "Mindestbestellmenge",
    "pack_qty": "Stück je Einheit / VPE",
    "price_unit": "Preiseinheit (Preis je X Stück)",
    "lead_time": "Lieferzeit",
    "category": "Kategorie",
    "color": "Farbe",
    "size": "Größe",
    "condition": "Zustand",
    "image_url": "Bild-URL",
    "asin": "ASIN",
    "vat_rate": "MwSt-Satz",
    "valid_until": "Gültig bis",
    "ignore": "(ignorieren)",
}

_SYNONYMS: dict[str, list[str]] = {
    "gtin": ["ean", "gtin", "upc", "barcode", "ean13", "ean code", "eancode", "gtin13", "gtin14",
             "ean nummer", "ean-nummer", "strichcode", "jan"],
    "supplier_sku": ["sku", "artikelnummer", "art nr", "artnr", "art-nr", "artikel nr", "artikel-nr",
                     "item no", "item number", "item", "article", "article no", "article number",
                     "artikel", "product code", "productcode", "code", "ref", "reference", "referenz",
                     "bestellnummer", "best nr", "best.-nr", "material", "materialnummer", "part no"],
    "mpn": ["mpn", "herstellerartikelnummer", "hersteller artikelnummer", "hersteller-nr",
            "herstellernummer", "manufacturer part number", "mfr part", "mfr no", "oem", "oem nr",
            "oe nummer", "tecdoc pn", "tecdoc", "part number", "model", "modell", "modellnummer",
            "model no", "typ"],
    "brand": ["brand", "marke", "hersteller", "manufacturer", "make", "fabrikat", "label"],
    "title": ["product name", "produktname", "name", "bezeichnung", "artikelbezeichnung",
              "beschreibung", "titel", "title", "description", "product", "produkt",
              "artikelname", "item description", "kurztext"],
    "description": ["long description", "langtext", "langbeschreibung", "details",
                    "produktbeschreibung", "description long"],
    "net_price": ["ek", "einkaufspreis", "net price", "netto", "nettopreis", "price", "preis",
                  "unit price", "ek netto", "net", "dealer price", "händlerpreis", "haendlerpreis",
                  "hek", "price net", "cost", "your price", "ihr preis", "wholesale price",
                  "großhandelspreis", "angebotspreis", "offer price"],
    "gross_price": ["ek brutto", "bruttopreis", "brutto", "gross price", "price gross",
                    "preis inkl mwst", "inkl. mwst"],
    "rrp": ["rrp", "uvp", "msrp", "vk", "empf vk", "empfohlener vk", "retail price", "srp",
            "listenpreis", "list price", "verkaufspreis", "pvp"],
    "currency": ["currency", "währung", "waehrung", "curr", "cur"],
    "stock": ["quantity", "stock", "availability", "bestand", "lagerbestand", "lagermenge",
              "verfügbar", "verfuegbar", "verfügbarkeit", "qty", "menge", "available",
              "on hand", "inventory", "stock qty", "lager"],
    "moq": ["moq", "mindestmenge", "mindestbestellmenge", "min order", "min qty",
            "minimum order quantity", "min. bestellmenge"],
    "pack_qty": ["vpe", "verpackungseinheit", "pack size", "case pack", "pack qty",
                 "units per pack", "inhalt", "stück je karton", "pcs per carton", "inner pack"],
    "price_unit": ["preiseinheit", "pe", "price unit", "per"],
    "lead_time": ["lead time", "leadtime", "lieferzeit", "delivery time", "lieferdauer"],
    "category": ["category", "kategorie", "warengruppe", "product group", "produktgruppe"],
    "color": ["color", "colour", "farbe"],
    "size": ["size", "größe", "groesse", "grösse"],
    "condition": ["condition", "zustand"],
    "image_url": ["image", "image url", "bild", "bild url", "bildlink", "picture", "foto",
                  "image link", "main image"],
    "asin": ["asin"],
    "vat_rate": ["mwst", "ust", "vat", "tax rate", "steuersatz", "mehrwertsteuer"],
    "valid_until": ["valid until", "gültig bis", "gueltig bis", "expiry", "valid to"],
}

_TIER_HINT = re.compile(r"(moa|moq|ab\s*\d|from\s*\d|tier|staffel|\d[\d\s.]*\s*(stk|pcs|eur|€|k\b))", re.I)
_PRICE_HINT = re.compile(r"(price|preis|ek\b|netto|cost)", re.I)


def norm_header(h: object) -> str:
    s = unicodedata.normalize("NFKC", str(h or "")).strip().lower()
    s = re.sub(r"[_\s]+", " ", s)
    s = re.sub(r"[()\[\]:#*]", "", s)
    return s.strip(" .")


_LOOKUP: dict[str, str] = {}
for _field, _syns in _SYNONYMS.items():
    for _s in _syns:
        _LOOKUP.setdefault(norm_header(_s), _field)


@dataclass
class ColumnGuess:
    header: str
    field: str | None
    confidence: float
    method: str  # synonym|pattern|content|llm|none


def guess_field(header: str, samples: list[object] | None = None) -> ColumnGuess:
    n = norm_header(header)
    if not n:
        return ColumnGuess(header, None, 0.0, "none")
    if n in _LOOKUP:
        return ColumnGuess(header, _LOOKUP[n], 0.95, "synonym")
    # Preisspalten mit Staffelhinweis: "Price MOA 55 000 eur"
    if _PRICE_HINT.search(n) and _TIER_HINT.search(n):
        return ColumnGuess(header, "tier_price", 0.75, "pattern")
    # Inhaltsbasierte Erkennung hat Vorrang vor Teilwort-Treffern (z. B. "Code X" mit EANs)
    if samples:
        from sbc.domain.identifiers import normalize_gtin

        vals = [v for v in samples if v not in (None, "")]
        if vals and sum(1 for v in vals if normalize_gtin(v)[0]) / len(vals) >= 0.8:
            return ColumnGuess(header, "gtin", 0.6, "content")
    # Teilwortsuche (längste Übereinstimmung gewinnt)
    best: tuple[int, str] | None = None
    for syn, field in _LOOKUP.items():
        if len(syn) >= 3 and re.search(rf"(^|\s){re.escape(syn)}($|\s)", n):
            if best is None or len(syn) > best[0]:
                best = (len(syn), field)
    if best:
        return ColumnGuess(header, best[1], 0.7, "synonym")
    return ColumnGuess(header, None, 0.0, "none")


def guess_mapping(headers: list[str], sample_rows: list[list[object]]) -> list[ColumnGuess]:
    guesses: list[ColumnGuess] = []
    for idx, h in enumerate(headers):
        samples = [r[idx] if idx < len(r) else None for r in sample_rows[:30]]
        guesses.append(guess_field(h, samples))
    _resolve_conflicts(guesses)
    return guesses


def _resolve_conflicts(guesses: list[ColumnGuess]) -> None:
    """Pro Feld nur eine Spalte (außer tier_price). Bei Konflikten gewinnt die höhere Konfidenz;
    'beschreibung' fällt auf description zurück, wenn schon ein Titel existiert."""
    by_field: dict[str, list[ColumnGuess]] = {}
    for g in guesses:
        if g.field and g.field != "tier_price":
            by_field.setdefault(g.field, []).append(g)
    for field, gs in by_field.items():
        if len(gs) <= 1:
            continue
        gs.sort(key=lambda g: g.confidence, reverse=True)
        for loser in gs[1:]:
            if field == "title":
                loser.field, loser.confidence = "description", loser.confidence * 0.8
            elif field == "supplier_sku" and "mpn" not in by_field:
                loser.field, loser.confidence = "mpn", loser.confidence * 0.7
            else:
                loser.field, loser.confidence, loser.method = None, 0.0, "conflict"


def header_score(row: list[object]) -> int:
    return sum(1 for c in row if c not in (None, "") and guess_field(str(c)).field)


def detect_header_row(rows: list[list[object]], max_scan: int = 25) -> int:
    best_idx, best_score = 0, -1
    for i, row in enumerate(rows[:max_scan]):
        score = header_score(row)
        if score > best_score:
            best_idx, best_score = i, score
    return best_idx


def header_signature(headers: list[str]) -> str:
    joined = "|".join(norm_header(h) for h in headers if norm_header(h))
    return hashlib.sha256(joined.encode()).hexdigest()
