"""Analysepipeline (Level 1 → 2 → 3) für Lieferantenangebote.

Level 1 (günstig): vorhandene Daten, EAN/MPN gegen bekannte Listings, Grobkalkulation.
Level 2: Keepa-Abfrage (Tokens!), Text-/Attribut-Matching, Nachfrageindikatoren.
Level 3 (teuer): KI-Text/Bild – nur wenn das Angebot wirtschaftlich relevant ist.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from sbc.domain.identifiers import normalize_mpn
from sbc.integrations import llm
from sbc.integrations.usage import BudgetExceeded, check_budget, record_usage
from sbc.matching import calibration, identity
from sbc.matching.engine import ENGINE_VERSION, Decision, ItemView, compare, decide
from sbc.models import (
    AsinMapping,
    FeeProfile,
    ListingGtin,
    MarketplaceListing,
    MatchClass,
    MatchingResult,
    Opportunity,
    ProfitCalculation,
    ReviewStatus,
    SalesEstimate,
    Supplier,
    SupplierOffer,
)
from sbc.profit.engine import ENGINE_VERSION as PROFIT_VERSION
from sbc.profit.engine import (
    SCENARIO_LABELS,
    CostInputs,
    PricePoints,
    calculate,
    capital_metrics,
    max_buy_price,
    run_scenarios,
)
from sbc.profit.fees import get_profile
from sbc.scoring.engine import ScoreInputs, estimate_demand, score
from sbc.settings_store import get_setting

log = logging.getLogger(__name__)
D0 = Decimal(0)


def offer_view(o: SupplierOffer) -> ItemView:
    return ItemView.build(o.title, o.brand, [o.gtin] if o.gtin else [], o.mpn, None,
                          o.unit_quantity if o.unit_quantity > 1 else None, o.condition,
                          image_urls=o.image_urls)


def listing_view(l: MarketplaceListing) -> ItemView:
    qty = l.number_of_items or l.package_quantity
    return ItemView.build(l.title, l.brand, l.gtins or [], l.mpn, l.model,
                          qty if qty and qty > 1 else None, "new", image_urls=l.image_urls)


def candidate_listings(db: Session, offer: SupplierOffer, country: str,
                       include_test: bool = False) -> list[MarketplaceListing]:
    q = select(MarketplaceListing).where(MarketplaceListing.org_id == offer.org_id,
                                         MarketplaceListing.country == country)
    if not include_test:
        q = q.where(MarketplaceListing.is_test_data.is_(False))
    found: dict = {}
    if offer.gtin:
        for l in db.scalars(q.join(ListingGtin, ListingGtin.listing_id == MarketplaceListing.id)
                            .where(ListingGtin.gtin == offer.gtin)):
            found[l.id] = l
    mpn = normalize_mpn(offer.mpn)
    if mpn and len(mpn) >= 4:
        for l in db.scalars(q.where(MarketplaceListing.mpn.is_not(None)).limit(500)):
            if normalize_mpn(l.mpn) == mpn:
                found[l.id] = l
    return list(found.values())


def _mapping(db: Session, product_id, listing_id) -> AsinMapping | None:
    if product_id is None:
        return None
    return db.scalar(select(AsinMapping).where(AsinMapping.product_id == product_id,
                                               AsinMapping.listing_id == listing_id))


def upsert_mapping(db: Session, mr: MatchingResult, status: str, source: str, user_id=None,
                   note: str | None = None) -> AsinMapping | None:
    """Dauerhafte Zuordnung Produkt ↔ ASIN schreiben (VERIFIED | REJECTED | REVIEW)."""
    offer = mr.supplier_offer
    if offer.product_id is None:
        return None
    m = _mapping(db, offer.product_id, mr.listing_id)
    if m is None:
        m = AsinMapping(org_id=mr.org_id, product_id=offer.product_id, listing_id=mr.listing_id,
                        status=status, relation=mr.classification.value, source=source)
        db.add(m)
    m.status = status
    m.relation = mr.classification.value
    m.units_required = mr.units_required
    m.identity_attrs = offer.identity_attrs or {}
    m.identity_fingerprint = offer.identity_fingerprint
    m.source = source
    m.matching_result_id = mr.id
    m.decided_by = user_id
    m.decided_at = datetime.now(UTC)
    m.note = note
    db.flush()
    return m


def match_offer(db: Session, offer: SupplierOffer, listings: list[MarketplaceListing]) -> list[MatchingResult]:
    """Matching je Kandidat in Stufen: 1 bekannte Zuordnung → 2–4 Regeln (Identifikatoren, Marke/Modell,
    Titel/Attribute). Stufe 5/6 (Bild/KI) folgt in ``maybe_ai_review`` nur bei Bedarf."""
    thresholds = get_setting(db, offer.org_id, "thresholds")
    min_conf = Decimal(str(thresholds["min_match_confidence"]))
    auto_min = int(thresholds.get("auto_verify_min_score", 95))
    identity_diff, identity_changed = identity.refresh(offer)
    results = []
    sv = offer_view(offer)
    for listing in listings:
        outcome = compare(sv, listing_view(listing))
        mr = db.scalar(select(MatchingResult).where(MatchingResult.supplier_offer_id == offer.id,
                                                    MatchingResult.listing_id == listing.id))
        manual = mr is not None and mr.review_status in (ReviewStatus.confirmed, ReviewStatus.rejected)
        if manual and not identity_changed:
            results.append(mr)  # manuelle Entscheidungen werden nicht überschrieben
            continue
        if mr is None:
            mr = MatchingResult(org_id=offer.org_id, supplier_offer_id=offer.id, listing_id=listing.id,
                                engine_version=ENGINE_VERSION, score=D0, confidence=D0,
                                classification=outcome.classification, stage=outcome.stage)
            db.add(mr)
        mr.score = outcome.score
        mr.score_breakdown = outcome.breakdown
        mr.matched_attributes = outcome.matched
        mr.mismatched_attributes = outcome.mismatched
        mr.sources = [f"supplier_offer:{offer.id}", f"{listing.source}:{listing.external_id}"]
        mr.engine_version = ENGINE_VERSION
        mr.model_version = None
        mr.reviewed_by = None
        mr.reviewed_at = None
        mapping = _mapping(db, offer.product_id, listing.id)
        mapping_conflicts = identity.conflicts(mapping.identity_attrs or {}, offer.identity_attrs or {}) if mapping else []

        if identity_changed:
            # Stufe 1 entfällt: Identitätsmerkmale des Angebots haben sich geändert → Neuprüfung
            mr.classification = outcome.classification
            mr.units_required = outcome.units_required
            mr.confidence, mr.calibrated = outcome.score, False
            mr.stage = outcome.stage
            mr.identity_conflicts = sorted({*outcome.conflicts, "identity_changed"})
            mr.reasoning = f"Identitätsmerkmale geändert ({', '.join(identity_diff)}) – erneute Prüfung nötig; {outcome.reasoning}"
            mr.decision_status = Decision.NO_MATCH if outcome.classification == MatchClass.NO_MATCH else Decision.REVIEW
            mr.review_status = ReviewStatus.pending if mr.decision_status == Decision.REVIEW else ReviewStatus.auto_accepted
            if mapping is not None and mapping.status != "REJECTED":
                mapping.status = "REVIEW"
                mapping.note = "Identitätsmerkmale eines Lieferantenangebots geändert"
            mr._needs_ai = outcome.needs_ai  # type: ignore[attr-defined]
        elif mapping is not None and mapping.status in ("VERIFIED", "REJECTED") and not mapping_conflicts:
            # Stufe 1: bekannte Zuordnung wiederverwenden (kein KI-Aufruf, keine Neuprüfung)
            verified = mapping.status == "VERIFIED"
            mr.classification = MatchClass(mapping.relation) if verified else MatchClass.NO_MATCH
            mr.units_required = mapping.units_required if verified else outcome.units_required
            mr.confidence, mr.calibrated = Decimal("1") if verified else D0, False
            mr.stage = "known_mapping"
            mr.identity_conflicts = []
            when = f", {mapping.decided_at:%d.%m.%Y}" if mapping.decided_at else ""
            mr.reasoning = ("Bekannte Zuordnung wiederverwendet" if verified else "Zuordnung früher abgelehnt") + \
                f" ({mapping.source}{when})"
            mr.decision_status = Decision.VERIFIED if verified else Decision.NO_MATCH
            mr.review_status = ReviewStatus.auto_accepted
            mr._needs_ai = False  # type: ignore[attr-defined]
        else:
            # Stufen 2–4: Regeln
            bins = calibration.build_bins(calibration.load_labels(db, offer.org_id, outcome.classification.value))
            conf, calibrated = calibration.calibrate(outcome.score, bins)
            mr.classification = outcome.classification
            mr.units_required = outcome.units_required
            mr.confidence, mr.calibrated = conf, calibrated
            mr.stage = outcome.stage
            mr.identity_conflicts = sorted({*outcome.conflicts, *(f"mapping:{c}" for c in mapping_conflicts)})
            mr.reasoning = outcome.reasoning
            decision = decide(outcome, conf, calibrated, min_conf, auto_min)
            if mapping_conflicts and decision == Decision.VERIFIED:
                decision = Decision.REVIEW  # widerspricht einer bestehenden Zuordnung
            mr.decision_status = decision
            mr.review_status = ReviewStatus.pending if decision == Decision.REVIEW else ReviewStatus.auto_accepted
            db.flush()
            if decision == Decision.VERIFIED:
                upsert_mapping(db, mr, "VERIFIED", "auto")
            mr._needs_ai = outcome.needs_ai  # type: ignore[attr-defined]
        db.flush()
        mr._outcome = outcome  # type: ignore[attr-defined]
        results.append(mr)
    return results


def maybe_ai_review(db: Session, mr: MatchingResult, use_images: bool = True) -> bool:
    """Level 3 nur bei Bedarf und Budget. Gibt True zurück, wenn KI gelaufen ist."""
    outcome = getattr(mr, "_outcome", None)
    if outcome is None or not getattr(mr, "_needs_ai", False) or not llm.llm_available():
        return False
    from sbc.matching.ai import ai_review

    try:
        check_budget(db, mr.org_id, "anthropic", planned=0)
    except BudgetExceeded as e:
        log.info("KI übersprungen: %s", e)
        return False
    o, l = mr.supplier_offer, mr.listing
    supplier = {"title": o.title, "brand": o.brand, "gtin": o.gtin, "mpn": o.mpn,
                "unit_quantity": o.unit_quantity, "condition": o.condition, "image_urls": o.image_urls}
    listing = {"title": l.title, "brand": l.brand, "gtins": l.gtins, "mpn": l.mpn, "model": l.model,
               "package_quantity": l.package_quantity, "number_of_items": l.number_of_items,
               "category": l.category, "image_urls": l.image_urls}
    try:
        new, meta = ai_review(llm.get_provider(), supplier, listing, outcome, use_images)
    except Exception as e:  # KI-Ausfall darf die Pipeline nicht stoppen
        log.warning("KI-Prüfung fehlgeschlagen: %s", e)
        record_usage(db, org_id=mr.org_id, provider="anthropic", endpoint="match_review", units=0,
                     unit_type="llm_tokens", status_code=500, meta={"error": str(e)[:300]})
        return False
    record_usage(db, org_id=mr.org_id, provider="anthropic", endpoint="match_review",
                 units=meta["input_tokens"] + meta["output_tokens"], unit_type="llm_tokens",
                 cost_eur=Decimal(meta["cost_eur"]), status_code=200, duration_ms=meta["duration_ms"],
                 meta={"model": meta["model"], "flags": meta["flags"]})
    mr.classification = new.classification
    mr.score = new.score
    mr.confidence = new.score
    mr.calibrated = False
    mr.units_required = new.units_required
    mr.stage = new.stage
    mr.matched_attributes = new.matched
    mr.mismatched_attributes = {**new.mismatched, **({"flags": meta["flags"]} if meta["flags"] else {})}
    mr.reasoning = new.reasoning
    mr.model_version = meta["model"]
    mr.review_status = ReviewStatus.pending  # KI-Ergebnisse immer manuell bestätigen
    mr.decision_status = Decision.NO_MATCH if new.classification == MatchClass.NO_MATCH else Decision.REVIEW
    db.flush()
    return True


# --------------------------------------------------------------------------- Kalkulation


def build_inputs(offer: SupplierOffer, supplier: Supplier, profile: FeeProfile | None,
                 units_required: int | None, sell_price: Decimal | None, fx_rate: Decimal) -> CostInputs:
    ship_unit = supplier.shipping_cost_per_unit
    if ship_unit is None and supplier.incoterm and supplier.incoterm.upper() in {"DDP", "DAP", "CPT", "CIP"}:
        ship_unit = D0  # frei Haus laut Incoterm
    p = profile
    return CostInputs(
        sell_price_gross=sell_price,
        vat_rate=p.vat_rate if p else None,
        unit_cost_net=offer.unit_price_net,
        units_required=units_required,
        fx_rate=fx_rate,
        supplier_discount_rate=D0,
        inbound_shipping_per_unit=ship_unit,
        import_duty_rate=supplier.import_duty_rate if supplier.import_duty_rate is not None else D0,
        referral_rate=p.referral_rate if p else None,
        referral_min=p.referral_min if p else D0,
        fixed_fee=p.fixed_fee if p else D0,
        fulfillment_fee=p.fulfillment_fee if p else None,
        storage_cost_per_month=p.storage_cost_per_month if p else None,
        packaging_cost=p.packaging_cost if p else None,
        bundling_cost=Decimal("0.50") if units_required and units_required > 1 else D0,
        payment_fee_rate=p.payment_fee_rate if p else D0,
        return_rate=p.return_rate if p else None,
        return_cost=p.return_cost if p else None,
        ad_cost_rate=p.ad_cost_rate if p else None,
        other_cost=p.other_cost if p else D0,
    )


def fx_rate_for(db: Session, org_id, currency: str) -> Decimal | None:
    if currency == "EUR":
        return Decimal(1)
    fx = get_setting(db, org_id, "fx_rates")
    rate = (fx.get("rates") or {}).get(currency)
    # ECB-Kurse sind "1 EUR = x Fremdwährung" → Umrechnung Fremdwährung→EUR = 1/x
    return (Decimal(1) / Decimal(str(rate))) if rate else None


def evaluate(db: Session, mr: MatchingResult, channel: str | None = None) -> Opportunity | None:
    if mr.classification == MatchClass.NO_MATCH or mr.review_status == ReviewStatus.rejected:
        return None
    offer, listing = mr.supplier_offer, mr.listing
    supplier = db.get(Supplier, offer.supplier_id)
    assert supplier is not None
    analysis = get_setting(db, offer.org_id, "analysis")
    channel = channel or ("EBAY" if listing.marketplace == "ebay" else analysis.get("default_channel", "FBA"))
    profile = get_profile(db, offer.org_id, listing.marketplace, listing.country, channel)
    fx = fx_rate_for(db, offer.org_id, offer.currency)
    sell = listing.buybox_price or listing.new_price
    base = build_inputs(offer, supplier, profile, mr.units_required, sell, fx or Decimal(1))
    if fx is None:
        base = replace(base, unit_cost_net=None)  # kein Kurs → keine Scheinberechnung

    thresholds = get_setting(db, offer.org_id, "thresholds")
    purchasing = get_setting(db, offer.org_id, "purchasing")
    pp = PricePoints(current=sell, avg30=listing.avg30_price, avg90=listing.avg90_price, min90=listing.min90_price)
    scenarios = run_scenarios(base, pp)
    main = scenarios.get("current") or calculate(base)

    demand = estimate_demand(listing.monthly_sold, listing.sales_rank_drops30, listing.sales_rank_drops90,
                             listing.offer_count_new, Decimal(str(purchasing["own_share_of_market"])))
    db.add(SalesEstimate(org_id=offer.org_id, listing_id=listing.id, method=demand.method,
                         source=demand.source, market_units=demand.market_units_month,
                         own_units=demand.own_units_month, low=demand.low, high=demand.high,
                         confidence=demand.confidence, details={"notes": demand.notes}))
    test_qty = 0
    cap = None
    if main.result is not None and demand.own_units_month:
        months = Decimal(str(purchasing["test_order_months"]))
        test_qty = max(int(demand.own_units_month * months), 1)
        max_test = Decimal(str(thresholds["max_test_purchase"]))
        if main.result.goods_cost > 0:
            test_qty = min(test_qty, int(max_test / main.result.goods_cost))
        cap = capital_metrics(main, max(test_qty, 1), demand.own_units_month, offer.lead_time_days)

    calc_row = None
    for name, calc in scenarios.items():
        if calc is None:
            continue
        mbp = max_buy_price(calc.inputs, Decimal(str(thresholds["min_margin"])), Decimal(str(thresholds["min_contribution"])))
        row = ProfitCalculation(
            org_id=offer.org_id, supplier_offer_id=offer.id, listing_id=listing.id,
            matching_result_id=mr.id, channel=channel, scenario=name,
            inputs={**calc.inputs.snapshot(), "fee_profile_id": str(profile.id) if profile else None,
                    "fee_profile_is_assumption": profile.is_assumption if profile else None,
                    "listing_fetched_at": listing.fetched_at.isoformat() if listing.fetched_at else None,
                    "scenario_label": SCENARIO_LABELS[name]},
            outputs={**(calc.result.to_json() if calc.result else {}),
                     "max_buy_price_net": str(mbp) if mbp is not None else None,
                     **({"capital": cap.to_json()} if cap and name == "current" else {})},
            missing=calc.missing, is_complete=calc.is_complete, engine_version=PROFIT_VERSION,
        )
        db.add(row)
        if name == "current":
            calc_row = row
    db.flush()

    r = main.result
    s = score(ScoreInputs(
        contribution=r.contribution if r else None, margin=r.contribution_margin if r else None,
        roi=r.roi if r else None, market_units=demand.market_units_month,
        offer_count=listing.offer_count_new, amazon_is_seller=listing.amazon_is_seller,
        price_cv90=listing.price_cv90,
        contribution_per_1000_month=cap.contribution_per_1000_per_month if cap else None,
        stock_qty=offer.stock_qty, moq=offer.moq, lead_time_days=offer.lead_time_days,
        supplier_rating=supplier.rating, match_class=mr.classification, match_confidence=mr.confidence,
        match_confirmed=mr.decision_status == Decision.VERIFIED, units_required=mr.units_required,
        condition=offer.condition, restricted=bool(offer.product and offer.product.restricted),
        calc_complete=main.is_complete,
    ), get_setting(db, offer.org_id, "score_weights"), thresholds)

    opp = db.scalar(select(Opportunity).where(Opportunity.supplier_offer_id == offer.id,
                                              Opportunity.listing_id == listing.id,
                                              Opportunity.channel == channel))
    if opp is None:
        opp = Opportunity(org_id=offer.org_id, supplier_offer_id=offer.id, listing_id=listing.id, channel=channel)
        db.add(opp)
    opp.matching_result_id = mr.id
    opp.calculation_id = calc_row.id if calc_row else None
    opp.score = Decimal(str(s["score"]))
    opp.score_breakdown = {**s, "missing_costs": main.missing, "demand": {
        "method": demand.method, "source": demand.source, "market_units_month": str(demand.market_units_month) if demand.market_units_month is not None else None,
        "own_units_month": str(demand.own_units_month) if demand.own_units_month is not None else None,
        "confidence": str(demand.confidence), "notes": demand.notes}, "test_quantity": test_qty}
    opp.exclusion_reasons = s["exclusion_reasons"]
    opp.is_excluded = bool(s["exclusion_reasons"])
    opp.buy_price_net = offer.unit_price_net
    opp.sell_price_gross = sell
    opp.contribution = r.contribution if r else None
    opp.contribution_margin = r.contribution_margin if r else None
    opp.roi = r.roi if r else None
    opp.demand_score = Decimal(str(s["components"]["demand"])) if s["components"]["demand"] is not None else None
    opp.competition_score = (Decimal(str(s["components"]["stability_competition"]))
                             if s["components"]["stability_competition"] is not None else None)
    opp.match_confidence = mr.confidence
    opp.is_test_data = listing.is_test_data
    opp.updated_at = datetime.now(UTC)
    db.flush()
    return opp


def analyze_offer(db: Session, offer: SupplierOffer, *, use_keepa: bool = True, country: str = "DE",
                  include_test: bool = False) -> dict:
    """Vollständige Analyse eines Angebots. Gibt eine Zusammenfassung zurück."""
    summary = {"offer_id": str(offer.id), "keepa": "skipped", "matches": 0, "ai_reviews": 0, "opportunities": 0}
    if use_keepa and offer.gtin:
        from sbc.config import get_settings
        from sbc.integrations import keepa_service
        from sbc.integrations.keepa import KeepaError

        if get_settings().keepa_api_key:
            try:
                keepa_service.lookup_codes(db, offer.org_id, [offer.gtin], country)
                summary["keepa"] = "ok"
            except BudgetExceeded as e:
                summary["keepa"] = f"budget: {e}"
            except KeepaError as e:
                summary["keepa"] = f"error: {e}"
                if e.__class__.__name__ == "KeepaTokensExhausted":
                    raise
        else:
            summary["keepa"] = "not_configured"
    listings = candidate_listings(db, offer, country, include_test=include_test)
    results = match_offer(db, offer, listings)
    summary["matches"] = len(results)
    analysis = get_setting(db, offer.org_id, "analysis")
    min_potential = Decimal(str(analysis["ai_min_potential_contribution"]))
    for mr in results:
        # Level 1: Grobkalkulation entscheidet, ob Level 3 (KI) lohnt
        opp = evaluate(db, mr)
        if getattr(mr, "_needs_ai", False) and opp is not None and (opp.contribution or D0) >= min_potential:
            if maybe_ai_review(db, mr):
                summary["ai_reviews"] += 1
                opp = evaluate(db, mr)
        if opp is not None:
            summary["opportunities"] += 1
    db.commit()
    return summary
