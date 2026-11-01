"""Keepa-Abfragen mit Caching, Deduplizierung, Token-/Budgetverwaltung und Persistenz."""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from sbc.config import get_settings
from sbc.domain.identifiers import normalize_gtin
from sbc.integrations.keepa import (
    MAX_CODES_PER_REQUEST,
    KeepaClient,
    KeepaListing,
    KeepaTokensExhausted,
    TokenState,
    parse_product,
)
from sbc.integrations.redis_client import get_redis
from sbc.integrations.usage import check_budget, record_usage
from sbc.models import ListingGtin, MarketplaceListing, PriceHistory

log = logging.getLogger(__name__)
TOKEN_KEY = "keepa:tokens"


def save_token_state(state: TokenState) -> None:
    r = get_redis()
    if r is not None:
        r.set(TOKEN_KEY, json.dumps(state.__dict__), ex=3600)


def load_token_state() -> TokenState | None:
    r = get_redis()
    if r is None:
        return None
    raw = r.get(TOKEN_KEY)
    return TokenState(**json.loads(raw)) if raw else None


def ensure_tokens(needed: int) -> None:
    """Vorab-Prüfung anhand des letzten bekannten Token-Stands (vermeidet sinnlose 429)."""
    st = load_token_state()
    if st is None:
        return
    elapsed_ms = time.time() * 1000 - st.timestamp_ms
    est = st.tokens_left
    if elapsed_ms > st.refill_in_ms:
        est += st.refill_rate * (1 + int((elapsed_ms - st.refill_in_ms) // 60000))
    if est < needed:
        missing = needed - est
        wait_s = max(60.0 * missing / max(st.refill_rate, 1), 5.0)
        raise KeepaTokensExhausted(wait_s, st.tokens_left)


def upsert_listing(db: Session, org_id, kl: KeepaListing, is_test_data: bool = False,
                   source: str = "keepa") -> MarketplaceListing:
    listing = db.scalar(select(MarketplaceListing).where(
        MarketplaceListing.org_id == org_id, MarketplaceListing.marketplace == "amazon",
        MarketplaceListing.country == kl.country, MarketplaceListing.external_id == kl.asin,
    ))
    if listing is None:
        listing = MarketplaceListing(org_id=org_id, marketplace="amazon", country=kl.country,
                                     external_id=kl.asin, source=source)
        db.add(listing)
    gtins = []
    for g in kl.gtins:
        n, _ = normalize_gtin(g)
        if n and n not in gtins:
            gtins.append(n)
    for attr in ("title", "brand", "manufacturer", "model", "mpn", "package_quantity",
                 "number_of_items", "category", "image_urls", "buybox_price", "new_price",
                 "amazon_price", "avg30_price", "avg90_price", "min90_price", "price_cv90",
                 "sales_rank", "sales_rank_drops30", "sales_rank_drops90", "monthly_sold",
                 "offer_count_new", "amazon_is_seller"):
        setattr(listing, attr, getattr(kl, attr))
    for attr, n in (("title", 1000), ("brand", 200), ("manufacturer", 200), ("model", 200), ("mpn", 200),
                    ("category", 500)):
        v = getattr(listing, attr)
        if isinstance(v, str) and len(v) > n:
            setattr(listing, attr, v[: n - 1] + "…")
    listing.gtins = gtins
    db.flush()
    existing = set(db.scalars(select(ListingGtin.gtin).where(ListingGtin.listing_id == listing.id)))
    for g in gtins:
        if g not in existing:
            db.add(ListingGtin(listing_id=listing.id, gtin=g))
    listing.raw = kl.raw_meta
    listing.source = source
    listing.is_test_data = is_test_data
    listing.fetched_at = datetime.now(UTC)
    db.flush()
    now = datetime.now(UTC)
    for metric, value in (("buybox", kl.buybox_price), ("new", kl.new_price),
                          ("amazon", kl.amazon_price), ("sales_rank", kl.sales_rank)):
        if value is not None:
            db.add(PriceHistory(org_id=org_id, subject_type="listing", subject_id=listing.id,
                                metric=metric, value=Decimal(value), currency="EUR" if metric != "sales_rank" else None,
                                tax_basis="gross" if metric != "sales_rank" else None,
                                observed_at=now, source=source))
    return listing


def lookup_codes(db: Session, org_id, codes: list[str], country: str = "DE",
                 client: KeepaClient | None = None, force: bool = False) -> dict[str, list[MarketplaceListing]]:
    """Sucht Amazon-Listings zu GTINs. Frische DB-Treffer werden nicht erneut abgefragt."""
    settings = get_settings()
    ttl = timedelta(hours=settings.keepa_cache_ttl_hours)
    result: dict[str, list[MarketplaceListing]] = {}
    todo: list[str] = []
    for code in dict.fromkeys(codes):
        g, _ = normalize_gtin(code)
        if not g:
            continue
        if not force:
            cached = [l for l in db.scalars(
                select(MarketplaceListing).join(ListingGtin, ListingGtin.listing_id == MarketplaceListing.id)
                .where(ListingGtin.gtin == g, MarketplaceListing.org_id == org_id,
                       MarketplaceListing.marketplace == "amazon", MarketplaceListing.country == country,
                       MarketplaceListing.is_test_data.is_(False))
            ) if l.fetched_at and _aware(l.fetched_at) > datetime.now(UTC) - ttl]
            miss_key = f"keepa:miss:{country}:{g}"
            r = get_redis()
            if cached:
                result[g] = cached
                continue
            if r is not None and r.get(miss_key):
                result[g] = []
                continue
        todo.append(g)
    if not todo:
        return result

    client = client or KeepaClient(settings.keepa_api_key)
    r = get_redis()
    for i in range(0, len(todo), MAX_CODES_PER_REQUEST):
        batch = todo[i:i + MAX_CODES_PER_REQUEST]
        # Doppelte parallele Abfragen vermeiden
        locked = []
        if r is not None:
            for g in batch:
                if r.set(f"keepa:lock:{country}:{g}", "1", nx=True, ex=300):
                    locked.append(g)
            batch = locked
        if not batch:
            continue
        try:
            check_budget(db, org_id, "keepa", planned=len(batch))
            ensure_tokens(len(batch))
            t0 = time.monotonic()
            # Keepa erwartet EAN-13/UPC ohne Auffüll-Nullen
            query_codes = [g.lstrip("0").rjust(13, "0") if len(g.lstrip("0")) <= 13 else g for g in batch]
            data = client.products(country, codes=query_codes)
            if client.last_tokens:
                save_token_state(client.last_tokens)
            consumed = client.last_tokens.tokens_consumed if client.last_tokens else len(batch)
            record_usage(db, org_id=org_id, provider="keepa", endpoint="product", units=consumed,
                         unit_type="keepa_tokens", status_code=200,
                         duration_ms=int((time.monotonic() - t0) * 1000), items=len(batch),
                         meta={"country": country, "tokens_left": client.last_tokens.tokens_left if client.last_tokens else None})
            found: dict[str, list[MarketplaceListing]] = {g: [] for g in batch}
            for p in data.get("products") or []:
                kl = parse_product(p, country)
                listing = upsert_listing(db, org_id, kl)
                for g in listing.gtins:
                    if g in found:
                        found[g].append(listing)
            for g, ls in found.items():
                result[g] = ls
                if not ls and r is not None:
                    r.set(f"keepa:miss:{country}:{g}", "1", ex=int(ttl.total_seconds()))
            db.commit()
        except KeepaTokensExhausted as e:
            if client.last_tokens:
                save_token_state(client.last_tokens)
            record_usage(db, org_id=org_id, provider="keepa", endpoint="product", units=0,
                         unit_type="keepa_tokens", status_code=429, items=len(batch))
            db.commit()
            raise e
        finally:
            if r is not None:
                for g in batch:
                    r.delete(f"keepa:lock:{country}:{g}")
    return result


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def lookup_asins(db: Session, org_id, asins: list[str], country: str = "DE",
                 client: KeepaClient | None = None) -> dict[str, MarketplaceListing]:
    """Listings per ASIN abfragen (1 Token je Produkt, bis 100 je Request) und speichern."""
    settings = get_settings()
    client = client or KeepaClient(settings.keepa_api_key)
    out: dict[str, MarketplaceListing] = {}
    asins = list(dict.fromkeys(a.strip().upper() for a in asins if a))
    for i in range(0, len(asins), MAX_CODES_PER_REQUEST):
        batch = asins[i:i + MAX_CODES_PER_REQUEST]
        check_budget(db, org_id, "keepa", planned=len(batch))
        ensure_tokens(len(batch))
        t0 = time.monotonic()
        data = client.products(country, asins=batch)
        if client.last_tokens:
            save_token_state(client.last_tokens)
        record_usage(db, org_id=org_id, provider="keepa", endpoint="product_asin",
                     units=client.last_tokens.tokens_consumed if client.last_tokens else len(batch),
                     unit_type="keepa_tokens", status_code=200, duration_ms=int((time.monotonic() - t0) * 1000),
                     items=len(batch), meta={"country": country})
        for p in data.get("products") or []:
            if p.get("title") is None and not p.get("eanList"):
                continue  # Keepa liefert für unbekannte ASINs leere Objekte
            listing = upsert_listing(db, org_id, parse_product(p, country))
            out[listing.external_id] = listing
    return out
