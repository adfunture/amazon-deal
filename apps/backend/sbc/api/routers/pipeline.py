"""Endpunkte für Automatisierung (n8n, Cron): Jobs einplanen, Berichte abrufen.

Alle Endpunkte reihen nur Jobs in die Queue ein – die Verarbeitung läuft im Worker.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sbc.api.deps import CurrentUser, current_user, require
from sbc.api.serialize import row
from sbc.db import get_db
from sbc.models import MatchingResult, Role, Supplier, SupplierOffer

router = APIRouter(tags=["automation"])


@router.get("/suppliers-lookup/by-email")
def supplier_by_email(sender: str, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    """Ordnet einen E-Mail-Absender einem Lieferanten zu (email_import_rules.senders / .domains)."""
    sender = sender.strip().lower()
    domain = sender.split("@")[-1]
    for s in db.scalars(select(Supplier).where(Supplier.org_id == user.org_id, Supplier.is_active.is_(True))):
        rules = s.email_import_rules or {}
        if sender in [x.lower() for x in rules.get("senders", [])] or domain in [x.lower() for x in rules.get("domains", [])]:
            return {"supplier_id": str(s.id), "name": s.name, "auto_start": bool(rules.get("auto_start", False))}
    raise HTTPException(404, "Kein Lieferant für diesen Absender konfiguriert")


class AnalyzeIn(BaseModel):
    limit: int = Field(default=1000, ge=1, le=20000)
    use_keepa: bool = True


@router.post("/pipeline/analyze-pending")
def analyze_pending(body: AnalyzeIn, user: CurrentUser = Depends(require(Role.buyer)), db: Session = Depends(get_db)):
    """Aktuelle Angebote ohne Matching-Ergebnis zur Analyse einplanen."""
    from sbc.worker.tasks import ANALYZE_CHUNK, analyze_offers

    has_match = select(MatchingResult.supplier_offer_id).where(MatchingResult.org_id == user.org_id)
    ids = [str(i) for i in db.scalars(
        select(SupplierOffer.id).where(SupplierOffer.org_id == user.org_id, SupplierOffer.is_current.is_(True),
                                       SupplierOffer.id.notin_(has_match))
        .order_by(SupplierOffer.last_seen_at.desc()).limit(body.limit))]
    for i in range(0, len(ids), ANALYZE_CHUNK):
        analyze_offers.send(ids[i:i + ANALYZE_CHUNK], body.use_keepa)
    return {"queued": len(ids)}


@router.post("/pipeline/refresh-market-data")
def refresh_market(max_age_hours: int = 24, user: CurrentUser = Depends(require(Role.buyer))):
    from sbc.worker.tasks import reanalyze_stale

    reanalyze_stale.send(max_age_hours)
    return {"queued": True}


@router.post("/pipeline/recalculate")
def recalculate(user: CurrentUser = Depends(require(Role.buyer))):
    """Kalkulation & Score aller Matches neu berechnen (z. B. nach Änderung von Gebühren/Grenzwerten)."""
    from sbc.worker.tasks import recalculate_opportunities

    recalculate_opportunities.send(str(user.org_id))
    return {"queued": True}


@router.get("/reports/daily")
def daily_report(limit: int = 25, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    from sbc.reports import render_text, top_opportunities

    items = top_opportunities(db, user.org_id, min(limit, 200))
    return {"items": items, "text": render_text(items), "count": len(items)}


@router.get("/pipeline/status")
def status(user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    from sbc.models import BackgroundJob, JobStatus

    running = db.scalars(select(BackgroundJob).where(BackgroundJob.status == JobStatus.running)
                         .order_by(BackgroundJob.created_at.desc()).limit(20))
    pending_offers = db.scalar(select(func.count()).where(
        SupplierOffer.org_id == user.org_id, SupplierOffer.is_current.is_(True),
        SupplierOffer.id.notin_(select(MatchingResult.supplier_offer_id))))
    return {"running_jobs": [row(j) for j in running], "offers_without_analysis": pending_offers}
