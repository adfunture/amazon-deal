"""Keepa-API-Adapter.

Geprüft gegen die öffentlichen Keepa-Definitionen (keepacom/api_backend, Python-Client
``keepa``, Stand 2026-11):
* Basis-URL ``https://api.keepa.com``; Endpunkte ``/product`` und ``/token``.
* ``/product``-Parameter: key, domain, asin | code (kommagetrennt, max. 100), stats (Tage),
  history (0/1), offers (20..100, kostet zusätzliche Tokens – hier nicht genutzt), days.
* Domain-IDs: 1 US, 2 GB, 3 DE, 4 FR, 5 JP, 6 CA, 8 IT, 9 ES, 10 IN, 11 MX, 12 BR.
  NL, BE, PL werden von Keepa NICHT unterstützt → als "nicht verfügbar" gekennzeichnet.
* csv-Indizes: 0 AMAZON, 1 NEW, 3 SALES, 7 NEW_FBM_SHIPPING, 11 COUNT_NEW,
  18 BUY_BOX_SHIPPING (Tripel Zeit/Preis/Versand).
* Zeit: Keepa-Minuten seit 2011-01-01 → Unix = (km + 21564000) * 60.
* Preise in Cent der Marktplatzwährung, -1 = keine Daten, -2 = nicht verfügbar.
* HTTP 429 = nicht genug Tokens; Antwort enthält tokensLeft, refillIn (ms), refillRate (/min).
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

BASE_URL = "https://api.keepa.com"
KEEPA_EPOCH_OFFSET_MIN = 21564000
DOMAINS = {"US": 1, "GB": 2, "DE": 3, "FR": 4, "JP": 5, "CA": 6, "IT": 8, "ES": 9}
UNSUPPORTED = {"NL", "BE", "PL"}
MAX_CODES_PER_REQUEST = 100

IDX_AMAZON, IDX_NEW, IDX_SALES, IDX_COUNT_NEW, IDX_BUY_BOX = 0, 1, 3, 11, 18


class KeepaError(RuntimeError):
    pass


class KeepaNotConfigured(KeepaError):
    pass


class KeepaUnsupportedMarketplace(KeepaError):
    pass


class KeepaTokensExhausted(KeepaError):
    def __init__(self, retry_after_s: float, tokens_left: int | None):
        super().__init__(f"Keepa-Tokens erschöpft, nächste Auffüllung in {retry_after_s:.0f}s")
        self.retry_after_s = retry_after_s
        self.tokens_left = tokens_left


@dataclass
class TokenState:
    tokens_left: int
    refill_in_ms: int
    refill_rate: int
    timestamp_ms: int
    tokens_consumed: int = 0

    @classmethod
    def from_response(cls, data: dict) -> TokenState:
        return cls(
            tokens_left=int(data.get("tokensLeft", 0)),
            refill_in_ms=int(data.get("refillIn", 0)),
            refill_rate=int(data.get("refillRate", 0)),
            timestamp_ms=int(data.get("timestamp", time.time() * 1000)),
            tokens_consumed=int(data.get("tokensConsumed", 0)),
        )


def keepa_minutes_to_dt(km: int) -> datetime:
    return datetime.fromtimestamp((km + KEEPA_EPOCH_OFFSET_MIN) * 60, tz=UTC)


def domain_id(country: str) -> int:
    c = country.upper()
    if c in UNSUPPORTED:
        raise KeepaUnsupportedMarketplace(f"Keepa unterstützt Amazon {c} nicht")
    if c not in DOMAINS:
        raise KeepaUnsupportedMarketplace(f"Unbekannter Marktplatz {c}")
    return DOMAINS[c]


class KeepaClient:
    def __init__(self, api_key: str | None, http: httpx.Client | None = None, timeout: float = 60):
        if not api_key:
            raise KeepaNotConfigured("KEEPA_API_KEY ist nicht gesetzt")
        self._key = api_key
        self._http = http or httpx.Client(base_url=BASE_URL, timeout=timeout)
        self.last_tokens: TokenState | None = None

    def _get(self, path: str, params: dict[str, Any]) -> dict:
        params = {"key": self._key, **params}
        try:
            resp = self._http.get(BASE_URL + path, params=params)
        except httpx.HTTPError as e:
            raise KeepaError(f"Keepa nicht erreichbar: {e.__class__.__name__}") from e
        try:
            data = resp.json()
        except ValueError as e:
            raise KeepaError(f"Ungültige Antwort von Keepa (HTTP {resp.status_code})") from e
        if isinstance(data, dict) and "tokensLeft" in data:
            self.last_tokens = TokenState.from_response(data)
        if resp.status_code == 429:
            refill = (self.last_tokens.refill_in_ms if self.last_tokens else 60000) / 1000
            raise KeepaTokensExhausted(max(refill, 1.0), self.last_tokens.tokens_left if self.last_tokens else None)
        if resp.status_code >= 400:
            msg = data.get("error", {}).get("message") if isinstance(data, dict) else None
            raise KeepaError(f"Keepa HTTP {resp.status_code}: {msg or resp.text[:200]}")
        return data

    def token_status(self) -> TokenState:
        self._get("/token", {})
        assert self.last_tokens is not None
        return self.last_tokens

    def products(self, country: str, *, codes: list[str] | None = None, asins: list[str] | None = None,
                 stats_days: int = 90, history: bool = True, days: int | None = 90) -> dict:
        if bool(codes) == bool(asins):
            raise ValueError("Entweder codes oder asins angeben")
        items = codes or asins or []
        if len(items) > MAX_CODES_PER_REQUEST:
            raise ValueError(f"Maximal {MAX_CODES_PER_REQUEST} Einträge je Anfrage")
        params: dict[str, Any] = {
            "domain": domain_id(country),
            "stats": stats_days,
            "history": 1 if history else 0,
        }
        if days and history:
            params["days"] = days
        params["code" if codes else "asin"] = ",".join(items)
        return self._get("/product", params)


# --------------------------------------------------------------------------- Parsing


def _price(v: Any) -> Decimal | None:
    if v is None:
        return None
    try:
        iv = int(v)
    except (TypeError, ValueError):
        return None
    if iv < 0:
        return None
    return (Decimal(iv) / 100).quantize(Decimal("0.01"))


def _stat(stats: dict | None, key: str, idx: int) -> Any:
    if not stats:
        return None
    arr = stats.get(key)
    if isinstance(arr, list) and len(arr) > idx:
        return arr[idx]
    return None


def history_series(csv_arr: list | None, triple: bool = False) -> list[tuple[datetime, Decimal]]:
    """Preisverlauf; bei Tripeln (Buy Box) wird Versand addiert."""
    if not csv_arr:
        return []
    step = 3 if triple else 2
    out = []
    for i in range(0, len(csv_arr) - step + 1, step):
        t, v = csv_arr[i], csv_arr[i + 1]
        if v is None or v < 0:
            continue
        if triple:
            ship = csv_arr[i + 2]
            v = v + (ship if ship and ship > 0 else 0)
        out.append((keepa_minutes_to_dt(t), Decimal(v) / 100))
    return out


@dataclass
class KeepaListing:
    asin: str
    country: str
    title: str | None
    brand: str | None
    manufacturer: str | None
    model: str | None
    mpn: str | None
    gtins: list[str]
    package_quantity: int | None
    number_of_items: int | None
    category: str | None
    image_urls: list[str]
    buybox_price: Decimal | None
    new_price: Decimal | None
    amazon_price: Decimal | None
    avg30_price: Decimal | None
    avg90_price: Decimal | None
    min90_price: Decimal | None
    price_cv90: Decimal | None
    sales_rank: int | None
    sales_rank_drops30: int | None
    sales_rank_drops90: int | None
    monthly_sold: int | None
    offer_count_new: int | None
    amazon_is_seller: bool | None
    history: dict[str, list[tuple[datetime, Decimal]]] = field(default_factory=dict)
    raw_meta: dict = field(default_factory=dict)


def _images(p: dict) -> list[str]:
    urls: list[str] = []
    if isinstance(p.get("images"), list):
        for img in p["images"]:
            name = img.get("l") or img.get("m") if isinstance(img, dict) else None
            if name:
                urls.append(f"https://m.media-amazon.com/images/I/{name}")
    elif p.get("imagesCSV"):
        urls = [f"https://m.media-amazon.com/images/I/{n}" for n in p["imagesCSV"].split(",") if n]
    return urls[:8]


def parse_product(p: dict, country: str, now: datetime | None = None) -> KeepaListing:
    stats = p.get("stats") or {}
    csv = p.get("csv") or []
    now = now or datetime.now(UTC)

    def cur(idx):
        return _stat(stats, "current", idx)

    buybox_hist = history_series(csv[IDX_BUY_BOX] if len(csv) > IDX_BUY_BOX else None, triple=True)
    new_hist = history_series(csv[IDX_NEW] if len(csv) > IDX_NEW else None)
    ref_hist = buybox_hist or new_hist
    cutoff = now - timedelta(days=90)
    last90 = [v for t, v in ref_hist if t >= cutoff]
    # Wert, der zu Beginn des Fensters galt, gehört ebenfalls dazu
    before = [v for t, v in ref_hist if t < cutoff]
    if before:
        last90.insert(0, before[-1])
    min90 = min(last90) if last90 else None
    cv = None
    if len(last90) >= 3:
        mean = statistics.fmean(float(x) for x in last90)
        if mean > 0:
            cv = Decimal(str(round(statistics.pstdev(float(x) for x in last90) / mean, 5)))

    gtins = [*(p.get("eanList") or []), *(p.get("upcList") or [])]
    tree = p.get("categoryTree") or []
    amazon_cur = cur(IDX_AMAZON)
    rank = cur(IDX_SALES)
    count_new = cur(IDX_COUNT_NEW)
    return KeepaListing(
        asin=p["asin"],
        country=country,
        title=p.get("title"),
        brand=p.get("brand"),
        manufacturer=p.get("manufacturer"),
        model=p.get("model"),
        mpn=p.get("partNumber"),
        gtins=[str(g) for g in gtins],
        package_quantity=p.get("packageQuantity") or None,
        number_of_items=p.get("numberOfItems") if (p.get("numberOfItems") or 0) > 0 else None,
        category=" > ".join(c.get("name", "") for c in tree) if tree else None,
        image_urls=_images(p),
        buybox_price=_price(cur(IDX_BUY_BOX)),
        new_price=_price(cur(IDX_NEW)),
        amazon_price=_price(amazon_cur),
        avg30_price=_price(_stat(stats, "avg30", IDX_BUY_BOX)) or _price(_stat(stats, "avg30", IDX_NEW)),
        avg90_price=_price(_stat(stats, "avg90", IDX_BUY_BOX)) or _price(_stat(stats, "avg90", IDX_NEW)),
        min90_price=min90.quantize(Decimal("0.01")) if min90 is not None else None,
        price_cv90=cv,
        sales_rank=rank if isinstance(rank, int) and rank > 0 else None,
        sales_rank_drops30=stats.get("salesRankDrops30") if (stats.get("salesRankDrops30") or -1) >= 0 else None,
        sales_rank_drops90=stats.get("salesRankDrops90") if (stats.get("salesRankDrops90") or -1) >= 0 else None,
        monthly_sold=p.get("monthlySold") if (p.get("monthlySold") or 0) > 0 else None,
        offer_count_new=count_new if isinstance(count_new, int) and count_new >= 0 else None,
        amazon_is_seller=(amazon_cur is not None and amazon_cur >= 0) if amazon_cur is not None else None,
        history={"buybox": buybox_hist[-200:], "new": new_hist[-200:]},
        raw_meta={k: p.get(k) for k in ("asin", "domainId", "lastUpdate", "lastPriceChange",
                                          "rootCategory", "productType", "parentAsin")},
    )
