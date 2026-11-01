"""Nachfrage-/Wettbewerbs-Indikatoren und konfigurierbarer Opportunity-Score (0..100).

Fehlende Datenpunkte werden NICHT geschätzt: Die betroffene Komponente zählt 0 Punkte und
wird in ``coverage`` ausgewiesen. Damit ist ein Score ohne Nachfragedaten sichtbar niedriger
und als unvollständig erkennbar.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal

from sbc.models import MatchClass

SCORING_VERSION = "score-1.0.0"


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


@dataclass
class DemandEstimate:
    market_units_month: Decimal | None
    own_units_month: Decimal | None
    low: Decimal | None
    high: Decimal | None
    method: str
    source: str
    confidence: Decimal
    notes: list[str] = field(default_factory=list)


def estimate_demand(monthly_sold: int | None, rank_drops30: int | None, rank_drops90: int | None,
                    offer_count: int | None, own_share: Decimal, own_units_hist: Decimal | None = None) -> DemandEstimate:
    """Absatzschätzung mit dokumentierter Methode und Unsicherheit.

    1. Eigene Verkaufshistorie (falls vorhanden) – höchste Konfidenz.
    2. Amazon 'monthlySold' (über Keepa; Amazon zeigt Bandbreiten wie '100+') – Untergrenze.
    3. Sales-Rank-Drops als Proxy: jeder Drop ≈ mind. ein Verkauf → Untergrenze, wahrer Wert
       typischerweise 1–3× höher (Mehrfachverkäufe zwischen zwei Messpunkten).
    Eigener Absatz = Markt × realistischer Anteil, begrenzt durch Anzahl Wettbewerber.
    """
    notes: list[str] = []
    if own_units_hist is not None and own_units_hist > 0:
        return DemandEstimate(None, own_units_hist, own_units_hist * Decimal("0.7"),
                              own_units_hist * Decimal("1.3"), "own_history", "eigene Verkäufe",
                              Decimal("0.8"), ["Basis: eigene Verkaufshistorie"])
    market = low = high = None
    method, source, conf = "none", "keine Daten", Decimal("0")
    if monthly_sold:
        market = Decimal(monthly_sold)
        low, high = market, market * 2
        method, source, conf = "amazon_monthly_sold", "Amazon 'x+ gekauft' via Keepa", Decimal("0.6")
        notes.append("Amazon-Angabe ist eine gerundete Untergrenze")
    elif rank_drops30 is not None or rank_drops90 is not None:
        d30 = Decimal(rank_drops30) if rank_drops30 is not None else Decimal(rank_drops90 or 0) / 3
        market = d30
        low, high = d30, d30 * 3
        method, source, conf = "sales_rank_drops", "Keepa Sales-Rank-Drops", Decimal("0.4")
        notes.append("Rank-Drops sind eine Untergrenze der Verkäufe")
    if market is None:
        return DemandEstimate(None, None, None, None, method, source, conf, ["Keine Nachfragedaten"])
    sellers = max(offer_count or 1, 1)
    share = min(own_share, Decimal(1) / Decimal(sellers) * Decimal("1.5"))
    own = (market * share).quantize(Decimal("0.1"))
    notes.append(f"Eigener Anteil {share:.2%} bei {sellers} Anbietern")
    return DemandEstimate(market, own, (low * share).quantize(Decimal("0.1")) if low else None,
                          (high * share).quantize(Decimal("0.1")) if high else None,
                          method, source, conf, notes)


def demand_score(market_units: Decimal | None) -> float | None:
    if market_units is None:
        return None
    # log-Skala: 1/Monat → 0, 10 → 50, 100 → 100
    return _clamp(50 * math.log10(max(float(market_units), 1)))


def competition_score(offer_count: int | None, amazon_is_seller: bool | None) -> float | None:
    if offer_count is None and amazon_is_seller is None:
        return None
    s = 100.0
    if offer_count is not None:
        s -= min(offer_count, 20) * 4  # 20+ Anbieter → -80
    if amazon_is_seller:
        s -= 40
    return _clamp(s)


def stability_score(price_cv90: Decimal | None) -> float | None:
    if price_cv90 is None:
        return None
    return _clamp(100 - float(price_cv90) * 400)  # CV 25 % → 0


@dataclass
class ScoreInputs:
    contribution: Decimal | None
    margin: Decimal | None
    roi: Decimal | None
    market_units: Decimal | None
    offer_count: int | None
    amazon_is_seller: bool | None
    price_cv90: Decimal | None
    contribution_per_1000_month: Decimal | None
    stock_qty: int | None
    moq: int | None
    lead_time_days: int | None
    supplier_rating: Decimal | None
    match_class: MatchClass | None
    match_confidence: Decimal | None
    match_confirmed: bool
    units_required: int | None
    condition: str
    restricted: bool
    calc_complete: bool


def score(si: ScoreInputs, weights: dict[str, str], thresholds: dict) -> dict:
    w = {k: float(v) for k, v in weights.items()}
    comps: dict[str, float | None] = {}
    if si.margin is not None and si.contribution is not None:
        m = _clamp(float(si.margin) / 0.30 * 100)
        c = _clamp(float(si.contribution) / 20 * 100)
        comps["profit"] = 0.6 * m + 0.4 * c
    else:
        comps["profit"] = None
    comps["demand"] = demand_score(si.market_units)
    stab, comp = stability_score(si.price_cv90), competition_score(si.offer_count, si.amazon_is_seller)
    comps["stability_competition"] = (
        None if stab is None and comp is None else
        (stab if comp is None else comp if stab is None else (stab + comp) / 2)
    )
    comps["turnover"] = (_clamp(float(si.contribution_per_1000_month) / 300 * 100)
                         if si.contribution_per_1000_month is not None else None)
    risk = 100.0
    if si.stock_qty is not None and si.moq and si.stock_qty < si.moq:
        risk -= 50
    if si.lead_time_days and si.lead_time_days > 14:
        risk -= min((si.lead_time_days - 14) * 1.5, 40)
    if si.supplier_rating is not None:
        risk -= (5 - float(si.supplier_rating)) * 8
    comps["sourcing_risk"] = _clamp(risk)

    total = sum((comps[k] or 0.0) * w.get(k, 0) for k in comps)
    coverage = sum(w.get(k, 0) for k, v in comps.items() if v is not None)

    # Harte Ausschlusskriterien (konfigurierbar)
    reasons: list[str] = []
    min_margin = Decimal(str(thresholds["min_margin"]))
    min_db = Decimal(str(thresholds["min_contribution"]))
    min_conf = Decimal(str(thresholds["min_match_confidence"]))
    if si.contribution is None:
        reasons.append("Kalkulation unvollständig (Pflichtwerte fehlen)")
    else:
        if si.contribution < 0:
            reasons.append("Negativer Deckungsbeitrag")
        elif si.contribution < min_db:
            reasons.append(f"DB je Stück unter {min_db} €")
        if si.margin is not None and si.margin < min_margin:
            reasons.append(f"DB-Marge unter {min_margin:.0%}")
    if si.match_class in (None, MatchClass.NO_MATCH):
        reasons.append("Kein Produktmatch")
    elif not si.match_confirmed:
        if si.match_class not in (MatchClass.EXACT_MATCH, MatchClass.MULTIPACK):
            reasons.append(f"Match ungeklärt ({si.match_class.value}) – manuelle Prüfung nötig")
        elif (si.match_confidence or 0) < min_conf:
            reasons.append(f"Matching-Konfidenz unter {min_conf:.0%}")
    if si.units_required is None:
        reasons.append("Unklare Verkaufseinheit")
    if thresholds.get("require_stock", True) and si.stock_qty == 0:
        reasons.append("Nicht lieferbar (Bestand 0)")
    if si.condition not in thresholds.get("allowed_conditions", ["new"]):
        reasons.append(f"Unzulässiger Zustand: {si.condition}")
    if si.restricted:
        reasons.append("Rechtliche/Marktplatz-Beschränkung markiert")

    return {
        "score": round(total, 2),
        "components": {k: (round(v, 1) if v is not None else None) for k, v in comps.items()},
        "weights": weights,
        "coverage": round(coverage, 3),
        "complete": coverage >= 0.999 and si.calc_complete,
        "exclusion_reasons": reasons,
        "version": SCORING_VERSION,
    }
