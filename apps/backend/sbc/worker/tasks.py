"""Hintergrundjobs (Dramatiq-Actors). Start: ``dramatiq sbc.worker.tasks``."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime, timedelta

import dramatiq
from sqlalchemy import select

from sbc.worker.broker import broker  # noqa: F401  (setzt den Broker)
from sbc.db import session_scope
from sbc.integrations.keepa import KeepaTokensExhausted
from sbc.models import ImportJob, ImportStatus, MarketplaceListing, Opportunity, SupplierOffer
from sbc.worker import jobs

log = logging.getLogger(__name__)
ANALYZE_CHUNK = 100  # = Keepa-Maximum je Request

# Queues: "imports" (Priorität hoch), "analysis", "maintenance". Mehrere Worker-Prozesse
# können gezielt Queues bedienen: dramatiq sbc.worker.tasks --queues imports analysis


@dramatiq.actor(queue_name="imports", max_retries=3, min_backoff=10_000, time_limit=6 * 3600 * 1000)
def process_import(job_id: str) -> None:
    from sbc.importer.service import process_job

    key = f"import:{job_id}"
    if not jobs.acquire_dedup(key, ttl_s=6 * 3600):
        log.info("Import %s läuft bereits", job_id)
        return
    t0 = time.monotonic()
    bg = jobs.start("process_import", {"job_id": job_id}, dedup_key=key, priority=1)
    try:
        with session_scope() as db:
            job = process_job(db, uuid.UUID(job_id))
            status, org_id = job.status, job.org_id
            offer_ids = [str(i) for i in db.scalars(select(SupplierOffer.id).where(
                SupplierOffer.last_import_job_id == job.id, SupplierOffer.is_current.is_(True)))]
            auto = (job.options or {}).get("auto_analyze", True)
        if status == ImportStatus.failed:
            raise RuntimeError("Import fehlgeschlagen – Details im Importjob")
        if auto:
            for i in range(0, len(offer_ids), ANALYZE_CHUNK):
                analyze_offers.send(offer_ids[i:i + ANALYZE_CHUNK], True)
        jobs.finish(bg, t0, {"offers": len(offer_ids), "org_id": str(org_id)})
    except Exception as e:
        jobs.finish(bg, t0, error=f"{e.__class__.__name__}: {e}")
        raise
    finally:
        jobs.release_dedup(key)


@dramatiq.actor(queue_name="analysis", max_retries=5, min_backoff=30_000, time_limit=3600 * 1000)
def analyze_offers(offer_ids: list[str], use_keepa: bool = True) -> None:
    from sbc.pipeline import analyze_offer

    t0 = time.monotonic()
    bg = jobs.start("analyze_offers", {"count": len(offer_ids), "use_keepa": use_keepa}, priority=5)
    done, summaries = 0, []
    try:
        if use_keepa:
            # Keepa gebündelt vorab abfragen (bis zu 100 EANs je Request statt 1 Request je Angebot).
            # Danach liegen die Listings frisch in der DB; die Einzelanalyse braucht keine API mehr.
            from sbc.config import get_settings
            from sbc.integrations.keepa import KeepaError
            from sbc.integrations.keepa_service import lookup_codes
            from sbc.integrations.usage import BudgetExceeded

            if get_settings().keepa_api_key:
                with session_scope() as db:
                    offers = [o for o in (db.get(SupplierOffer, uuid.UUID(i)) for i in offer_ids) if o is not None]
                    gtins = sorted({o.gtin for o in offers if o.gtin})
                    if gtins:
                        try:
                            lookup_codes(db, offers[0].org_id, gtins, "DE")
                        except KeepaTokensExhausted as e:
                            analyze_offers.send_with_options(args=(offer_ids, use_keepa),
                                                             delay=int(e.retry_after_s * 1000) + 5000)
                            jobs.finish(bg, t0, {"done": 0, "requeued": len(offer_ids), "reason": str(e)})
                            return
                        except (BudgetExceeded, KeepaError) as e:
                            log.warning("Keepa-Vorabfrage übersprungen: %s", e)
                use_keepa = False
        for oid in offer_ids:
            with session_scope() as db:
                offer = db.get(SupplierOffer, uuid.UUID(oid))
                if offer is None:
                    continue
                try:
                    summaries.append(analyze_offer(db, offer, use_keepa=use_keepa))
                except KeepaTokensExhausted as e:
                    # Rest später erneut einplanen statt Fehler zu werfen
                    rest = offer_ids[done:]
                    analyze_offers.send_with_options(args=(rest, use_keepa),
                                                     delay=int(e.retry_after_s * 1000) + 5000)
                    jobs.finish(bg, t0, {"done": done, "requeued": len(rest), "reason": str(e)})
                    return
            done += 1
        jobs.finish(bg, t0, {"done": done,
                             "opportunities": sum(s["opportunities"] for s in summaries),
                             "ai_reviews": sum(s["ai_reviews"] for s in summaries)})
    except Exception as e:
        jobs.finish(bg, t0, error=f"{e.__class__.__name__}: {e}")
        raise


@dramatiq.actor(queue_name="maintenance", max_retries=3)
def reanalyze_stale(max_age_hours: int = 24, limit: int = 2000) -> None:
    """Regelmäßige Neubewertung: Angebote mit relevanten Opportunities und alten Marktdaten."""
    cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)
    with session_scope() as db:
        ids = [str(i) for i in db.scalars(
            select(Opportunity.supplier_offer_id).join(MarketplaceListing,
                                                       MarketplaceListing.id == Opportunity.listing_id)
            .where(MarketplaceListing.fetched_at < cutoff, Opportunity.status != "ignored",
                   MarketplaceListing.is_test_data.is_(False))
            .order_by(Opportunity.score.desc().nulls_last()).limit(limit)
        ).unique()]
    for i in range(0, len(ids), ANALYZE_CHUNK):
        analyze_offers.send(ids[i:i + ANALYZE_CHUNK], True)


@dramatiq.actor(queue_name="analysis", max_retries=2, time_limit=3600 * 1000)
def recalculate_opportunities(org_id: str) -> None:
    """Neuberechnung ohne externe API-Aufrufe (Gebühren/Grenzwerte/Gewichte geändert)."""
    from sbc.models import MatchClass, MatchingResult, ReviewStatus
    from sbc.pipeline import evaluate

    t0 = time.monotonic()
    bg = jobs.start("recalculate_opportunities", {"org_id": org_id})
    n = 0
    with session_scope() as db:
        q = select(MatchingResult).where(MatchingResult.org_id == uuid.UUID(org_id),
                                         MatchingResult.classification != MatchClass.NO_MATCH,
                                         MatchingResult.review_status != ReviewStatus.rejected)
        for mr in db.scalars(q):
            evaluate(db, mr)
            n += 1
            if n % 200 == 0:
                db.commit()
    jobs.finish(bg, t0, {"recalculated": n})


@dramatiq.actor(queue_name="maintenance", max_retries=5, min_backoff=60_000)
def refresh_fx_rates() -> None:
    from sbc.integrations.fx import refresh_all_orgs

    refresh_all_orgs()


@dramatiq.actor(queue_name="maintenance", max_retries=2)
def cleanup_expired_files() -> None:
    from sbc.config import get_settings
    from sbc.integrations.storage import get_storage

    days = get_settings().file_retention_days
    cutoff = datetime.now(UTC) - timedelta(days=days)
    storage = get_storage()
    with session_scope() as db:
        for job in db.scalars(select(ImportJob).where(ImportJob.created_at < cutoff,
                                                      ImportJob.file_deleted_at.is_(None)).limit(500)):
            try:
                storage.delete(job.file_key)
                job.file_deleted_at = datetime.now(UTC)
            except Exception as e:  # pragma: no cover - Netzwerkfehler
                log.warning("Löschen %s fehlgeschlagen: %s", job.file_key, e)


@dramatiq.actor(queue_name="maintenance", max_retries=1)
def daily_report() -> None:
    from sbc.reports import build_and_deliver_daily_reports

    build_and_deliver_daily_reports()
