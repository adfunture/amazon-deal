from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sbc.api.deps import CurrentUser, audit, current_user, require
from sbc.api.serialize import row
from sbc.db import get_db
from sbc.matching import calibration
from sbc.matching.engine import Decision
from sbc.matching.review import apply_review, bulk_confirm_blockers
from sbc.models import (
    MatchClass,
    MarketplaceListing,
    MatchingResult,
    ReviewStatus,
    Role,
    SupplierOffer,
)

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
          decision: str | None = None, offset: int = 0, limit: int = 50,
          user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    """Prüfwarteschlange. ``decision`` (VERIFIED_MATCH|MANUAL_REVIEW|NO_MATCH) hat Vorrang vor ``status``."""
    q = select(MatchingResult).where(MatchingResult.org_id == user.org_id)
    q = q.where(MatchingResult.decision_status == decision) if decision else q.where(MatchingResult.review_status == status)
    if classification:
        q = q.where(MatchingResult.classification == classification)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    items = db.scalars(q.order_by(MatchingResult.score.desc()).offset(offset).limit(min(limit, 200)))
    return {"total": total, "items": [_detail(m) for m in items]}


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
    prev_cls = m.classification
    opp = apply_review(db, m, body.action, user.id, classification=body.classification,
                       units_required=body.units_required, comment=body.comment, corrections=body.corrections)
    audit(db, user, "match_reviewed", "matching_result", m.id,
          {"action": body.action, "from": prev_cls.value, "to": m.classification.value}, request)
    db.commit()
    return {**_detail(m), "opportunity_id": str(opp.id) if opp else None}


# --------------------------------------------------------------------------- Prüfung je Lieferantenartikel


def _offer(db: Session, user: CurrentUser, oid: uuid.UUID) -> SupplierOffer:
    o = db.get(SupplierOffer, oid)
    if o is None or o.org_id != user.org_id:
        raise HTTPException(404, "Lieferantenartikel nicht gefunden")
    return o


def _offer_view(db: Session, o: SupplierOffer) -> dict:
    results = list(db.scalars(select(MatchingResult).where(MatchingResult.supplier_offer_id == o.id)
                              .order_by(MatchingResult.score.desc())))
    return {
        "offer": {**row(o, {"raw"}), "supplier_name": o.supplier.name,
                  "description": o.product.description if o.product else None},
        "candidates": [{**row(m), "listing": row(m.listing, {"raw"}),
                        "bulk_blocker": bulk_confirm_blockers(m)} for m in results],
    }


@router.get("/offers")
def offers_queue(decision: str = Decision.REVIEW, supplier_id: uuid.UUID | None = None, offset: int = 0,
                 limit: int = 20, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    """Lieferantenartikel mit mindestens einem Kandidaten im gewählten Entscheidungsstatus."""
    q = (select(MatchingResult.supplier_offer_id, func.max(MatchingResult.score).label("best"))
         .where(MatchingResult.org_id == user.org_id, MatchingResult.decision_status == decision)
         .group_by(MatchingResult.supplier_offer_id))
    if supplier_id:
        q = q.join(SupplierOffer, SupplierOffer.id == MatchingResult.supplier_offer_id).where(
            SupplierOffer.supplier_id == supplier_id)
    sub = q.subquery()
    total = db.scalar(select(func.count()).select_from(sub))
    ids = db.execute(select(sub.c.supplier_offer_id).order_by(sub.c.best.desc(), sub.c.supplier_offer_id)
                     .offset(offset).limit(min(limit, 100))).scalars().all()
    return {"total": total, "items": [_offer_view(db, db.get(SupplierOffer, i)) for i in ids]}


@router.get("/offers/{oid}")
def offer_detail(oid: uuid.UUID, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    return _offer_view(db, _offer(db, user, oid))


class ChooseIn(BaseModel):
    matching_result_id: uuid.UUID
    action: str = Field(default="confirm", pattern="^(confirm|variant|bundle)$")
    units_required: int | None = Field(default=None, ge=1, le=1000)
    comment: str | None = Field(default=None, max_length=2000)
    reject_others: bool = True


@router.post("/offers/{oid}/choose")
def choose_candidate(oid: uuid.UUID, body: ChooseIn, request: Request,
                     user: CurrentUser = Depends(require(Role.reviewer)), db: Session = Depends(get_db)):
    """Dieser passt: gewählten Kandidaten bestätigen, übrige offene Kandidaten ablehnen."""
    o = _offer(db, user, oid)
    chosen = _result(db, user, body.matching_result_id)
    if chosen.supplier_offer_id != o.id:
        raise HTTPException(422, "Kandidat gehört nicht zu diesem Lieferantenartikel")
    apply_review(db, chosen, body.action, user.id, units_required=body.units_required, comment=body.comment)
    rejected = 0
    if body.reject_others:
        for m in db.scalars(select(MatchingResult).where(MatchingResult.supplier_offer_id == o.id,
                                                         MatchingResult.id != chosen.id,
                                                         MatchingResult.decision_status == Decision.REVIEW)):
            apply_review(db, m, "reject", user.id, comment="Anderer Kandidat gewählt")
            rejected += 1
    audit(db, user, "match_chosen", "supplier_offer", o.id,
          {"chosen": str(chosen.id), "action": body.action, "rejected_others": rejected}, request)
    db.commit()
    return _offer_view(db, o)


class OfferActionIn(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)


@router.post("/offers/{oid}/none")
def reject_all(oid: uuid.UUID, body: OfferActionIn, request: Request,
               user: CurrentUser = Depends(require(Role.reviewer)), db: Session = Depends(get_db)):
    """Keiner passt: alle offenen Kandidaten ablehnen."""
    o = _offer(db, user, oid)
    n = 0
    for m in db.scalars(select(MatchingResult).where(MatchingResult.supplier_offer_id == o.id,
                                                     MatchingResult.decision_status == Decision.REVIEW)):
        apply_review(db, m, "reject", user.id, comment=body.comment or "Keiner der Kandidaten passt")
        n += 1
    audit(db, user, "match_none", "supplier_offer", o.id, {"rejected": n}, request)
    db.commit()
    return _offer_view(db, o)


@router.post("/offers/{oid}/defer")
def defer_offer(oid: uuid.UUID, body: OfferActionIn, user: CurrentUser = Depends(require(Role.reviewer)),
                db: Session = Depends(get_db)):
    o = _offer(db, user, oid)
    for m in db.scalars(select(MatchingResult).where(MatchingResult.supplier_offer_id == o.id,
                                                     MatchingResult.review_status == ReviewStatus.pending)):
        apply_review(db, m, "defer", user.id, comment=body.comment)
    db.commit()
    return _offer_view(db, o)


class AsinIn(BaseModel):
    asin: str = Field(min_length=10, max_length=10)
    country: str = Field(default="DE", min_length=2, max_length=2)


@router.post("/offers/{oid}/asin")
def add_asin(oid: uuid.UUID, body: AsinIn, request: Request, user: CurrentUser = Depends(require(Role.reviewer)),
             db: Session = Depends(get_db)):
    """Andere ASIN: Listing per Keepa holen (1 Token) und als Kandidat vergleichen – noch ohne Bestätigung."""
    from sbc.config import get_settings
    from sbc.domain.identifiers import is_asin
    from sbc.integrations import keepa as k
    from sbc.integrations.keepa_service import lookup_asins
    from sbc.integrations.usage import BudgetExceeded
    from sbc.pipeline import match_offer

    o = _offer(db, user, oid)
    asin = body.asin.strip().upper()
    if not is_asin(asin):
        raise HTTPException(422, "Ungültige ASIN")
    country = body.country.upper()
    listing = db.scalar(select(MarketplaceListing).where(
        MarketplaceListing.org_id == user.org_id, MarketplaceListing.marketplace == "amazon",
        MarketplaceListing.country == country, MarketplaceListing.external_id == asin))
    if listing is None:
        if not get_settings().keepa_api_key:
            raise HTTPException(503, "KEEPA_API_KEY nicht konfiguriert")
        try:
            found = lookup_asins(db, user.org_id, [asin], country)
        except (k.KeepaTokensExhausted, BudgetExceeded) as e:
            raise HTTPException(429, str(e)) from e
        except k.KeepaError as e:
            raise HTTPException(502, str(e)) from e
        listing = found.get(asin)
        if listing is None:
            raise HTTPException(404, f"ASIN {asin} bei Keepa nicht gefunden")
    match_offer(db, o, [listing])
    audit(db, user, "match_asin_added", "supplier_offer", o.id, {"asin": asin}, request)
    db.commit()
    return _offer_view(db, o)


class BulkIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


@router.post("/bulk-confirm")
def bulk_confirm(body: BulkIn, request: Request, user: CurrentUser = Depends(require(Role.reviewer)),
                 db: Session = Depends(get_db)):
    """Massenfreigabe – nur eindeutige exakte 1:1-Treffer ohne Konflikte; alles andere wird übersprungen."""
    confirmed, skipped = [], []
    for mid in body.ids:
        m = db.get(MatchingResult, mid)
        if m is None or m.org_id != user.org_id:
            skipped.append({"id": str(mid), "reason": "nicht gefunden"})
            continue
        blocker = bulk_confirm_blockers(m)
        if blocker:
            skipped.append({"id": str(mid), "reason": blocker})
            continue
        apply_review(db, m, "confirm", user.id, comment="Massenfreigabe")
        confirmed.append(str(mid))
    audit(db, user, "match_bulk_confirm", "matching_result", None,
          {"confirmed": len(confirmed), "skipped": len(skipped)}, request)
    db.commit()
    return {"confirmed": confirmed, "skipped": skipped}


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


# Muss zuletzt stehen: sonst würde "/offers" als {mid} interpretiert
@router.get("/{mid}")
def get_match(mid: uuid.UUID, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    return _detail(_result(db, user, mid))
