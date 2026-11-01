"""Konfigurierbare Parameter je Organisation (über das Dashboard änderbar)."""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sbc.models import AppSetting

DEFAULTS: dict[str, dict[str, Any]] = {
    # Startparameter – bewusst veränderbar, keine fest eingebauten Regeln
    "thresholds": {
        "min_margin": "0.12",
        "min_contribution": "5.00",
        "max_test_purchase": "1000.00",
        "min_match_confidence": "0.95",
        "allowed_conditions": ["new"],
        "require_stock": True,
    },
    "score_weights": {
        "profit": "0.35",
        "demand": "0.25",
        "stability_competition": "0.15",
        "turnover": "0.15",
        "sourcing_risk": "0.10",
    },
    "purchasing": {
        "test_order_months": "1",
        "reorder_cover_months": "2",
        "own_share_of_market": "0.15",  # realistischer eigener Anteil am Gesamtmarktabsatz
        "budget_total": "10000.00",
        "auto_order_enabled": False,  # automatische Bestellung ist AUS und nicht implementiert
    },
    "api_budgets": {
        "keepa": {"daily": 5000, "monthly": 100000, "field": "units"},
        "anthropic": {"daily": 20, "monthly": 300, "field": "cost"},
    },
    "analysis": {
        "ai_min_potential_contribution": "3.00",  # Level 3 (KI) nur ab diesem erwarteten DB
        "keepa_marketplaces": ["DE"],
        "default_channel": "FBA",
    },
    "fx_rates": {"base": "EUR", "rates": {}, "date": None, "source": None},
}


def get_setting(db: Session, org_id, key: str) -> dict[str, Any]:
    base = copy.deepcopy(DEFAULTS.get(key, {}))
    row = db.scalar(select(AppSetting).where(AppSetting.org_id == org_id, AppSetting.key == key))
    if row:
        base.update(row.value)
    return base


def put_setting(db: Session, org_id, key: str, value: dict[str, Any]) -> dict[str, Any]:
    row = db.scalar(select(AppSetting).where(AppSetting.org_id == org_id, AppSetting.key == key))
    if row:
        row.value = {**row.value, **value}
    else:
        row = AppSetting(org_id=org_id, key=key, value=value)
        db.add(row)
    db.flush()
    return get_setting(db, org_id, key)
