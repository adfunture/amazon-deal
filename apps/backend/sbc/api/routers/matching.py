from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sbc.api.deps import CurrentUser, audit, current_user, require
from sbc.api.serialize import row
from sbc.db import get_db
from sbc.matching import calibration
from sbc.models import (
    BundleComponent,
    MatchClass,
    MatchingFeedback,
    MatchingResult,
    ProductRelation,
    RelationType,
    ReviewStatus,
    Role,
)
from sbc.pipeline import evaluate

router = APIRouter(prefix="/matching", tags=["matching"])


def _result(db: Session, user: CurrentUser, mid: uuid.UUID) -> MatchingResult:
    m = db.get(MatchingResult, mid)
    if m is None or m.org_id != user.org_id:
        raise HTTPException(404, "Matching-Ergebnis nicht gefunden")
    return m


def _detail(m: MatchingResult) -> dict:
    o, l = m.supplier_offer, m.listing
    return {
        **row(m),
        "supplier_offer": {**row(o, {"raw"}), "supplier_name": o.supplier.name,
                           "description": o.product.description if o.product else None},
        "listing": row(l, {"raw"}),
    }


@router.get("/queue")
def queue(status: ReviewStatus = ReviewStatus.pending, classification: MatchClass | None = None,
          offset: int = 0, limit: int = 50, user: CurrentUser = Depends(current_user),
          db: Session = Depends(get_db)):
    q = select(MatchingResult).where(MatchingResult.org_id == user.org_id,
                                     MatchingResult.review_status == status)
    if classification:
        q = q.where(MatchingResult.classification == classification)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    items = db.scalars(q.order_by(MatchingResult.score.desc()).offset(offset).limit(min(limit, 200)))
    return {"total": total, "items": [_detail(m) for m in items]}


@router.get("/{mid}")
def get_match(mid: uuid.UUID, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    return _detail(_result(db, user, mid))


class ReviewIn(BaseModel):
    action: str = Field(pattern="^(confirm|reject|bundle|variant|defer|correct)$")
    classification: MatchClass | None = None
    units_required: int | None = Field(default=None, ge=1, le=1000)
    corrections: dict = Field(default_factory=dict)
    bundle_components: list[dict] = Field(default_factory=list)
    comment: str | None = Field(default=None, max_length=2000)


@router.post("/{mid}/review")
def review(mid: uuid.UUID, body: ReviewIn, request: Request,
           user: CurrentUser = Depends(require(Role.reviewer)), db: Session = Depends(get_db)):
    """Jede Entscheidung wird als Feedback gespeichert (Label für die Konfidenz-Kalibrierung)."""
    m = _result(db, user, mid)
    prev_cls, prev_score = m.classification, m.score
    new_cls = body.classification or m.classification
    positive: bool | None
    if body.action == "confirm":
        status, positive = ReviewStatus.confirmed, True
    elif body.action == "reject":
        status, positive, new_cls = ReviewStatus.rejected, False, MatchClass.NO_MATCH
    elif body.action == "bundle":
        status, positive, new_cls = ReviewStatus.confirmed, prev_cls == MatchClass.BUNDLE, MatchClass.BUNDLE
    elif body.action == "variant":
        status, positive, new_cls = ReviewStatus.confirmed, prev_cls == MatchClass.VARIANT_MATCH, MatchClass.VARIANT_MATCH
    elif body.action == "defer":
        status, positive = ReviewStatus.deferred, None
    else:  # correct
        status, positive = ReviewStatus.confirmed, new_cls == prev_cls
    if body.units_required is not None:
        if body.units_required != m.units_required and positive:
            positive = False  # Verkaufseinheit war falsch → kein korrektes Match-Label
        m.units_required = body.units_required
    if status == ReviewStatus.confirmed and new_cls in (MatchClass.REVIEW_REQUIRED, MatchClass.POSSIBLE_MATCH):
        new_cls = MatchClass.MULTIPACK if (m.units_required or 1) > 1 else MatchClass.EXACT_MATCH

    m.classification = new_cls
    m.review_status = status
    m.reviewed_by = user.id
    m.reviewed_at = datetime.now(UTC)
    if status == ReviewStatus.confirmed:
        m.confidence = max(m.confidence, m.score)
    db.add(MatchingFeedback(org_id=user.org_id, matching_result_id=m.id, user_id=user.id,
                            action=body.action, previous_classification=prev_cls.value,
                            new_classification=new_cls.value, score_at_review=prev_score,
                            is_positive=positive, corrections=body.corrections, comment=body.comment))

    o, l = m.supplier_offer, m.listing
    if status == ReviewStatus.confirmed and o.product_id:
        rel = {MatchClass.EXACT_MATCH: RelationType.SAME, MatchClass.MULTIPACK: RelationType.MULTIPACK_OF,
               MatchClass.VARIANT_MATCH: RelationType.VARIANT, MatchClass.BUNDLE: RelationType.BUNDLE_CONTAINS,
               MatchClass.PACKAGING_DIFFERENCE: RelationType.PACKAGING_VARIANT}.get(new_cls)
        if rel and new_cls in (MatchClass.EXACT_MATCH,) and l.product_id is None:
            l.product_id = o.product_id
        if rel and new_cls != MatchClass.EXACT_MATCH:
            from sbc.models import Product

            if l.product_id is None:
                lp = Product(org_id=user.org_id, title=l.title or l.external_id, brand=l.brand, mpn=l.mpn,
                             unit_quantity=(l.number_of_items or l.package_quantity or 1),
                             is_bundle=new_cls == MatchClass.BUNDLE)
                db.add(lp)
                db.flush()
                l.product_id = lp.id
            db.add(ProductRelation(org_id=user.org_id, from_product_id=l.product_id, to_product_id=o.product_id,
                                   relation_type=rel, quantity=m.units_required,
                                   evidence={"matching_result_id": str(m.id), "reasoning": m.reasoning},
                                   confidence=m.confidence, status="confirmed", created_by=user.id))
            if new_cls in (MatchClass.MULTIPACK, MatchClass.BUNDLE):
                db.add(BundleComponent(bundle_product_id=l.product_id, component_product_id=o.product_id,
                                       quantity=m.units_required or 1, preferred_supplier_id=o.supplier_id,
                                       confirmed=new_cls == MatchClass.MULTIPACK))
    audit(db, user, "match_reviewed", "matching_result", m.id,
          {"action": body.action, "from": prev_cls.value, "to": new_cls.value}, request)
    db.flush()
    opp = evaluate(db, m) if status != ReviewStatus.rejected else None
    db.commit()
    return {**_detail(m), "opportunity_id": str(opp.id) if opp else None}


@router.get("/calibration/report")
def calibration_report(user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    out = {}
    for cls in MatchClass:
        labels = calibration.load_labels(db, user.org_id, cls.value)
        if labels:
            out[cls.value] = {"labels": len(labels),
                              "bins": [b.__dict__ for b in calibration.build_bins(labels)],
                              "min_samples_per_bin": calibration.MIN_SAMPLES}
    return out
