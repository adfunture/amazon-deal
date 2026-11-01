"""TESTDATEN für Demo-Ansichten. Alle Listings tragen is_test_data=True / source='fixture'.

Die Oberfläche blendet Testdaten standardmäßig aus und kennzeichnet sie als TESTDATEN.
Die Marktdaten unten sind ERFUNDEN und dürfen nicht für Einkaufsentscheidungen genutzt werden.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from sbc.importer.normalize import ImportOptions, normalize_row
from sbc.importer.service import _find_or_create_product
from sbc.integrations.keepa import KeepaListing
from sbc.integrations.keepa_service import upsert_listing
from sbc.models import Organization, Supplier, SupplierOffer
from sbc.pipeline import analyze_offer

DEMO_SUPPLIER = "DEMO-TEST"

OFFERS = [
    {"SKU": "DEMO-W1", "EAN": "4971850436140", "Marke": "Casio", "Bezeichnung": "Casio Collection Uhr MQ-24-7BLL",
     "EK": "20,00", "Bestand": 120},
    {"SKU": "DEMO-F1", "EAN": "4011558013301", "Marke": "MANN-FILTER", "Bezeichnung": "MANN-FILTER Ölfilter W 712/95",
     "EK": "3,10", "Bestand": 800},
    {"SKU": "DEMO-X1", "EAN": "4006381333931", "Marke": "Stabilo", "Bezeichnung": "STABILO point 88 Fineliner schwarz",
     "EK": "0,45", "Bestand": 0},
]


def _kl(asin, title, brand, gtins, price, avg30, avg90, drops30, offers, qty=None, mpn=None):
    return KeepaListing(
        asin=asin, country="DE", title=title, brand=brand, manufacturer=brand, model=None, mpn=mpn,
        gtins=gtins, package_quantity=qty, number_of_items=qty, category="TESTDATEN", image_urls=[],
        buybox_price=Decimal(price), new_price=Decimal(price), amazon_price=None,
        avg30_price=Decimal(avg30), avg90_price=Decimal(avg90), min90_price=Decimal(avg90) * Decimal("0.95"),
        price_cv90=Decimal("0.04"), sales_rank=1500, sales_rank_drops30=drops30, sales_rank_drops90=drops30 * 3,
        monthly_sold=None, offer_count_new=offers, amazon_is_seller=False, raw_meta={"fixture": True},
    )


LISTINGS = [
    _kl("B0DEMO0001", "Casio Collection Herren-Uhr MQ-24-7BLL, 2er Pack", "Casio", ["4971850436140"],
        "89.90", "88.50", "87.00", 40, 4, qty=2, mpn="MQ-24-7BLL"),
    _kl("B0DEMO0002", "MANN-FILTER W 712/95 Ölfilter", "MANN-FILTER", ["4011558013301"],
        "12.99", "12.49", "12.20", 120, 9, mpn="W 712/95"),
    _kl("B0DEMO0003", "STABILO point 88 Fineliner 10er Etui", "Stabilo", ["4006381333931"],
        "9.99", "9.80", "9.70", 300, 25, qty=10),
]


def load(db: Session) -> dict:
    org = db.scalar(select(Organization).order_by(Organization.created_at))
    if org is None:
        raise RuntimeError("Zuerst bootstrap ausführen")
    sup = db.scalar(select(Supplier).where(Supplier.org_id == org.id, Supplier.code == DEMO_SUPPLIER))
    if sup is None:
        sup = Supplier(org_id=org.id, code=DEMO_SUPPLIER, name="DEMO – Testlieferant (TESTDATEN)",
                       currency="EUR", shipping_cost_per_unit=Decimal("0.30"), lead_time_days=5,
                       notes="Automatisch erzeugte Testdaten")
        db.add(sup)
        db.flush()
    for kl in LISTINGS:
        upsert_listing(db, org.id, kl, is_test_data=True, source="fixture")
    mapping = {"SKU": "supplier_sku", "EAN": "gtin", "Marke": "brand", "Bezeichnung": "title",
               "EK": "net_price", "Bestand": "stock"}
    results = []
    for raw in OFFERS:
        n = normalize_row(raw, mapping, ImportOptions())
        offer = db.scalar(select(SupplierOffer).where(SupplierOffer.supplier_id == sup.id,
                                                      SupplierOffer.offer_key == n.offer_key))
        if offer is None:
            product = _find_or_create_product(db, org.id, sup, n)
            offer = SupplierOffer(org_id=org.id, supplier_id=sup.id, offer_key=n.offer_key,
                                  product_id=product.id, content_hash=n.content_hash(), raw=raw)
            db.add(offer)
        offer.supplier_sku, offer.gtin, offer.title, offer.brand = n.supplier_sku, n.gtin, n.title, n.brand
        offer.unit_price_net, offer.stock_qty, offer.lead_time_days = n.unit_price_net, n.stock_qty, 5
        db.flush()
        results.append(analyze_offer(db, offer, use_keepa=False, include_test=True))
    return {"supplier": str(sup.id), "results": results}
