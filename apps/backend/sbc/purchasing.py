"""Einkaufsassistent: Test- und Nachbestellmengen vorschlagen. Keine automatische Bestellung.

Regeln:
* Bundles/Multipacks/Varianten nur mit bestätigtem Match (manuelle Freigabe).
* Testmenge = eigener Monatsabsatz × Testmonate, begrenzt durch max. Testeinkauf (EUR),
  Lieferantenbestand; aufgerundet auf MOQ (dann Hinweis, falls Budget überschritten).
* Nachbestellmenge = Absatz × (Lieferzeit + Reichweite) − aktueller Bestand (sofern bekannt).
"""

from __future__ import annotations

import math
from decimal import Decimal

from sqlalchemy.orm import Session

from sbc.models import MatchClass, Opportunity, PurchaseRecommendation
from sbc.settings_store import get_setting


def propose(db: Session, o: Opportunity, kind: str, quantity: int | None, thresholds: dict) -> PurchaseRecommendation:
    m = o.matching_result
    if m is None or m.classification == MatchClass.NO_MATCH:
        raise ValueError("Kein Match")
    if m.decision_status != "VERIFIED_MATCH":
        raise ValueError("Match ist nicht verifiziert bzw. nicht manuell bestätigt "
                         "(bei Bundles/Multipacks/Varianten Pflicht)")
    if o.contribution is None:
        raise ValueError("Kalkulation unvollständig")
    offer = o.supplier_offer
    if offer.unit_price_net is None:
        raise ValueError("Kein Einkaufspreis")
    purchasing = get_setting(db, o.org_id, "purchasing")
    breakdown = o.score_breakdown or {}
    own = breakdown.get("demand", {}).get("own_units_month")
    own_units = Decimal(own) if own else None
    units_per_sale = m.units_required or 1
    notes: list[str] = []

    if quantity is None:
        if own_units is None:
            raise ValueError("Keine Absatzschätzung – Menge bitte manuell angeben")
        if kind == "test":
            months = Decimal(str(purchasing["test_order_months"]))
            sales = math.ceil(own_units * months)
        else:
            lead = Decimal(offer.lead_time_days or 0) / 30
            cover = Decimal(str(purchasing["reorder_cover_months"]))
            sales = math.ceil(own_units * (lead + cover))
        quantity = max(sales * units_per_sale, 1)
    unit_cost = offer.unit_price_net
    if kind == "test":
        max_test = Decimal(str(thresholds["max_test_purchase"]))
        cap_qty = int(max_test / unit_cost) if unit_cost > 0 else quantity
        if quantity > cap_qty:
            notes.append(f"Auf max. Testeinkauf {max_test} € begrenzt")
            quantity = max(cap_qty, 1)
    if offer.stock_qty is not None and quantity > offer.stock_qty:
        notes.append(f"Auf Lieferantenbestand {offer.stock_qty} begrenzt")
        quantity = offer.stock_qty
    if quantity <= 0:
        raise ValueError("Lieferant hat keinen Bestand")
    if offer.moq and quantity < offer.moq:
        notes.append(f"Auf MOQ {offer.moq} angehoben")
        quantity = offer.moq
    if units_per_sale > 1 and quantity % units_per_sale:
        quantity += units_per_sale - quantity % units_per_sale
        notes.append(f"Auf Vielfaches von {units_per_sale} (Bundle-Menge) gerundet")
    total = (unit_cost * quantity).quantize(Decimal("0.01"))
    supplier = offer.supplier
    if supplier.min_order_value and total < supplier.min_order_value:
        notes.append(f"Mindestbestellwert {supplier.min_order_value} € beim Lieferanten nicht erreicht")
    rec = PurchaseRecommendation(
        org_id=o.org_id, opportunity_id=o.id, supplier_id=offer.supplier_id, kind=kind, quantity=quantity,
        unit_cost_net=unit_cost, total_cost_net=total, currency=offer.currency,
        rationale={"notes": notes, "own_units_month": own, "units_per_sale": units_per_sale,
                   "expected_contribution_per_sale": str(o.contribution),
                   "expected_total_contribution": str((o.contribution * Decimal(quantity // units_per_sale)).quantize(Decimal("0.01")))},
    )
    db.add(rec)
    db.flush()
    return rec
