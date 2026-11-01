"""Health, Dashboard, Einstellungen, Monitoring, Einkaufsfreigaben, Amazon-Lookup."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from sbc import __version__
from sbc.api.deps import CurrentUser, audit, current_user, require
from sbc.api.serialize import jsonable, row
from sbc.config import get_settings
from sbc.db import get_db
from sbc.integrations.redis_client import redis_ok
from sbc.models import (
    APIUsage,
    BackgroundJob,
    FeeProfile,
    ImportJob,
    JobStatus,
    MarketplaceListing,
    MatchingResult,
    Opportunity,
    PurchaseRecommendation,
    RecommendationStatus,
    ReviewStatus,
    Role,
    SupplierOffer,
)
from sbc.settings_store import DEFAULTS, get_setting, put_setting

router = APIRouter()


# --------------------------------------------------------------------------- Health


@router.get("/health", tags=["health"])
def health():
    """Liveness – für den Railway-Healthcheck. Prüft bewusst keine Abhängigkeiten,
    damit ein kurzer DB-Ausfall keine Deploy-Schleife auslöst."""
    return {"status": "ok", "version": __version__}


@router.get("/health/ready", tags=["health"])
def ready(db: Session = Depends(get_db)):
    checks = {}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:  # pragma: no cover
        checks["database"] = f"error: {e.__class__.__name__}"
    checks["redis"] = "ok" if redis_ok() else ("not_configured" if not get_settings().redis_url else "error")
    ok = checks["database"] == "ok" and checks["redis"] in ("ok", "not_configured")
    if not ok:
        raise HTTPException(503, checks)
    return {"status": "ok", "checks": checks}


@router.get("/api/v1/integrations", tags=["system"])
def integrations(user: CurrentUser = Depends(current_user)):
    s = get_settings()
    return {
        "keepa": "configured" if s.keepa_api_key else "not_configured",
        "anthropic": "configured" if s.anthropic_api_key else "not_configured",
        "openai": "configured" if s.openai_api_key else "not_configured",
        "amazon_sp_api": "not_implemented (Phase 4)" if s.amazon_sp_api_credentials else "not_configured",
        "ebay": "not_implemented (Phase 4)" if s.ebay_client_id else "not_configured",
        "storage": s.storage_backend,
        "redis": "ok" if redis_ok() else "unavailable",
        "smtp": "configured" if s.smtp_url else "not_configured",
        "environment": s.app_env,
    }


# --------------------------------------------------------------------------- Dashboard


@router.get("/api/v1/dashboard", tags=["dashboard"])
def dashboard(user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    org = user.org_id
    since = datetime.now(UTC) - timedelta(days=7)
    live = (Opportunity.org_id == org, Opportunity.is_test_data.is_(False))
    profitable = db.scalar(select(func.count()).where(*live, Opportunity.is_excluded.is_(False)))
    avg_margin = db.scalar(select(func.avg(Opportunity.contribution_margin)).where(*live, Opportunity.is_excluded.is_(False)))
    potential = db.scalar(select(func.sum(Opportunity.contribution)).where(*live, Opportunity.is_excluded.is_(False)))
    top = db.scalars(select(Opportunity).where(*live, Opportunity.is_excluded.is_(False))
                     .order_by(Opportunity.score.desc()).limit(10))
    buckets = db.execute(select(func.floor(Opportunity.score / 10), func.count()).where(*live)
                         .group_by(func.floor(Opportunity.score / 10))).all()
    usage = db.execute(select(APIUsage.provider, func.sum(APIUsage.units), func.sum(APIUsage.cost_eur))
                       .where(APIUsage.created_at >= since).group_by(APIUsage.provider)).all()
    imports = db.execute(select(ImportJob.status, func.count()).where(ImportJob.org_id == org)
                         .group_by(ImportJob.status)).all()
    return {
        "analyzed_offers": db.scalar(select(func.count()).where(SupplierOffer.org_id == org, SupplierOffer.is_current.is_(True))),
        "analyzed_with_market_data": db.scalar(select(func.count(func.distinct(Opportunity.supplier_offer_id))).where(*live)),
        "profitable_candidates": profitable,
        "avg_margin": jsonable(Decimal(str(avg_margin)).quantize(Decimal("0.0001")) if avg_margin is not None else None),
        "potential_contribution_per_unit_sum": jsonable(potential),
        "top_opportunities": [{"id": str(o.id), "title": o.supplier_offer.title, "score": jsonable(o.score),
                               "contribution": jsonable(o.contribution), "margin": jsonable(o.contribution_margin)}
                              for o in top],
        "score_distribution": [{"bucket": int(b or 0) * 10, "count": c} for b, c in sorted(buckets, key=lambda x: x[0] or 0)],
        "api_usage_7d": [{"provider": p, "units": jsonable(u), "cost_eur": jsonable(c)} for p, u, c in usage],
        "imports": {s.value if hasattr(s, "value") else s: c for s, c in imports},
        "matching_pending": db.scalar(select(func.count()).select_from(MatchingResult).join(
            MarketplaceListing, MarketplaceListing.id == MatchingResult.listing_id).where(
            MatchingResult.org_id == org, MatchingResult.review_status == ReviewStatus.pending,
            MarketplaceListing.is_test_data.is_(False))),
        "failed_jobs_7d": db.scalar(select(func.count()).where(BackgroundJob.status.in_([JobStatus.failed, JobStatus.dead]),
                                                               BackgroundJob.created_at >= since)),
        "new_offers_7d": db.scalar(select(func.count()).where(SupplierOffer.org_id == org, SupplierOffer.first_seen_at >= since)),
        "test_data_present": bool(db.scalar(select(func.count()).where(Opportunity.org_id == org, Opportunity.is_test_data.is_(True)))),
    }


# --------------------------------------------------------------------------- Einstellungen


@router.get("/api/v1/settings", tags=["settings"])
def get_settings_all(user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    return {k: get_setting(db, user.org_id, k) for k in [*DEFAULTS.keys(), "reports"]}


class SettingIn(BaseModel):
    value: dict


@router.put("/api/v1/settings/{key}", tags=["settings"])
def put_settings(key: str, body: SettingIn, request: Request, user: CurrentUser = Depends(require(Role.admin)),
                 db: Session = Depends(get_db)):
    if key not in DEFAULTS and key != "reports":
        raise HTTPException(404, "Unbekannter Einstellungsbereich")
    if key == "score_weights":
        total = sum(Decimal(str(v)) for v in body.value.values())
        if abs(total - 1) > Decimal("0.001"):
            raise HTTPException(422, f"Gewichte müssen in Summe 1 ergeben (aktuell {total})")
    if key == "purchasing" and body.value.get("auto_order_enabled"):
        raise HTTPException(422, "Automatische Bestellungen sind nicht implementiert und bleiben deaktiviert")
    val = put_setting(db, user.org_id, key, body.value)
    audit(db, user, "settings_changed", "setting", key, body.value, request)
    db.commit()
    return val


class FeeProfileIn(BaseModel):
    marketplace: str
    country: str = Field(min_length=2, max_length=2)
    channel: str
    category: str = "default"
    vat_rate: Decimal
    referral_rate: Decimal | None = None
    referral_min: Decimal | None = None
    fixed_fee: Decimal | None = None
    fulfillment_fee: Decimal | None = None
    shipping_cost: Decimal | None = None
    storage_cost_per_month: Decimal | None = None
    packaging_cost: Decimal | None = None
    payment_fee_rate: Decimal | None = None
    return_rate: Decimal | None = None
    return_cost: Decimal | None = None
    ad_cost_rate: Decimal | None = None
    other_cost: Decimal | None = None
    is_assumption: bool = False
    notes: str | None = None


@router.get("/api/v1/fee-profiles", tags=["settings"])
def fee_profiles(user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    return [row(p) for p in db.scalars(select(FeeProfile).where(FeeProfile.org_id == user.org_id)
                                       .order_by(FeeProfile.marketplace, FeeProfile.country, FeeProfile.channel))]


@router.put("/api/v1/fee-profiles/{pid}", tags=["settings"])
def update_fee_profile(pid: uuid.UUID, body: FeeProfileIn, request: Request,
                       user: CurrentUser = Depends(require(Role.admin)), db: Session = Depends(get_db)):
    p = db.get(FeeProfile, pid)
    if p is None or p.org_id != user.org_id:
        raise HTTPException(404, "Profil nicht gefunden")
    for k, v in body.model_dump().items():
        setattr(p, k, v)
    audit(db, user, "fee_profile_updated", "fee_profile", p.id, jsonable(body.model_dump()), request)
    db.commit()
    return row(p)


# --------------------------------------------------------------------------- Monitoring


@router.get("/api/v1/monitoring", tags=["monitoring"])
def monitoring(days: int = 30, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    since = datetime.now(UTC) - timedelta(days=min(days, 365))
    day = func.date(APIUsage.created_at)
    per_day = db.execute(select(day, APIUsage.provider, func.sum(APIUsage.units), func.sum(APIUsage.cost_eur),
                                func.count(), func.sum(APIUsage.items))
                         .where(APIUsage.created_at >= since).group_by(day, APIUsage.provider).order_by(day)).all()
    errors = db.execute(select(APIUsage.provider, func.count()).where(APIUsage.created_at >= since,
                                                                       APIUsage.status_code >= 400)
                        .group_by(APIUsage.provider)).all()
    jobs = db.execute(select(BackgroundJob.kind, BackgroundJob.status, func.count(), func.avg(BackgroundJob.duration_ms))
                      .where(BackgroundJob.created_at >= since).group_by(BackgroundJob.kind, BackgroundJob.status)).all()
    recent_failed = db.scalars(select(BackgroundJob).where(BackgroundJob.status.in_([JobStatus.failed, JobStatus.dead]))
                               .order_by(BackgroundJob.created_at.desc()).limit(20))
    from sbc.integrations.keepa_service import load_token_state

    tokens = load_token_state()
    analyzed = db.scalar(select(func.count(func.distinct(Opportunity.supplier_offer_id))).where(
        Opportunity.org_id == user.org_id, Opportunity.updated_at >= since)) or 0
    keepa_units = sum((u or 0) for d, p, u, c, n, i in per_day if p == "keepa")
    ai_cost = sum((c or 0) for d, p, u, c, n, i in per_day if p == "anthropic")
    return {
        "per_day": [{"date": str(d), "provider": p, "units": jsonable(u), "cost_eur": jsonable(c),
                     "requests": n, "items": i} for d, p, u, c, n, i in per_day],
        "errors": {p: n for p, n in errors},
        "jobs": [{"kind": k, "status": s.value if hasattr(s, "value") else s, "count": n,
                  "avg_ms": int(a) if a else None} for k, s, n, a in jobs],
        "recent_failed_jobs": [row(j) for j in recent_failed],
        "keepa_tokens": tokens.__dict__ if tokens else None,
        "budgets": get_setting(db, user.org_id, "api_budgets"),
        "per_product": {
            "analyzed_offers": analyzed,
            "keepa_tokens_per_offer": jsonable(Decimal(str(keepa_units)) / analyzed if analyzed else None),
            "ai_cost_eur_per_offer": jsonable(Decimal(str(ai_cost)) / analyzed if analyzed else None),
        },
        "railway_resources": "Ressourcennutzung (CPU/RAM/Netzwerk) im Railway-Dashboard unter Metrics je Service",
    }


@router.get("/api/v1/jobs", tags=["monitoring"])
def list_jobs(status: JobStatus | None = None, limit: int = 100, user: CurrentUser = Depends(current_user),
              db: Session = Depends(get_db)):
    q = select(BackgroundJob)
    if status:
        q = q.where(BackgroundJob.status == status)
    return [row(j) for j in db.scalars(q.order_by(BackgroundJob.created_at.desc()).limit(min(limit, 500)))]


@router.get("/api/v1/audit", tags=["monitoring"])
def audit_log(limit: int = 200, user: CurrentUser = Depends(require(Role.admin)), db: Session = Depends(get_db)):
    from sbc.models import AuditLog

    return [row(a) for a in db.scalars(select(AuditLog).where(AuditLog.org_id == user.org_id)
                                       .order_by(AuditLog.created_at.desc()).limit(min(limit, 1000)))]


# --------------------------------------------------------------------------- Einkauf


class DecisionIn(BaseModel):
    status: RecommendationStatus
    quantity: int | None = Field(default=None, ge=1)


@router.post("/api/v1/purchase-recommendations/{rid}", tags=["purchasing"])
def decide(rid: uuid.UUID, body: DecisionIn, request: Request, user: CurrentUser = Depends(require(Role.buyer)),
           db: Session = Depends(get_db)):
    """Freigabe/Ablehnung. 'ordered' markiert eine manuell außerhalb des Systems ausgelöste Bestellung."""
    r = db.get(PurchaseRecommendation, rid)
    if r is None or r.org_id != user.org_id:
        raise HTTPException(404, "Vorschlag nicht gefunden")
    if body.quantity:
        r.quantity = body.quantity
        r.total_cost_net = (r.unit_cost_net * body.quantity).quantize(Decimal("0.01"))
    if body.status == RecommendationStatus.ordered and r.status != RecommendationStatus.approved:
        raise HTTPException(409, "Nur freigegebene Vorschläge können als bestellt markiert werden")
    r.status = body.status
    if body.status == RecommendationStatus.approved:
        r.approved_by, r.approved_at = user.id, datetime.now(UTC)
    audit(db, user, "purchase_decision", "purchase_recommendation", r.id,
          {"status": body.status.value, "quantity": r.quantity}, request)
    db.commit()
    return row(r)


# --------------------------------------------------------------------------- Amazon / Keepa


class LookupIn(BaseModel):
    codes: list[str] = Field(min_length=1, max_length=20)
    country: str = "DE"


@router.post("/api/v1/amazon/lookup", tags=["amazon"])
def amazon_lookup(body: LookupIn, user: CurrentUser = Depends(require(Role.buyer)), db: Session = Depends(get_db)):
    """Synchroner Keepa-Lookup für bis zu 20 EANs (Amazon Scanner)."""
    from sbc.integrations import keepa as k
    from sbc.integrations.keepa_service import lookup_codes
    from sbc.integrations.usage import BudgetExceeded

    if not get_settings().keepa_api_key:
        raise HTTPException(503, "KEEPA_API_KEY nicht konfiguriert")
    try:
        res = lookup_codes(db, user.org_id, body.codes, body.country.upper())
    except k.KeepaUnsupportedMarketplace as e:
        raise HTTPException(422, str(e)) from e
    except k.KeepaTokensExhausted as e:
        raise HTTPException(429, str(e)) from e
    except BudgetExceeded as e:
        raise HTTPException(429, str(e)) from e
    except k.KeepaError as e:
        raise HTTPException(502, str(e)) from e
    db.commit()
    return {code: [row(l, {"raw"}) for l in listings] for code, listings in res.items()}
