"""Täglicher Sourcing-Bericht.

Der Bericht wird immer erzeugt und im Dashboard abrufbar gespeichert (background_jobs.result).
E-Mail-Versand erfolgt NUR, wenn SMTP_URL und REPORT_RECIPIENTS gesetzt sind UND in den
Einstellungen ``reports.email_enabled`` explizit aktiviert wurde.
"""

from __future__ import annotations

import smtplib
import time
from email.message import EmailMessage
from urllib.parse import urlparse

from sqlalchemy import select

from sbc.config import get_settings
from sbc.db import session_scope
from sbc.models import Opportunity, OpportunityStatus, Organization
from sbc.settings_store import get_setting
from sbc.worker import jobs


def top_opportunities(db, org_id, limit: int = 25) -> list[dict]:
    rows = db.scalars(
        select(Opportunity).where(Opportunity.org_id == org_id, Opportunity.is_excluded.is_(False),
                                  Opportunity.is_test_data.is_(False),
                                  Opportunity.status.notin_([OpportunityStatus.ignored, OpportunityStatus.purchased]))
        .order_by(Opportunity.score.desc()).limit(limit * 3)
    )
    seen, out = set(), []
    for o in rows:  # Dubletten: je Lieferantenangebot nur der beste Kanal/Listing
        if o.supplier_offer_id in seen:
            continue
        seen.add(o.supplier_offer_id)
        out.append({
            "title": o.supplier_offer.title, "gtin": o.supplier_offer.gtin, "asin": o.listing.external_id,
            "supplier": o.supplier_offer.supplier.name, "channel": o.channel,
            "buy": str(o.buy_price_net), "sell": str(o.sell_price_gross),
            "contribution": str(o.contribution), "margin": str(o.contribution_margin),
            "score": str(o.score), "test_qty": (o.score_breakdown or {}).get("test_quantity"),
        })
        if len(out) >= limit:
            break
    return out


def render_text(items: list[dict]) -> str:
    lines = ["SBC Sourcing – Top-Einkaufschancen", ""]
    for i, it in enumerate(items, 1):
        lines.append(f"{i}. {it['title']} | EAN {it['gtin']} | ASIN {it['asin']} | {it['supplier']}")
        lines.append(f"   EK {it['buy']} € netto → VK {it['sell']} € brutto | DB {it['contribution']} € "
                     f"| Marge {it['margin']} | Score {it['score']} | Testmenge {it['test_qty']}")
    lines += ["", "Alle Werte sind Vorschläge. Einkäufe erfordern manuelle Freigabe."]
    return "\n".join(lines)


def send_mail(subject: str, body: str, recipients: list[str]) -> None:
    url = urlparse(get_settings().smtp_url or "")
    with smtplib.SMTP(url.hostname, url.port or 587, timeout=30) as s:
        s.starttls()
        if url.username:
            s.login(url.username, url.password or "")
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, url.username or "noreply", ", ".join(recipients)
        msg.set_content(body)
        s.send_message(msg)


def build_and_deliver_daily_reports() -> list[dict]:
    t0 = time.monotonic()
    settings = get_settings()
    out = []
    with session_scope() as db:
        for org in db.scalars(select(Organization)):
            items = top_opportunities(db, org.id)
            cfg = get_setting(db, org.id, "reports")
            delivered = False
            recipients = [r.strip() for r in settings.report_recipients.split(",") if r.strip()]
            if cfg.get("email_enabled") and settings.smtp_url and recipients and items:
                send_mail("SBC Sourcing – Tagesbericht", render_text(items), recipients)
                delivered = True
            out.append({"org": str(org.id), "items": items, "emailed": delivered})
    bg = jobs.start("daily_report", {})
    jobs.finish(bg, t0, {"reports": out})
    return out
