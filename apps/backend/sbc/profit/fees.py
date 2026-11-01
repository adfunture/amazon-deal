"""Gebührenprofile je Marktplatz/Land/Kanal.

WICHTIG: Die Startwerte sind ANNAHMEN zur Inbetriebnahme (is_assumption=True). Sie ersetzen
keine Gebührenschätzung der SP-API (getMyFeesEstimate) oder der eBay-Gebührenübersicht und
müssen im Dashboard unter Einstellungen → Gebührenprofile geprüft werden. Die USt-Sätze sind
die Standardsätze der Länder (Stand 2026); ermäßigte Sätze je Produktkategorie sind zu pflegen.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from sbc.models import FeeProfile

STANDARD_VAT = {
    "DE": "0.19", "FR": "0.20", "IT": "0.22", "ES": "0.21",
    "NL": "0.21", "BE": "0.21", "PL": "0.23",
}

AMAZON_COUNTRIES = ["DE", "FR", "IT", "ES", "NL", "BE", "PL"]

# Annahmen: 15 % Provision (Kategorie prüfen), FBA-Gebühr 3,50 € (Größenklasse prüfen),
# FBM-Versand 4,90 €. Entspricht der bisherigen SBC-Arbeitsdatei (MANN-Filter-Kalkulation).
_DEFAULTS = {
    "FBA": {"referral_rate": "0.15", "referral_min": "0.30", "fulfillment_fee": "3.50",
            "storage_cost_per_month": "0.10", "packaging_cost": "0.00"},
    "FBM": {"referral_rate": "0.15", "referral_min": "0.30", "fulfillment_fee": "4.90",
            "storage_cost_per_month": "0.05", "packaging_cost": "0.40"},
    "EBAY": {"referral_rate": "0.11", "fixed_fee": "0.35", "fulfillment_fee": "4.90",
             "storage_cost_per_month": "0.05", "packaging_cost": "0.40"},
}


def default_profiles() -> list[dict]:
    rows = []
    for country in AMAZON_COUNTRIES:
        for channel in ("FBA", "FBM"):
            rows.append({"marketplace": "amazon", "country": country, "channel": channel,
                         "vat_rate": STANDARD_VAT[country], **_DEFAULTS[channel]})
    rows.append({"marketplace": "ebay", "country": "DE", "channel": "EBAY",
                 "vat_rate": STANDARD_VAT["DE"], **_DEFAULTS["EBAY"]})
    for r in rows:
        r.update({"payment_fee_rate": "0", "return_rate": "0.03", "return_cost": "5.00",
                  "ad_cost_rate": "0.05", "other_cost": "0"})
    return rows


def seed_fee_profiles(db: Session, org_id) -> int:
    created = 0
    for row in default_profiles():
        exists = db.scalar(
            select(FeeProfile.id).where(
                FeeProfile.org_id == org_id,
                FeeProfile.marketplace == row["marketplace"],
                FeeProfile.country == row["country"],
                FeeProfile.channel == row["channel"],
                FeeProfile.category == "default",
            )
        )
        if exists:
            continue
        db.add(FeeProfile(
            org_id=org_id, is_assumption=True,
            notes="Startannahme – vor Nutzung prüfen (SP-API-Gebührenschätzung bevorzugen)",
            **{k: (Decimal(v) if k not in {"marketplace", "country", "channel"} else v) for k, v in row.items()},
        ))
        created += 1
    return created


def get_profile(db: Session, org_id, marketplace: str, country: str, channel: str,
                category: str | None = None) -> FeeProfile | None:
    q = select(FeeProfile).where(
        FeeProfile.org_id == org_id, FeeProfile.marketplace == marketplace,
        FeeProfile.country == country, FeeProfile.channel == channel,
    )
    profiles = list(db.scalars(q))
    if category:
        for p in profiles:
            if p.category == category:
                return p
    return next((p for p in profiles if p.category == "default"), None)
