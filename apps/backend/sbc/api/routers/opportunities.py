from __future__ import annotations

import csv
import io
import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sbc.api.deps import CurrentUser, audit, current_user, require
from sbc.api.serialize import jsonable, row
from sbc.db import get_db
from sbc.models import (
    MarketplaceListing,
    MatchClass,
    MatchingResult,
    Opportunity,
    OpportunityStatus,
    PriceHistory,
    ProfitCalculation,
    PurchaseRecommendation,
    Role,
    Supplier,
    SupplierOffer,
)
from sbc.settings_store import get_setting

router = APIRouter(prefix="/opportunities", tags=["opportunities"])

SORTABLE = {
    "score": Opportunity.score, "contribution": Opportunity.contribution,
    "margin": Opportunity.contribution_margin, "roi": Opportunity.roi,
    "sell": Opportunity.sell_price_gross, "buy": Opportunity.buy_price_net,
    "demand": Opportunity.demand_score, "updated": Opportunity.updated_at,
    "confidence": Opportunity.match_confidence,
}


def _query(user: CurrentUser, *, include_excluded: bool, include_test: bool, status: list[str] | None,
           supplier_id: uuid.UUID | None, brand: str | None, category: str | None, marketplace: str | None,
           channel: str | None, min_margin: Decimal | None, min_contribution: Decimal | None,
           min_sell: Decimal | None, max_sell: Decimal | None, min_buy: Decimal | None,
           max_buy: Decimal | None, min_demand: Decimal | None, min_score: Decimal | None,
           match_class: list[MatchClass] | None, bundle_only: bool, q: str | None):
    stmt = (select(Opportunity).join(SupplierOffer, SupplierOffer.id == Opportunity.supplier_offer_id)
            .join(MarketplaceListing, MarketplaceListing.id == Opportunity.listing_id)
            .outerjoin(MatchingResult, MatchingResult.id == Opportunity.matching_result_id)
            .where(Opportunity.org_id == user.org_id))
    if not include_excluded:
        stmt = stmt.where(Opportunity.is_excluded.is_(False))
    if not include_test:
        stmt = stmt.where(Opportunity.is_test_data.is_(False))
    stmt = stmt.where(Opportunity.status.in_(status) if status else Opportunity.status != OpportunityStatus.ignored)
    filters = [
        (supplier_id, lambda v: SupplierOffer.supplier_id == v),
        (brand, lambda v: func.lower(SupplierOffer.brand) == v.lower()),
        (category, lambda v: MarketplaceListing.category.ilike(f"%{v}%")),
        (marketplace, lambda v: MarketplaceListing.marketplace == v),
        (channel, lambda v: Opportunity.channel == v),
        (min_margin, lambda v: Opportunity.contribution_margin >= v),
        (min_contribution, lambda v: Opportunity.contribution >= v),
        (min_sell, lambda v: Opportunity.sell_price_gross >= v),
        (max_sell, lambda v: Opportunity.sell_price_gross <= v),
        (min_buy, lambda v: Opportunity.buy_price_net >= v),
        (max_buy, lambda v: Opportunity.buy_price_net <= v),
        (min_demand, lambda v: Opportunity.demand_score >= v),
        (min_score, lambda v: Opportunity.score >= v),
        (match_class, lambda v: MatchingResult.classification.in_(v)),
    ]
    for value, f in filters:
        if value is not None and value != []:
            stmt = stmt.where(f(value))
    if bundle_only:
        stmt = stmt.where(MatchingResult.classification.in_([MatchClass.BUNDLE, MatchClass.MULTIPACK]))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(SupplierOffer.title.ilike(like) | (SupplierOffer.gtin == q.zfill(14))
                          | (MarketplaceListing.external_id == q.upper()) | SupplierOffer.supplier_sku.ilike(like))
    return stmt


def _summary(o: Opportunity) -> dict:
    off, l, m = o.supplier_offer, o.listing, o.matching_result
    return {
        "id": str(o.id), "status": o.status.value, "channel": o.channel, "score": jsonable(o.score),
        "is_excluded": o.is_excluded, "exclusion_reasons": o.exclusion_reasons, "is_test_data": o.is_test_data,
        "image": (l.image_urls or off.image_urls or [None])[0],
        "title": off.title or l.title, "listing_title": l.title, "brand": off.brand or l.brand,
        "gtin": off.gtin, "asin": l.external_id if l.marketplace == "amazon" else None,
        "marketplace": l.marketplace, "country": l.country,
        "supplier": off.supplier.name, "supplier_id": str(off.supplier_id),
        "buy_price_net": jsonable(o.buy_price_net), "currency": off.currency,
        "sell_price_gross": jsonable(o.sell_price_gross),
        "contribution": jsonable(o.contribution), "contribution_margin": jsonable(o.contribution_margin),
        "roi": jsonable(o.roi), "demand_score": jsonable(o.demand_score),
        "competition_score": jsonable(o.competition_score), "match_confidence": jsonable(o.match_confidence),
        "match_class": m.classification.value if m else None,
        "match_status": m.review_status.value if m else None,
        "units_required": m.units_required if m else None,
        "stock_qty": off.stock_qty, "coverage": (o.score_breakdown or {}).get("coverage"),
        "missing_costs": (o.score_breakdown or {}).get("missing_costs", []),
        "updated_at": jsonable(o.updated_at), "market_data_at": jsonable(l.fetched_at),
    }


def _params(
    include_excluded: bool = False, include_test: bool = False, status: list[str] | None = Query(None),
    supplier_id: uuid.UUID | None = None, brand: str | None = None, category: str | None = None,
    marketplace: str | None = None, channel: str | None = None, min_margin: Decimal | None = None,
    min_contribution: Decimal | None = None, min_sell: Decimal | None = None, max_sell: Decimal | None = None,
    min_buy: Decimal | None = None, max_buy: Decimal | None = None, min_demand: Decimal | None = None,
    min_score: Decimal | None = None, match_class: list[MatchClass] | None = Query(None),
    bundle_only: bool = False, q: str | None = None,
) -> dict:
    return locals()


@router.get("")
def list_opportunities(params: dict = Depends(_params), sort: str = "score",
                       order: Literal["asc", "desc"] = "desc", offset: int = 0, limit: int = 50,
                       user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    stmt = _query(user, **params)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    col = SORTABLE.get(sort, Opportunity.score)
    stmt = stmt.order_by(col.desc().nulls_last() if order == "desc" else col.asc().nulls_last())
    items = db.scalars(stmt.offset(offset).limit(min(limit, 500))).unique()
    return {"total": total, "items": [_summary(o) for o in items]}


EXPORT_COLS = ["title", "brand", "gtin", "asin", "supplier", "buy_price_net", "sell_price_gross",
               "contribution", "contribution_margin", "roi", "demand_score", "competition_score",
               "score", "match_class", "match_confidence", "units_required", "channel", "country",
               "stock_qty", "updated_at"]


@router.get("/export")
def export(params: dict = Depends(_params), fmt: Literal["csv", "xlsx"] = "csv",
           ids: list[uuid.UUID] | None = Query(None),
           user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    stmt = _query(user, **params)
    if ids:
        stmt = stmt.where(Opportunity.id.in_(ids))
    items = [_summary(o) for o in db.scalars(stmt.order_by(Opportunity.score.desc()).limit(20000)).unique()]
    if fmt == "xlsx":
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Opportunities"
        ws.append(EXPORT_COLS)
        for it in items:
            ws.append([_num(it.get(c)) for c in EXPORT_COLS])
        for cell in ws["C"][1:]:
            cell.number_format = "@"  # EAN als Text (führende Nullen)
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                 headers={"Content-Disposition": 'attachment; filename="opportunities.xlsx"'})
    sio = io.StringIO()
    w = csv.writer(sio, delimiter=";")
    w.writerow(EXPORT_COLS)
    for it in items:
        w.writerow([str(it.get(c)).replace(".", ",") if c in {"buy_price_net", "sell_price_gross", "contribution", "contribution_margin", "roi"} and it.get(c) is not None else (it.get(c) if it.get(c) is not None else "") for c in EXPORT_COLS])
    return StreamingResponse(iter(["﻿" + sio.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="opportunities.csv"'})


def _num(v):
    if isinstance(v, str):
        try:
            return float(Decimal(v)) if v.replace(".", "", 1).replace("-", "", 1).isdigit() else v
        except Exception:
            return v
    return v


@router.get("/{oid}")
def detail(oid: uuid.UUID, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    o = db.get(Opportunity, oid)
    if o is None or o.org_id != user.org_id:
        raise HTTPException(404, "Opportunity nicht gefunden")
    calcs = db.scalars(select(ProfitCalculation).where(
        ProfitCalculation.supplier_offer_id == o.supplier_offer_id, ProfitCalculation.listing_id == o.listing_id,
        ProfitCalculation.channel == o.channel).order_by(ProfitCalculation.created_at.desc()).limit(30))
    latest: dict[str, dict] = {}
    for c in calcs:
        latest.setdefault(c.scenario, row(c))
    history = db.scalars(select(PriceHistory).where(
        PriceHistory.subject_type == "listing", PriceHistory.subject_id == o.listing_id
    ).order_by(PriceHistory.observed_at).limit(2000))
    offer_hist = db.scalars(select(PriceHistory).where(
        PriceHistory.subject_type == "supplier_offer", PriceHistory.subject_id == o.supplier_offer_id
    ).order_by(PriceHistory.observed_at).limit(500))
    other_offers = db.scalars(select(SupplierOffer).where(
        SupplierOffer.org_id == user.org_id, SupplierOffer.gtin == o.supplier_offer.gtin,
        SupplierOffer.gtin.is_not(None), SupplierOffer.id != o.supplier_offer_id,
        SupplierOffer.is_current.is_(True))) if o.supplier_offer.gtin else []
    supplier = db.get(Supplier, o.supplier_offer.supplier_id)
    return {
        **_summary(o), "score_breakdown": o.score_breakdown,
        "supplier_offer": row(o.supplier_offer), "supplier": row(supplier) if supplier else None,
        "listing": row(o.listing), "matching": row(o.matching_result) if o.matching_result else None,
        "calculations": latest,
        "price_history": [{"metric": h.metric, "value": jsonable(h.value), "at": jsonable(h.observed_at),
                           "source": h.source} for h in history],
        "supplier_price_history": [{"value": jsonable(h.value), "at": jsonable(h.observed_at)} for h in offer_hist],
        "alternative_suppliers": [{"supplier": x.supplier.name, "price": jsonable(x.unit_price_net),
                                   "currency": x.currency, "stock": x.stock_qty, "offer_id": str(x.id)}
                                  for x in other_offers],
        "data_sources": {"listing_source": o.listing.source, "listing_fetched_at": jsonable(o.listing.fetched_at),
                         "offer_last_seen": jsonable(o.supplier_offer.last_seen_at)},
    }


class StatusIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)
    status: OpportunityStatus


@router.post("/status")
def set_status(body: StatusIn, request: Request, user: CurrentUser = Depends(require(Role.reviewer)),
               db: Session = Depends(get_db)):
    n = 0
    for o in db.scalars(select(Opportunity).where(Opportunity.id.in_(body.ids), Opportunity.org_id == user.org_id)):
        o.status = body.status
        n += 1
    audit(db, user, "opportunity_status", "opportunity", None, {"ids": [str(i) for i in body.ids], "status": body.status.value}, request)
    db.commit()
    return {"updated": n}


class AnalyzeIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    use_keepa: bool = True


@router.post("/analyze")
def reanalyze(body: AnalyzeIn, user: CurrentUser = Depends(require(Role.buyer)), db: Session = Depends(get_db)):
    offer_ids = [str(i) for i in db.scalars(select(Opportunity.supplier_offer_id).where(
        Opportunity.id.in_(body.ids), Opportunity.org_id == user.org_id))]
    from sbc.worker.tasks import analyze_offers

    analyze_offers.send(list(dict.fromkeys(offer_ids)), body.use_keepa)
    return {"queued": len(offer_ids)}


class PurchaseIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    kind: Literal["test", "reorder"] = "test"
    quantity: int | None = Field(default=None, ge=1)


@router.post("/purchase-list")
def add_to_purchase_list(body: PurchaseIn, request: Request, user: CurrentUser = Depends(require(Role.buyer)),
                         db: Session = Depends(get_db)):
    """Erzeugt Einkaufsvorschläge (Status 'proposed'). Es wird NICHTS bestellt."""
    from sbc.purchasing import propose

    thresholds = get_setting(db, user.org_id, "thresholds")
    out, skipped = [], []
    for o in db.scalars(select(Opportunity).where(Opportunity.id.in_(body.ids), Opportunity.org_id == user.org_id)):
        try:
            rec = propose(db, o, body.kind, body.quantity, thresholds)
            out.append(str(rec.id))
        except ValueError as e:
            skipped.append({"id": str(o.id), "reason": str(e)})
    audit(db, user, "purchase_list_add", "opportunity", None, {"created": out, "skipped": skipped}, request)
    db.commit()
    return {"created": out, "skipped": skipped}


@router.get("/purchase-list/items")
def purchase_list(status: str | None = None, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    q = select(PurchaseRecommendation).where(PurchaseRecommendation.org_id == user.org_id)
    if status:
        q = q.where(PurchaseRecommendation.status == status)
    return [{**row(r), "opportunity": _summary(r.opportunity)}
            for r in db.scalars(q.order_by(PurchaseRecommendation.created_at.desc()).limit(500))]
