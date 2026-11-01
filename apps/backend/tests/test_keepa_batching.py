"""Keepa: gebündelte Abfragen je Analyse-Job und keine Secrets in Logs."""

import json
import logging

import httpx

from sbc.config import get_settings
from sbc.integrations import keepa as k
from sbc.logging_setup import RedactSecrets
from sbc.models import Supplier, SupplierOffer
from tests.conftest import FIXTURES

FIXTURE = json.loads((FIXTURES / "keepa_product_FIXTURE.json").read_text())


def test_redaction_masks_keepa_key():
    rec = logging.LogRecord("httpx", logging.INFO, __file__, 1,
                            'HTTP Request: GET https://api.keepa.com/product?key=SECRET123&domain=3 "200"', None, None)
    RedactSecrets().filter(rec)
    assert "SECRET123" not in rec.getMessage() and "key=***" in rec.getMessage()


def test_httpx_url_logging_disabled():
    import sbc.logging_setup  # noqa: F401

    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


def test_analyze_job_batches_keepa_requests(db, org, monkeypatch):
    from sbc.worker import tasks

    sup = Supplier(org_id=org.id, code="B", name="B")
    db.add(sup)
    db.flush()
    ids = []
    for i, gtin in enumerate(["04011558013301", "04006381333931", "04971850436140"]):
        o = SupplierOffer(org_id=org.id, supplier_id=sup.id, offer_key=f"K{i}", gtin=gtin, title=f"T{i}",
                          unit_price_net=1, content_hash="x")
        db.add(o)
        db.flush()
        ids.append(str(o.id))
    db.commit()

    calls = []

    def handler(req: httpx.Request):
        calls.append(req.url.params["code"])
        return httpx.Response(200, json=FIXTURE)

    real_init = k.KeepaClient.__init__

    def fake_init(self, api_key, http=None, timeout=60):
        real_init(self, api_key, http=httpx.Client(transport=httpx.MockTransport(handler)))

    monkeypatch.setattr(k.KeepaClient, "__init__", fake_init)
    monkeypatch.setenv("KEEPA_API_KEY", "test-key")
    get_settings.cache_clear()
    try:
        tasks.analyze_offers.fn(ids, True)
    finally:
        get_settings.cache_clear()
    assert len(calls) == 1 and len(calls[0].split(",")) == 3  # ein Request für alle EANs
