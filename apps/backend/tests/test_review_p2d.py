"""P2d: Prüfung je Lieferantenartikel – Kandidat wählen, keiner passt, andere ASIN, Massenfreigabe."""

import json
from decimal import Decimal

import httpx

from sbc.config import get_settings
from sbc.integrations import keepa as k
from sbc.matching.engine import Decision
from sbc.models import AsinMapping, MatchingFeedback, MatchingResult, Opportunity
from tests.conftest import FIXTURES
from tests.test_matching_p2a import _listing, _match, _offer

FIXTURE = json.loads((FIXTURES / "keepa_product_FIXTURE.json").read_text())


def _two_candidates(db, org):
    _listing(db, org, title="Casio Collection Herren-Uhr MQ-24-7BLL", asin="B0TESTW001")
    _listing(db, org, title="Casio Collection Herren-Uhr MQ-24-7BLL, 2er Pack", qty=2, asin="B0TESTW002")
    o = _offer(db, org, "A")
    _match(db, o)
    db.commit()
    return o


def test_offer_queue_groups_candidates(client, db, org):
    o = _two_candidates(db, org)
    q = client.get("/api/v1/matching/offers").json()
    assert q["total"] == 1
    item = q["items"][0]
    assert item["offer"]["id"] == str(o.id) and len(item["candidates"]) == 2
    assert {c["listing"]["external_id"] for c in item["candidates"]} == {"B0TESTW001", "B0TESTW002"}
    assert client.get(f"/api/v1/matching/offers/{o.id}").status_code == 200


def test_choose_confirms_one_and_rejects_others(client, db, org):
    o = _two_candidates(db, org)
    cands = client.get(f"/api/v1/matching/offers/{o.id}").json()["candidates"]
    single = next(c for c in cands if c["listing"]["external_id"] == "B0TESTW001")
    r = client.post(f"/api/v1/matching/offers/{o.id}/choose", json={"matching_result_id": single["id"]})
    assert r.status_code == 200, r.text
    by_asin = {c["listing"]["external_id"]: c for c in r.json()["candidates"]}
    assert by_asin["B0TESTW001"]["decision_status"] == Decision.VERIFIED
    assert by_asin["B0TESTW002"]["decision_status"] == Decision.NO_MATCH
    db.expire_all()
    assert {m.status for m in db.query(AsinMapping)} == {"VERIFIED", "REJECTED"}
    assert db.query(MatchingFeedback).count() == 2


def test_choose_multipack_with_units(client, db, org):
    o = _two_candidates(db, org)
    cands = client.get(f"/api/v1/matching/offers/{o.id}").json()["candidates"]
    pack = next(c for c in cands if c["listing"]["external_id"] == "B0TESTW002")
    r = client.post(f"/api/v1/matching/offers/{o.id}/choose",
                    json={"matching_result_id": pack["id"], "units_required": 2, "reject_others": False})
    c = next(c for c in r.json()["candidates"] if c["id"] == pack["id"])
    assert c["classification"] == "MULTIPACK" and c["units_required"] == 2 and c["decision_status"] == Decision.VERIFIED


def test_none_fits_rejects_all_and_excludes_opportunities(client, db, org):
    o = _two_candidates(db, org)
    r = client.post(f"/api/v1/matching/offers/{o.id}/none", json={})
    assert all(c["decision_status"] == Decision.NO_MATCH for c in r.json()["candidates"])
    db.expire_all()
    assert all(op.is_excluded for op in db.query(Opportunity))
    assert client.get("/api/v1/matching/offers").json()["total"] == 0


def test_defer_keeps_review(client, db, org):
    o = _two_candidates(db, org)
    r = client.post(f"/api/v1/matching/offers/{o.id}/defer", json={})
    assert all(c["review_status"] == "deferred" for c in r.json()["candidates"])


def test_bulk_confirm_only_unambiguous(client, db, org):
    _two_candidates(db, org)
    ids = [str(m.id) for m in db.query(MatchingResult)]
    r = client.post("/api/v1/matching/bulk-confirm", json={"ids": ids}).json()
    assert len(r["confirmed"]) == 1  # nur der exakte 1:1-Treffer
    assert "MULTIPACK" in r["skipped"][0]["reason"]


def test_other_asin_via_keepa(client, db, org, monkeypatch):
    o = _offer(db, org, "A")
    db.commit()
    seen = []

    def handler(req):
        seen.append(dict(req.url.params))
        return httpx.Response(200, json=FIXTURE)

    real_init = k.KeepaClient.__init__
    monkeypatch.setattr(k.KeepaClient, "__init__", lambda self, key, http=None, timeout=60:
                        real_init(self, key, http=httpx.Client(transport=httpx.MockTransport(handler))))
    monkeypatch.setenv("KEEPA_API_KEY", "test-key")
    get_settings.cache_clear()
    try:
        r = client.post(f"/api/v1/matching/offers/{o.id}/asin", json={"asin": "B0FIXTURE1"})
    finally:
        get_settings.cache_clear()
    assert r.status_code == 200, r.text
    assert seen[0]["asin"] == "B0FIXTURE1"
    cand = r.json()["candidates"][0]
    assert cand["listing"]["external_id"] == "B0FIXTURE1" and cand["decision_status"] != Decision.VERIFIED
    assert client.post(f"/api/v1/matching/offers/{o.id}/asin", json={"asin": "NOTANASIN!"}).status_code == 422


def test_single_review_endpoint_still_works(client, db, org):
    o = _two_candidates(db, org)
    m = db.query(MatchingResult).filter(MatchingResult.supplier_offer_id == o.id).first()
    r = client.post(f"/api/v1/matching/{m.id}/review", json={"action": "reject"})
    assert r.status_code == 200 and r.json()["decision_status"] == Decision.NO_MATCH
    assert Decimal(r.json()["score"]) >= 0
