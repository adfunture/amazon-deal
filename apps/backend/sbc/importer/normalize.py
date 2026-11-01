"""Normalisierung einer Importzeile in ein Lieferantenangebot (rein funktional, testbar)."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sbc.domain.identifiers import is_asin, normalize_gtin, normalize_mpn, raw_to_digits
from sbc.domain.money import D, detect_currency, parse_decimal, parse_int, parse_lead_time_days
from sbc.domain.text import extract_pack_quantity


@dataclass
class ImportOptions:
    currency: str = "EUR"
    prices_are_gross: bool = False  # Lieferant gibt Bruttopreise an
    input_vat_rate: Decimal = Decimal("0.19")  # zum Herausrechnen der USt bei Bruttopreisen
    decimal_comma: bool | None = None
    price_tier_index: int | None = None  # welche Staffel als Basispreis gilt (None = erste verfügbare)
    default_condition: str = "new"
    sku_is_mpn: bool = False  # Lieferanten-Artikelnummer ist zugleich Herstellernummer (z. B. Markenteile)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> ImportOptions:
        d = d or {}
        return cls(
            currency=(d.get("currency") or "EUR").upper(),
            prices_are_gross=bool(d.get("prices_are_gross", False)),
            input_vat_rate=D(d.get("input_vat_rate", "0.19")),
            decimal_comma=d.get("decimal_comma"),
            price_tier_index=d.get("price_tier_index"),
            default_condition=d.get("default_condition", "new"),
            sku_is_mpn=bool(d.get("sku_is_mpn", False)),
        )


@dataclass
class NormalizedOffer:
    offer_key: str | None = None
    supplier_sku: str | None = None
    gtin: str | None = None
    gtin_raw: str | None = None
    mpn: str | None = None
    mpn_normalized: str | None = None
    asin: str | None = None
    brand: str | None = None
    title: str | None = None
    description: str | None = None
    unit_price_net: Decimal | None = None
    price_basis_qty: int = 1
    currency: str = "EUR"
    price_tax_input: str = "net"
    tier_prices: list[dict[str, Any]] = field(default_factory=list)
    rrp_gross: Decimal | None = None
    stock_qty: int | None = None
    moq: int | None = None
    unit_quantity: int = 1
    lead_time_days: int | None = None
    category: str | None = None
    color: str | None = None
    size: str | None = None
    condition: str = "new"
    image_urls: list[str] = field(default_factory=list)
    valid_until: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in self.__dict__.items():
            if isinstance(v, Decimal):
                out[k] = str(v)
            elif isinstance(v, list):
                out[k] = [
                    {kk: (str(vv) if isinstance(vv, Decimal) else vv) for kk, vv in i.items()}
                    if isinstance(i, dict) else i for i in v
                ]
            else:
                out[k] = v
        return out

    def content_hash(self) -> str:
        data = self.to_json()
        for k in ("errors", "warnings"):
            data.pop(k, None)
        return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def _str(v: object) -> str | None:
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip()
    return s or None


def _sku(v: object) -> str | None:
    """SKUs als Text behalten; Excel-Floats wie 520831.0 korrigieren."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return raw_to_digits(v) if float(v).is_integer() else str(v)
    return _str(v)


_CONDITIONS = {
    "neu": "new", "new": "new", "neuware": "new", "used": "used", "gebraucht": "used",
    "refurbished": "refurbished", "b-ware": "used_like_new", "b ware": "used_like_new",
    "retoure": "used_like_new", "defekt": "defective",
}


def normalize_row(raw: dict[str, object], mapping: dict[str, str], opts: ImportOptions) -> NormalizedOffer:
    """raw: header -> Zellwert, mapping: header -> Zielfeld."""
    o = NormalizedOffer(currency=opts.currency, condition=opts.default_condition)
    tier_cols: list[tuple[str, object]] = []
    values: dict[str, object] = {}
    for header, fieldname in mapping.items():
        if not fieldname or fieldname == "ignore" or header not in raw:
            continue
        if fieldname == "tier_price":
            tier_cols.append((header, raw[header]))
        elif fieldname not in values or values[fieldname] in (None, ""):
            values[fieldname] = raw[header]

    # Identifikatoren
    if (v := values.get("gtin")) not in (None, ""):
        o.gtin_raw = _str(v)
        o.gtin, err = normalize_gtin(v)
        if err:
            o.warnings.append(f"EAN '{o.gtin_raw}': {err}")
    o.supplier_sku = _clip(_sku(values.get("supplier_sku")), 200, o)
    o.mpn = _clip(_sku(values.get("mpn")), 200, o)
    if o.mpn is None and opts.sku_is_mpn:
        o.mpn = o.supplier_sku
    o.mpn_normalized = normalize_mpn(o.mpn)
    if (a := _str(values.get("asin"))) and is_asin(a):
        o.asin = a.upper()

    o.brand = _clip(_str(values.get("brand")), 200, o)
    o.title = _clip(_str(values.get("title")), 500, o)
    o.description = _str(values.get("description"))
    o.category = _clip(_str(values.get("category")), 300, o)
    o.color = _str(values.get("color"))
    o.size = _str(values.get("size"))
    if (c := _str(values.get("condition"))) is not None:
        o.condition = _CONDITIONS.get(c.lower(), c.lower())

    if (cur := _str(values.get("currency"))) is not None:
        o.currency = (detect_currency(cur) or cur).upper()[:3]

    # Preise
    vat_factor = Decimal(1) + opts.input_vat_rate
    for header, val in tier_cols:
        price = parse_decimal(val, opts.decimal_comma)
        o.tier_prices.append({"label": header, "price": price})
        if price is None and _str(val) not in (None, "-"):
            o.warnings.append(f"Staffelpreis '{header}' nicht lesbar: {val!r}")
        if (cur := detect_currency(str(header))) and "currency" not in values:
            o.currency = cur

    price: Decimal | None = None
    if values.get("net_price") not in (None, ""):
        price = parse_decimal(values["net_price"], opts.decimal_comma)
        o.price_tax_input = "gross" if opts.prices_are_gross else "net"
        if price is None:
            o.errors.append(f"Einkaufspreis nicht lesbar: {values['net_price']!r}")
    elif values.get("gross_price") not in (None, ""):
        price = parse_decimal(values["gross_price"], opts.decimal_comma)
        o.price_tax_input = "gross"
        if price is None:
            o.errors.append(f"Bruttopreis nicht lesbar: {values['gross_price']!r}")
    elif o.tier_prices:
        avail = [t for t in o.tier_prices if t["price"] is not None]
        if opts.price_tier_index is not None and opts.price_tier_index < len(o.tier_prices):
            chosen = o.tier_prices[opts.price_tier_index]
            price = chosen["price"]
            if price is None:
                o.errors.append(f"Gewählte Staffel '{chosen['label']}' ohne Preis")
        elif avail:
            price = avail[0]["price"]
        o.price_tax_input = "gross" if opts.prices_are_gross else "net"

    if price is not None:
        if price <= 0:
            o.errors.append(f"Einkaufspreis ≤ 0 ({price})")
            price = None
        elif o.price_tax_input == "gross":
            price = price / vat_factor
            o.warnings.append("Bruttopreis in Nettopreis umgerechnet")
    if opts.prices_are_gross:
        for t in o.tier_prices:
            if t["price"] is not None:
                t["price"] = t["price"] / vat_factor

    basis = parse_int(values.get("price_unit")) if values.get("price_unit") not in (None, "") else None
    if basis and basis > 1:
        o.price_basis_qty = basis
    if price is not None:
        o.unit_price_net = price / o.price_basis_qty if o.price_basis_qty > 1 else price

    if values.get("rrp") not in (None, ""):
        o.rrp_gross = parse_decimal(values["rrp"], opts.decimal_comma)

    if (s := values.get("stock")) not in (None, ""):
        o.stock_qty = _parse_stock(s)
        if o.stock_qty is None:
            o.warnings.append(f"Bestand nicht lesbar: {s!r}")
    if values.get("moq") not in (None, ""):
        o.moq = parse_int(values["moq"])
    pack = parse_int(values.get("pack_qty")) if values.get("pack_qty") not in (None, "") else None
    if pack and pack > 1:
        o.unit_quantity = pack
    elif o.title:
        qty, _ = extract_pack_quantity(o.title)
        if qty and qty > 1:
            o.unit_quantity = qty
            o.warnings.append(f"Verkaufseinheit aus Titel erkannt: {qty} Stück")
    o.lead_time_days = parse_lead_time_days(values.get("lead_time"))
    if (img := _str(values.get("image_url"))) and img.lower().startswith(("http://", "https://")):
        o.image_urls = [u.strip() for u in re.split(r"[,;|\s]+", img) if u.strip().startswith("http")]
    if (vu := values.get("valid_until")) not in (None, ""):
        o.valid_until = vu.isoformat() if isinstance(vu, (date, datetime)) else _str(vu)

    # Artikelnummern sind je Marke eindeutig, nicht global (z. B. CONTITECH 3PK675 ≠ GATES 3PK675)
    brand_key = re.sub(r"[^A-Z0-9]", "", (o.brand or "").upper())
    base_key = o.supplier_sku or o.gtin or (o.mpn_normalized and f"MPN:{o.mpn_normalized}")
    o.offer_key = f"{brand_key}|{base_key}" if base_key and brand_key and not (base_key == o.gtin) else base_key
    if o.offer_key and len(o.offer_key) > 200:  # stabil kürzen (Hash statt Abschneiden → keine Kollision)
        o.offer_key = o.offer_key[:150] + "#" + hashlib.sha256(o.offer_key.encode()).hexdigest()[:40]
    if not o.offer_key:
        o.errors.append("Keine Artikelnummer, EAN oder MPN – Zeile nicht zuordenbar")
    if o.unit_price_net is None and not any(e.startswith(("Einkaufspreis", "Brutto", "Gewählte")) for e in o.errors):
        o.errors.append("Kein Einkaufspreis vorhanden")
    if not o.title and not o.gtin and not o.mpn:
        o.warnings.append("Weder Titel noch EAN/MPN – Matching kaum möglich")
    return o


def _clip(v: str | None, n: int, o: NormalizedOffer) -> str | None:
    """Text auf Spaltenlänge kürzen und als Warnung vermerken (statt Datenbankfehler)."""
    if v is not None and len(v) > n:
        o.warnings.append(f"Text auf {n} Zeichen gekürzt: {v[:40]}…")
        return v[: n - 1] + "…"
    return v


def _parse_stock(v: object) -> int | None:
    if isinstance(v, (int, float)):
        return max(int(v), 0)
    s = str(v).strip().lower()
    if s in {"ja", "yes", "verfügbar", "available", "in stock", "auf lager"}:
        return None  # verfügbar, Menge unbekannt
    if s in {"nein", "no", "0", "out of stock", "nicht verfügbar"}:
        return 0
    m = re.search(r"(\d[\d.]*)", s)
    if m:
        return int(m.group(1).replace(".", ""))
    return None
