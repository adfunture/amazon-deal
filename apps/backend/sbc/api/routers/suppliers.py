from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sbc.api.deps import CurrentUser, audit, current_user, require
from sbc.api.serialize import row
from sbc.db import get_db
from sbc.models import ImportJob, ImportProfile, Role, Supplier, SupplierFeed, SupplierOffer

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


class SupplierIn(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=200)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    contact: dict = Field(default_factory=dict)
    payment_terms: str | None = None
    incoterm: str | None = None
    shipping_cost_per_order: Decimal | None = None
    shipping_cost_per_unit: Decimal | None = None
    free_shipping_from: Decimal | None = None
    min_order_value: Decimal | None = None
    default_moq: int | None = None
    lead_time_days: int | None = None
    import_duty_rate: Decimal | None = None
    discount_tiers: list = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    brands: list[str] = Field(default_factory=list)
    prices_include_vat: bool = False
    offer_valid_until: datetime | None = None
    rating: Decimal | None = Field(default=None, ge=0, le=5)
    email_import_rules: dict = Field(default_factory=dict)
    notes: str | None = None
    is_active: bool = True


def _get(db: Session, user: CurrentUser, sid: uuid.UUID) -> Supplier:
    s = db.get(Supplier, sid)
    if s is None or s.org_id != user.org_id:
        raise HTTPException(404, "Lieferant nicht gefunden")
    return s


@router.get("")
def list_suppliers(user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    counts = dict(db.execute(select(SupplierOffer.supplier_id, func.count()).where(
        SupplierOffer.org_id == user.org_id, SupplierOffer.is_current.is_(True)
    ).group_by(SupplierOffer.supplier_id)).all())
    return [{**row(s), "current_offers": counts.get(s.id, 0)}
            for s in db.scalars(select(Supplier).where(Supplier.org_id == user.org_id).order_by(Supplier.name))]


@router.post("", status_code=201)
def create_supplier(body: SupplierIn, request: Request, user: CurrentUser = Depends(require(Role.buyer)),
                    db: Session = Depends(get_db)):
    if db.scalar(select(Supplier).where(Supplier.org_id == user.org_id, Supplier.code == body.code)):
        raise HTTPException(409, "Lieferanten-ID existiert bereits")
    s = Supplier(org_id=user.org_id, **body.model_dump())
    s.currency = s.currency.upper()
    db.add(s)
    db.flush()
    audit(db, user, "supplier_created", "supplier", s.id, {"code": s.code}, request)
    db.commit()
    return row(s)


@router.get("/{sid}")
def get_supplier(sid: uuid.UUID, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    s = _get(db, user, sid)
    imports = db.scalars(select(ImportJob).where(ImportJob.supplier_id == sid)
                         .order_by(ImportJob.created_at.desc()).limit(50))
    profiles = db.scalars(select(ImportProfile).where(ImportProfile.supplier_id == sid))
    feeds = db.scalars(select(SupplierFeed).where(SupplierFeed.supplier_id == sid))
    return {**row(s), "imports": [row(j, {"detected_columns"}) for j in imports],
            "profiles": [row(p) for p in profiles], "feeds": [row(f) for f in feeds]}


@router.put("/{sid}")
def update_supplier(sid: uuid.UUID, body: SupplierIn, request: Request,
                    user: CurrentUser = Depends(require(Role.buyer)), db: Session = Depends(get_db)):
    s = _get(db, user, sid)
    before = row(s)
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    audit(db, user, "supplier_updated", "supplier", s.id,
          {"changed": {k: v for k, v in row(s).items() if before.get(k) != v and k != "updated_at"}}, request)
    db.commit()
    return row(s)


class FeedIn(BaseModel):
    name: str
    feed_type: str = Field(pattern="^(upload|email|url|sftp|api)$")
    config: dict = Field(default_factory=dict)
    secret_ref: str | None = Field(default=None, description="Name der Env-Variable mit Zugangsdaten")
    import_profile_id: uuid.UUID | None = None
    schedule_cron: str | None = None
    is_active: bool = True


@router.post("/{sid}/feeds", status_code=201)
def create_feed(sid: uuid.UUID, body: FeedIn, user: CurrentUser = Depends(require(Role.buyer)),
                db: Session = Depends(get_db)):
    _get(db, user, sid)
    if body.feed_type == "url" and body.config.get("url"):
        from sbc.security.ssrf import UnsafeURL, check_url

        try:
            check_url(body.config["url"])
        except UnsafeURL as e:
            raise HTTPException(422, str(e)) from e
    f = SupplierFeed(org_id=user.org_id, supplier_id=sid, **body.model_dump())
    db.add(f)
    db.commit()
    return row(f)


@router.get("/{sid}/price-history")
def supplier_price_changes(sid: uuid.UUID, limit: int = 200, user: CurrentUser = Depends(current_user),
                           db: Session = Depends(get_db)):
    _get(db, user, sid)
    offers = db.scalars(select(SupplierOffer).where(
        SupplierOffer.supplier_id == sid, SupplierOffer.previous_unit_price_net.is_not(None)
    ).order_by(SupplierOffer.last_price_change_at.desc()).limit(min(limit, 1000)))
    return [{"offer_id": str(o.id), "sku": o.supplier_sku, "gtin": o.gtin, "title": o.title,
             "previous": str(o.previous_unit_price_net), "current": str(o.unit_price_net),
             "changed_at": o.last_price_change_at.isoformat() if o.last_price_change_at else None}
            for o in offers]
