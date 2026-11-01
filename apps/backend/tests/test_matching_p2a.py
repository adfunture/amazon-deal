"""P2a: Entscheidungsstatus, erklärbarer Score, Konfliktsperren, dauerhafte ASIN-Zuordnungen."""

from decimal import Decimal

from sbc.integrations.keepa import KeepaListing
from sbc.integrations.keepa_service import upsert_listing
from sbc.matching import identity
from sbc.matching.engine import Decision, ItemView, compare, decide
from sbc.models import AsinMapping, MatchClass, MatchingResult, Supplier, SupplierOffer
from sbc.pipeline import candidate_listings, match_offer

V = ItemView.build


# --------------------------------------------------------------------------- Engine


def test_score_is_explainable_0_to_100():
    o = compare(V("Casio Collection MQ-24-7BLL Uhr", "Casio", ["4971850436140"], "MQ-24-7BLL"),
                V("Casio Collection Herren-Uhr MQ-24-7BLL", "Casio", ["4971850436140"], "MQ-24-7BLL"))
    assert 0 <= o.score_100 <= 100
    signals = {b["signal"] for b in o.breakdown}
    assert {"gtin", "mpn", "brand", "title"} <= signals
    assert abs(sum(b["points"] for b in o.breakdown) - o.score_100) <= 1 or o.score_100 == 100


def test_ean_hit_full_evidence_auto_verifies():
    o = compare(V("Casio Collection MQ-24-7BLL Uhr", "Casio", ["4971850436140"], "MQ-24-7BLL"),
                V("Casio Collection Uhr MQ-24-7BLL", "Casio", ["4971850436140"], "MQ-24-7BLL"))
    assert o.classification == MatchClass.EXACT_MATCH and not o.conflicts
    assert decide(o, o.score, False, Decimal("0.95"), 95) == Decision.VERIFIED


def test_ean_only_is_not_enough_without_calibration():
    o = compare(V("Uhr", None, ["4971850436140"]), V("Casio Herrenuhr", "Casio", ["4971850436140"]))
    assert o.score_100 < 95
    assert decide(o, o.score, False, Decimal("0.95"), 95) != Decision.VERIFIED


def test_missing_information_never_counts_positive():
    o = compare(V("Thule EasyFold"), V("Thule EasyFold XT 2 Fahrradträger", "Thule"))
    assert all(b["signal"] == "title" for b in o.breakdown if b["points"] > 0)
    assert o.score_100 <= 25


def test_same_model_different_color_blocks_verification():
    o = compare(V("Jack & Jones T-Shirt Black XL", "Jack & Jones", ["5713441234563"]),
                V("Jack & Jones T-Shirt Weiß XL", "Jack & Jones", ["5713441234563"]))
    assert "color" in o.conflicts
    assert decide(o, Decimal("1"), True, Decimal("0.95"), 95) == Decision.REVIEW


def test_different_sizes_block_verification():
    o = compare(V("Jack & Jones T-Shirt Schwarz XL", "Jack & Jones", ["5713441234563"]),
                V("Jack & Jones T-Shirt Schwarz Größe M", "Jack & Jones", ["5713441234563"]))
    assert "size" in o.conflicts and o.classification == MatchClass.VARIANT_MATCH
    assert decide(o, Decimal("1"), True, Decimal("0.95"), 95) == Decision.REVIEW


def test_single_vs_multipack_never_auto_verified():
    o = compare(V("Casio MQ-24-7BLL Uhr", "Casio", ["4971850436140"], "MQ-24-7BLL"),
                V("Casio MQ-24-7BLL Uhr 2er Pack", "Casio", ["4971850436140"], "MQ-24-7BLL", unit_quantity=2))
    assert o.classification == MatchClass.MULTIPACK and o.units_required == 2
    assert decide(o, Decimal("1"), True, Decimal("0.5"), 0) == Decision.REVIEW


def test_similar_but_not_identical_products():
    o = compare(V("Bosch Akkuschrauber GSR 12V-15", "Bosch"), V("Bosch Akkuschrauber GSR 18V-55", "Bosch"))
    assert decide(o, o.score, False, Decimal("0.95"), 95) != Decision.VERIFIED


def test_identity_conflicts_ignore_missing_values():
    a = {"gtin": "04006381333931", "colors": ["black"], "sizes": [], "brand": "stabilo"}
    assert identity.conflicts(a, {"gtin": None, "colors": [], "brand": "stabilo"}) == []
    assert identity.conflicts(a, {"gtin": "04006381333931", "colors": ["blue"]}) == ["colors"]


# --------------------------------------------------------------------------- Pipeline & Zuordnungen


def _listing(db, org, title="Casio Collection Herren-Uhr MQ-24-7BLL", qty=None, asin="B0TESTW001"):
    kl = KeepaListing(
        asin=asin, country="DE", title=title, brand="Casio", manufacturer="Casio", model=None, mpn="MQ-24-7BLL",
        gtins=["4971850436140"], package_quantity=qty, number_of_items=qty, category="TEST", image_urls=[],
        buybox_price=Decimal("39.90"), new_price=Decimal("39.90"), amazon_price=None, avg30_price=None,
        avg90_price=None, min90_price=None, price_cv90=None, sales_rank=None, sales_rank_drops30=10,
        sales_rank_drops90=30, monthly_sold=None, offer_count_new=3, amazon_is_seller=False, raw_meta={},
    )
    return upsert_listing(db, org.id, kl, is_test_data=True, source="fixture")


def _offer(db, org, code, title="Casio Uhr MQ-24-7BLL", brand="Casio"):
    from sbc.importer.normalize import ImportOptions, normalize_row
    from sbc.importer.service import _find_or_create_product

    sup = Supplier(org_id=org.id, code=code, name=f"Lieferant {code}", currency="EUR")
    db.add(sup)
    db.flush()
    n = normalize_row({"SKU": f"{code}-1", "EAN": "4971850436140", "Name": title, "Marke": brand, "EK": "15"},
                      {"SKU": "supplier_sku", "EAN": "gtin", "Name": "title", "Marke": "brand", "EK": "net_price"},
                      ImportOptions())
    product = _find_or_create_product(db, org.id, sup, n)
    offer = SupplierOffer(org_id=org.id, supplier_id=sup.id, offer_key=n.offer_key, product_id=product.id,
                          supplier_sku=n.supplier_sku, gtin=n.gtin, title=n.title, brand=n.brand,
                          unit_price_net=n.unit_price_net, content_hash=n.content_hash())
    db.add(offer)
    db.flush()
    return offer


def _match(db, offer):
    return match_offer(db, offer, candidate_listings(db, offer, "DE", include_test=True))


def test_manual_confirmation_is_reused_by_second_supplier(client, db, org):
    _listing(db, org)
    a = _offer(db, org, "A")
    (mr,) = _match(db, a)
    db.commit()
    assert mr.decision_status == Decision.REVIEW  # nur GTIN+Marke+Titel → manuell
    r = client.post(f"/api/v1/matching/{mr.id}/review", json={"action": "confirm"})
    assert r.status_code == 200 and r.json()["decision_status"] == Decision.VERIFIED
    mapping = db.query(AsinMapping).one()
    assert mapping.status == "VERIFIED" and mapping.source == "manual"

    b = _offer(db, org, "B")  # anderer Lieferant, gleiches Produkt (gleiche EAN und VPE)
    assert b.product_id == a.product_id
    (mr_b,) = _match(db, b)
    assert mr_b.stage == "known_mapping" and mr_b.decision_status == Decision.VERIFIED
    assert mr_b._needs_ai is False  # kein KI-Aufruf für bekannte Zuordnungen


def test_rejection_is_persistent(client, db, org):
    _listing(db, org)
    a = _offer(db, org, "A")
    (mr,) = _match(db, a)
    db.commit()
    client.post(f"/api/v1/matching/{mr.id}/review", json={"action": "reject"})
    db.expire_all()
    assert db.query(AsinMapping).one().status == "REJECTED"
    (mr_again,) = _match(db, a)  # erneuter Lauf (z. B. Re-Import) überschreibt die Ablehnung nicht
    assert mr_again.decision_status == Decision.NO_MATCH
    b = _offer(db, org, "B")
    (mr_b,) = _match(db, b)
    assert mr_b.decision_status == Decision.NO_MATCH and mr_b.stage == "known_mapping"


def test_identity_change_forces_new_review(client, db, org):
    _listing(db, org)
    a = _offer(db, org, "A", title="Casio Uhr MQ-24-7BLL schwarz")
    (mr,) = _match(db, a)
    db.commit()
    client.post(f"/api/v1/matching/{mr.id}/review", json={"action": "confirm"})
    db.expire_all()
    a = db.get(SupplierOffer, a.id)
    a.title = "Casio Uhr MQ-24-7BLL weiß"  # neue Preisliste: andere Farbe unter gleicher Artikelnummer
    (mr2,) = _match(db, a)
    assert mr2.decision_status == Decision.REVIEW and "identity_changed" in mr2.identity_conflicts
    assert db.query(AsinMapping).one().status == "REVIEW"


def test_multipack_needs_manual_confirmation_then_reused(client, db, org):
    _listing(db, org, title="Casio Collection Herren-Uhr MQ-24-7BLL, 2er Pack", qty=2)
    a = _offer(db, org, "A")
    (mr,) = _match(db, a)
    db.commit()
    assert mr.classification == MatchClass.MULTIPACK and mr.decision_status == Decision.REVIEW
    client.post(f"/api/v1/matching/{mr.id}/review", json={"action": "confirm"})
    db.expire_all()
    m = db.query(AsinMapping).one()
    assert m.relation == "MULTIPACK" and m.units_required == 2
    (mr_b,) = _match(db, _offer(db, org, "B"))
    assert mr_b.classification == MatchClass.MULTIPACK and mr_b.units_required == 2


def test_strong_exact_match_auto_creates_mapping(db, org):
    _listing(db, org, title="Casio Collection MQ-24-7BLL Uhr")
    a = _offer(db, org, "A", title="Casio Collection MQ-24-7BLL Uhr")
    a.mpn = "MQ-24-7BLL"
    (mr,) = _match(db, a)
    assert mr.decision_status == Decision.VERIFIED and mr.review_status.value == "auto_accepted"
    assert db.query(AsinMapping).one().source == "auto"


def test_queue_filters_by_decision(client, db, org):
    _listing(db, org)
    _match(db, _offer(db, org, "A"))
    db.commit()
    q = client.get("/api/v1/matching/queue", params={"decision": "MANUAL_REVIEW"}).json()
    assert q["total"] == 1 and q["items"][0]["score_breakdown"]
    assert db.query(MatchingResult).one().decision_status == Decision.REVIEW
