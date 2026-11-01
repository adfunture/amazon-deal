"""Manuelle Matching-Entscheidungen – eine Stelle für Einzel-, Artikel- und Massenprüfung.

Jede Entscheidung:
* setzt Review- und Entscheidungsstatus,
* schreibt ein Feedback-Label (Grundlage der Konfidenz-Kalibrierung),
* aktualisiert die dauerhafte Zuordnung Produkt ↔ ASIN (gilt für alle Lieferanten),
* legt bei Multipack/Bundle/Variante Produktbeziehungen bzw. Bundle-Komponenten an,
* löst die Neukalkulation der Opportunity aus (bzw. schließt sie bei Ablehnung aus).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from sbc.matching.engine import Decision
from sbc.models import (
    BundleComponent,
    MatchClass,
    MatchingFeedback,
    MatchingResult,
    Opportunity,
    Product,
    ProductRelation,
    RelationType,
    ReviewStatus,
)

ACTIONS = ("confirm", "reject", "bundle", "variant", "defer", "correct")

_REL = {MatchClass.EXACT_MATCH: RelationType.SAME, MatchClass.MULTIPACK: RelationType.MULTIPACK_OF,
        MatchClass.VARIANT_MATCH: RelationType.VARIANT, MatchClass.BUNDLE: RelationType.BUNDLE_CONTAINS,
        MatchClass.PACKAGING_DIFFERENCE: RelationType.PACKAGING_VARIANT}


def apply_review(db: Session, m: MatchingResult, action: str, user_id, *, classification: MatchClass | None = None,
                 units_required: int | None = None, comment: str | None = None,
                 corrections: dict | None = None) -> Opportunity | None:
    from sbc.pipeline import evaluate, upsert_mapping

    if action not in ACTIONS:
        raise ValueError(f"Unbekannte Aktion {action}")
    prev_cls, prev_score = m.classification, m.score
    new_cls = classification or m.classification
    positive: bool | None
    if action == "confirm":
        status, positive = ReviewStatus.confirmed, True
    elif action == "reject":
        status, positive, new_cls = ReviewStatus.rejected, False, MatchClass.NO_MATCH
    elif action == "bundle":
        status, positive, new_cls = ReviewStatus.confirmed, prev_cls == MatchClass.BUNDLE, MatchClass.BUNDLE
    elif action == "variant":
        status, positive, new_cls = ReviewStatus.confirmed, prev_cls == MatchClass.VARIANT_MATCH, MatchClass.VARIANT_MATCH
    elif action == "defer":
        status, positive = ReviewStatus.deferred, None
    else:  # correct
        status, positive = ReviewStatus.confirmed, new_cls == prev_cls
    if units_required is not None:
        if units_required != m.units_required and positive:
            positive = False  # Verkaufseinheit war falsch → kein korrektes Match-Label
        m.units_required = units_required
    if status == ReviewStatus.confirmed and new_cls in (MatchClass.REVIEW_REQUIRED, MatchClass.POSSIBLE_MATCH):
        new_cls = MatchClass.MULTIPACK if (m.units_required or 1) > 1 else MatchClass.EXACT_MATCH
    if status == ReviewStatus.confirmed and new_cls == MatchClass.EXACT_MATCH and (m.units_required or 1) > 1:
        new_cls = MatchClass.MULTIPACK

    m.classification = new_cls
    m.review_status = status
    m.reviewed_by = user_id
    m.reviewed_at = datetime.now(UTC)
    if status == ReviewStatus.confirmed:
        m.confidence = max(m.confidence, m.score)
    m.decision_status = {ReviewStatus.confirmed: Decision.VERIFIED, ReviewStatus.rejected: Decision.NO_MATCH}.get(
        status, Decision.REVIEW)
    db.flush()
    upsert_mapping(db, m, {Decision.VERIFIED: "VERIFIED", Decision.NO_MATCH: "REJECTED"}.get(m.decision_status, "REVIEW"),
                   "manual", user_id, comment)
    db.add(MatchingFeedback(org_id=m.org_id, matching_result_id=m.id, user_id=user_id, action=action,
                            previous_classification=prev_cls.value, new_classification=new_cls.value,
                            score_at_review=prev_score, is_positive=positive, corrections=corrections or {},
                            comment=comment))

    o, l = m.supplier_offer, m.listing
    rel = _REL.get(new_cls)
    if status == ReviewStatus.confirmed and o.product_id and rel:
        if new_cls == MatchClass.EXACT_MATCH:
            if l.product_id is None:
                l.product_id = o.product_id
        else:
            if l.product_id is None:
                lp = Product(org_id=m.org_id, title=(l.title or l.external_id)[:500], brand=l.brand, mpn=l.mpn,
                             unit_quantity=(l.number_of_items or l.package_quantity or 1),
                             is_bundle=new_cls == MatchClass.BUNDLE)
                db.add(lp)
                db.flush()
                l.product_id = lp.id
            db.add(ProductRelation(org_id=m.org_id, from_product_id=l.product_id, to_product_id=o.product_id,
                                   relation_type=rel, quantity=m.units_required,
                                   evidence={"matching_result_id": str(m.id), "reasoning": m.reasoning},
                                   confidence=m.confidence, status="confirmed", created_by=user_id))
            if new_cls in (MatchClass.MULTIPACK, MatchClass.BUNDLE):
                db.add(BundleComponent(bundle_product_id=l.product_id, component_product_id=o.product_id,
                                       quantity=m.units_required or 1, preferred_supplier_id=o.supplier_id,
                                       confirmed=new_cls == MatchClass.MULTIPACK))
    db.flush()
    if status == ReviewStatus.rejected:
        for opp in db.scalars(select(Opportunity).where(Opportunity.matching_result_id == m.id)):
            opp.is_excluded = True
            opp.exclusion_reasons = ["Match manuell abgelehnt"]
        return None
    return evaluate(db, m)


def bulk_confirm_blockers(m: MatchingResult) -> str | None:
    """Massenfreigabe nur für eindeutige Fälle: exakter Treffer, 1:1, ohne Konflikte, noch offen."""
    if m.decision_status != Decision.REVIEW:
        return "nicht offen"
    if m.classification != MatchClass.EXACT_MATCH:
        return f"{m.classification.value} braucht Einzelprüfung"
    if m.identity_conflicts:
        return "Identitätskonflikt: " + ", ".join(m.identity_conflicts)
    if (m.units_required or 1) != 1:
        return "Verkaufseinheit ≠ 1"
    return None
