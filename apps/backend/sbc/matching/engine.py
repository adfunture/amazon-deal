"""Mehrstufige Matching-Engine (Stufe 1 Identifikatoren, Stufe 2 Text/Attribute, Stufe 4 Klasse).

Stufe 3 (KI-Text/Bild) liegt in ``sbc.matching.ai`` und wird nur für wirtschaftlich relevante,
unsichere Kandidaten ausgelöst. Regeln dieser Datei haben Vorrang: Eine gleiche EAN reicht
nicht, wenn Verkaufseinheit, Zustand oder Variante abweichen.

Der Score ist eine erklärbare Punktsumme (0–100, je Signal in ``breakdown`` aufgeführt); intern
zusätzlich als 0..1 (``score``) für die Kalibrierung. Die angezeigte Konfidenz wird über
``sbc.matching.calibration`` aus manuell geprüften Entscheidungen kalibriert, sobald genug
Labels vorliegen; bis dahin ist sie als "unkalibriert" gekennzeichnet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from rapidfuzz import fuzz

from sbc.domain.identifiers import normalize_gtin, normalize_mpn
from sbc.domain.text import extract_attributes, extract_pack_quantity, normalize_text
from sbc.models import MatchClass

ENGINE_VERSION = "match-1.1.0"

# Punkte je Signal (Summe wird auf 0..100 begrenzt). Fehlende Information = 0 Punkte, nie positiv.
POINTS = {
    "gtin": 45, "gtin_differs": -10, "mpn": 30, "mpn_in_title": 20, "brand": 10, "brand_conflict": -30,
    "title_max": 25, "model_tokens": 10, "color_conflict": -30, "size_conflict": -30,
    "measure_conflict": -20, "condition_conflict": -40, "unit_unclear": -20,
}
# Identitätskritische Widersprüche verhindern jede automatische Bestätigung – unabhängig vom Score.
IDENTITY_CONFLICTS = ("brand", "condition", "color", "size", "measures", "unit_quantity")


class Decision:
    VERIFIED = "VERIFIED_MATCH"
    REVIEW = "MANUAL_REVIEW"
    NO_MATCH = "NO_MATCH"


@dataclass
class ItemView:
    """Vergleichbare Sicht auf Lieferanten- bzw. Marktplatzartikel."""

    title: str | None
    brand: str | None = None
    gtins: set[str] = field(default_factory=set)  # GTIN-14
    mpn: str | None = None
    model: str | None = None
    unit_quantity: int | None = None  # explizit angegebene Stückzahl
    condition: str = "new"
    color: str | None = None
    size: str | None = None
    image_urls: list[str] = field(default_factory=list)

    @classmethod
    def build(cls, title, brand=None, gtins=(), mpn=None, model=None, unit_quantity=None,
              condition="new", color=None, size=None, image_urls=()) -> ItemView:
        norm = set()
        for g in gtins or ():
            n, _ = normalize_gtin(g)
            if n:
                norm.add(n)
        return cls(title, brand, norm, mpn, model, unit_quantity, condition or "new",
                   color, size, list(image_urls or []))


@dataclass
class MatchOutcome:
    classification: MatchClass
    score: Decimal
    units_required: int | None
    stage: str
    matched: dict = field(default_factory=dict)
    mismatched: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    needs_ai: bool = False
    breakdown: list[dict] = field(default_factory=list)  # [{signal, points, detail}]
    conflicts: list[str] = field(default_factory=list)

    @property
    def reasoning(self) -> str:
        return "; ".join(self.reasons)

    @property
    def score_100(self) -> int:
        return int((self.score * 100).to_integral_value())


def _brand_eq(a: str | None, b: str | None) -> bool | None:
    if not a or not b:
        return None
    na, nb = normalize_text(a).replace(" ", ""), normalize_text(b).replace(" ", "")
    return na == nb or na in nb or nb in na


def _units(item: ItemView) -> tuple[int, str]:
    if item.unit_quantity and item.unit_quantity > 0:
        return item.unit_quantity, "explizit"
    qty, ev = extract_pack_quantity(item.title)
    if qty:
        return qty, f"Titel: '{ev}'"
    return 1, "Standard (keine Mengenangabe)"


def compare(supplier: ItemView, listing: ItemView) -> MatchOutcome:
    matched: dict = {}
    mismatched: dict = {}
    reasons: list[str] = []
    breakdown: list[dict] = []

    def add(signal: str, points: float, detail: str) -> None:
        breakdown.append({"signal": signal, "points": round(points, 1), "detail": detail})

    # ---------------- Identifikatoren
    gtin_hit = bool(supplier.gtins & listing.gtins)
    if gtin_hit:
        matched["gtin"] = sorted(supplier.gtins & listing.gtins)
        add("gtin", POINTS["gtin"], "GTIN identisch")
        reasons.append("GTIN identisch")
    elif supplier.gtins and listing.gtins:
        mismatched["gtin"] = {"supplier": sorted(supplier.gtins), "listing": sorted(listing.gtins)}
        add("gtin", POINTS["gtin_differs"], "GTINs vorhanden, aber verschieden")
        reasons.append("GTINs vorhanden, aber verschieden")

    s_mpn, l_mpn = normalize_mpn(supplier.mpn), normalize_mpn(listing.mpn or listing.model)
    mpn_hit = False
    if s_mpn and l_mpn:
        if s_mpn == l_mpn:
            mpn_hit = True
            matched["mpn"] = s_mpn
            add("mpn", POINTS["mpn"], f"MPN identisch ({s_mpn})")
            reasons.append("MPN identisch")
        else:
            mismatched["mpn"] = {"supplier": s_mpn, "listing": l_mpn}
    # MPN im Titel des Listings (häufig bei Ersatzteilen/Filtern)
    if s_mpn and not mpn_hit and listing.title:
        compact_title = normalize_mpn(listing.title) or ""
        if len(s_mpn) >= 4 and s_mpn in compact_title:
            mpn_hit = True
            matched["mpn_in_title"] = s_mpn
            add("mpn", POINTS["mpn_in_title"], "MPN im Marktplatztitel gefunden")
            reasons.append("MPN im Marktplatztitel gefunden")

    brand_eq = _brand_eq(supplier.brand, listing.brand)
    if brand_eq is True:
        matched["brand"] = listing.brand
        add("brand", POINTS["brand"], "Marke identisch")
    elif brand_eq is False:
        mismatched["brand"] = {"supplier": supplier.brand, "listing": listing.brand}
        add("brand", POINTS["brand_conflict"], "Marke abweichend")
        reasons.append("Marke abweichend")

    # ---------------- Text & Attribute
    st, lt = normalize_text(supplier.title), normalize_text(listing.title)
    text_sim = 0.0
    if st and lt:
        text_sim = max(fuzz.token_set_ratio(st, lt), fuzz.partial_token_sort_ratio(st, lt)) / 100
        matched["title_similarity"] = round(text_sim, 3)
        add("title", POINTS["title_max"] * text_sim, f"Titelähnlichkeit {text_sim:.0%}")

    sa = extract_attributes(supplier.title, supplier.brand)
    la = extract_attributes(listing.title, listing.brand)
    s_colors = sa.colors | ({normalize_text(supplier.color)} if supplier.color else set())
    if s_colors and la.colors and not (s_colors & la.colors):
        mismatched["color"] = {"supplier": sorted(s_colors), "listing": sorted(la.colors)}
        add("color", POINTS["color_conflict"], "Farbe abweichend")
        reasons.append("Farbe abweichend")
    s_sizes = sa.sizes | ({supplier.size.upper()} if supplier.size else set())
    if s_sizes and la.sizes and not (s_sizes & la.sizes):
        mismatched["size"] = {"supplier": sorted(s_sizes), "listing": sorted(la.sizes)}
        add("size", POINTS["size_conflict"], "Größe abweichend")
        reasons.append("Größe abweichend")
    if sa.measures and la.measures and not (sa.measures & la.measures):
        mismatched["measures"] = {"supplier": sorted(sa.measures), "listing": sorted(la.measures)}
        add("measures", POINTS["measure_conflict"], "Maße/Mengen abweichend")
        reasons.append("Maße/Mengen abweichend")
    shared_models = sa.model_tokens & la.model_tokens
    if shared_models:
        matched["model_tokens"] = sorted(shared_models)
        add("model", POINTS["model_tokens"], f"Modell {', '.join(sorted(shared_models))}")
    elif sa.model_tokens and la.model_tokens and not mpn_hit and not gtin_hit:
        mismatched["model_tokens"] = {"supplier": sorted(sa.model_tokens), "listing": sorted(la.model_tokens)}

    if supplier.condition != listing.condition:
        mismatched["condition"] = {"supplier": supplier.condition, "listing": listing.condition}
        add("condition", POINTS["condition_conflict"], "Zustand abweichend")
        reasons.append("Zustand abweichend")

    # ---------------- Verkaufseinheit
    s_units, s_ev = _units(supplier)
    l_units, l_ev = _units(listing)
    units_required: int | None
    if l_units % s_units == 0:
        units_required = l_units // s_units
    else:
        units_required = None
        mismatched["unit_quantity"] = {"supplier": s_units, "listing": l_units}
        add("units", POINTS["unit_unclear"], f"Verkaufseinheit unklar ({s_units} vs. {l_units})")
        reasons.append(f"Verkaufseinheit nicht ganzzahlig ableitbar ({s_units} vs. {l_units})")
    if s_units != l_units:
        matched["unit_evidence"] = {"supplier": s_ev, "listing": l_ev}

    total = max(0.0, min(100.0, sum(b["points"] for b in breakdown)))
    score = (Decimal(str(round(total, 1))) / 100).quantize(Decimal("0.0001"))
    conflicts = [k for k in IDENTITY_CONFLICTS if k in mismatched]
    strong_id = gtin_hit or (mpn_hit and brand_eq is not False)

    # ---------------- Klassifizierung (regelbasiert)
    hard_conflict = any(k in mismatched for k in ("brand", "condition"))
    variant_conflict = any(k in mismatched for k in ("color", "size", "measures"))
    bundle_hint = la.bundle_hint and not sa.bundle_hint

    def out(cls: MatchClass, needs_ai: bool = False, stage: str = "identifier") -> MatchOutcome:
        return MatchOutcome(cls, score, units_required, stage, matched, mismatched, reasons, needs_ai,
                            breakdown, conflicts)

    if "condition" in mismatched:
        return out(MatchClass.REVIEW_REQUIRED)
    if strong_id and not hard_conflict:
        if units_required is None:
            return out(MatchClass.REVIEW_REQUIRED)
        if variant_conflict:
            return out(MatchClass.VARIANT_MATCH, needs_ai=True)
        if bundle_hint and units_required == 1:
            reasons.append("Bundle-/Zubehörhinweis im Marktplatztitel")
            return out(MatchClass.BUNDLE, needs_ai=True)
        if units_required > 1:
            reasons.append(f"Multipack: {units_required} Lieferanteneinheiten je Angebot")
            return out(MatchClass.MULTIPACK)
        if gtin_hit and (text_sim >= 0.5 or mpn_hit or brand_eq):
            return out(MatchClass.EXACT_MATCH)
        if mpn_hit and brand_eq and text_sim >= 0.5:
            return out(MatchClass.EXACT_MATCH)
        return out(MatchClass.POSSIBLE_MATCH, needs_ai=True)
    if strong_id and hard_conflict:
        return out(MatchClass.REVIEW_REQUIRED, needs_ai=True)

    # Kein harter Identifikator: nur Text
    if text_sim >= 0.85 and brand_eq and shared_models and not variant_conflict:
        cls = MatchClass.MULTIPACK if units_required and units_required > 1 else MatchClass.POSSIBLE_MATCH
        return out(cls, needs_ai=True, stage="text")
    if text_sim >= 0.7 and brand_eq is not False:
        return out(MatchClass.VARIANT_MATCH if variant_conflict else MatchClass.POSSIBLE_MATCH,
                   needs_ai=True, stage="text")
    return out(MatchClass.NO_MATCH, stage="text")


# Nur exakte Treffer dürfen automatisch bestätigt werden. Multipacks, Bundles und Varianten
# (z. B. Einzelartikel gegen 2er-Pack) brauchen immer eine manuelle Freigabe.
AUTO_ACCEPT_CLASSES = {MatchClass.EXACT_MATCH}


def decide(outcome: MatchOutcome, confidence: Decimal, calibrated: bool, min_confidence: Decimal,
           auto_verify_min_score: int) -> str:
    """Entscheidungsstatus. Ohne Kalibrierung zählt der Punktscore, mit Kalibrierung die Konfidenz."""
    if outcome.classification == MatchClass.NO_MATCH:
        return Decision.NO_MATCH
    if outcome.classification not in AUTO_ACCEPT_CLASSES or outcome.conflicts:
        return Decision.REVIEW
    ok = confidence >= min_confidence if calibrated else outcome.score_100 >= auto_verify_min_score
    return Decision.VERIFIED if ok else Decision.REVIEW


def requires_review(cls: MatchClass, confidence: Decimal, min_confidence: Decimal) -> bool:
    """Bundles/Varianten/Multipacks/Unsicheres immer manuell; Exakt nur unter Schwelle."""
    if cls not in AUTO_ACCEPT_CLASSES:
        return cls != MatchClass.NO_MATCH
    return confidence < min_confidence
