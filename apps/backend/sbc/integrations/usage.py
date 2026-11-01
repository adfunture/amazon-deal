"""API-Verbrauch protokollieren und Budgets durchsetzen (Tages-/Monatslimits je Anbieter)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sbc.models import APIUsage
from sbc.settings_store import get_setting


class BudgetExceeded(RuntimeError):
    def __init__(self, provider: str, period: str, used: Decimal, limit: Decimal):
        super().__init__(f"{provider}: {period}-Budget erschöpft ({used}/{limit})")
        self.provider, self.period, self.used, self.limit = provider, period, used, limit


def record_usage(db: Session, *, org_id, provider: str, endpoint: str, units: Decimal | int,
                 unit_type: str, cost_eur: Decimal | None = None, status_code: int | None = None,
                 duration_ms: int | None = None, items: int = 1, meta: dict | None = None) -> None:
    db.add(APIUsage(org_id=org_id, provider=provider, endpoint=endpoint, units=Decimal(units),
                    unit_type=unit_type, cost_eur=cost_eur, status_code=status_code,
                    duration_ms=duration_ms, items=items, meta=meta or {}))
    db.flush()


def usage_since(db: Session, org_id, provider: str, since: datetime, field: str = "units") -> Decimal:
    col = APIUsage.units if field == "units" else APIUsage.cost_eur
    v = db.scalar(select(func.coalesce(func.sum(col), 0)).where(
        APIUsage.provider == provider, APIUsage.created_at >= since,
        (APIUsage.org_id == org_id) | (APIUsage.org_id.is_(None)),
    ))
    return Decimal(str(v or 0))


def check_budget(db: Session, org_id, provider: str, planned: Decimal | int = 0) -> None:
    """Budgets aus app_settings['api_budgets'][provider] = {daily, monthly, field}."""
    budgets = get_setting(db, org_id, "api_budgets").get(provider) or {}
    field = budgets.get("field", "units")
    now = datetime.now(UTC)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)
    for period, start in (("daily", day_start), ("monthly", month_start)):
        limit = budgets.get(period)
        if limit is None:
            continue
        used = usage_since(db, org_id, provider, start, field)
        if used + Decimal(planned) > Decimal(str(limit)):
            raise BudgetExceeded(provider, period, used, Decimal(str(limit)))
