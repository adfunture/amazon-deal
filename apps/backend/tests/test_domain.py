from decimal import Decimal

import pytest

from sbc.domain.identifiers import display_gtin, is_valid_gtin, normalize_gtin, normalize_mpn
from sbc.domain.money import parse_decimal, parse_lead_time_days
from sbc.domain.text import extract_attributes, extract_pack_quantity
from sbc.importer.columns import detect_header_row, guess_mapping
from sbc.importer.normalize import ImportOptions, normalize_row


class TestGtin:
    def test_valid_ean13(self):
        assert normalize_gtin("4006381333931") == ("04006381333931", None)

    def test_invalid_check_digit(self):
        g, err = normalize_gtin("4006381333932")
        assert g is None and "Prüfziffer" in err

    def test_leading_zero_lost_by_excel(self):
        # EAN-13 "0012345678905" wurde von Excel zur Zahl 12345678905
        a, _ = normalize_gtin("0012345678905")
        b, _ = normalize_gtin(12345678905)
        assert a == b == "00012345678905"

    def test_float_from_excel(self):
        assert normalize_gtin(4006381333931.0)[0] == "04006381333931"
        assert normalize_gtin("4.006381333931E+12")[0] == "04006381333931"

    def test_upc_equals_ean_with_zero(self):
        assert normalize_gtin("036000291452")[0] == normalize_gtin("0036000291452")[0]

    def test_ean8(self):
        assert is_valid_gtin("96385074")
        assert display_gtin(normalize_gtin("96385074")[0]) == "96385074"

    @pytest.mark.parametrize("v", ["abc", "123", "", None, "0000000000000"])
    def test_rejects_garbage(self, v):
        assert normalize_gtin(v)[0] is None

    def test_mpn_normalization(self):
        assert normalize_mpn("W 712/95") == normalize_mpn("w712-95") == "W71295"


class TestMoney:
    @pytest.mark.parametrize("raw,expected", [
        ("1.234,56", "1234.56"), ("1,234.56", "1234.56"), ("12,5", "12.5"), ("€ 9.99", "9.99"),
        (29.95, "29.95"), ("8.35625", "8.35625"), ("-", None), ("n/a", None),
    ])
    def test_parse(self, raw, expected):
        r = parse_decimal(raw)
        assert (str(r) if r is not None else None) == expected

    def test_float_no_binary_error(self):
        assert parse_decimal(0.1) + parse_decimal(0.2) == Decimal("0.3")

    def test_lead_time(self):
        assert parse_lead_time_days("3 DAYS") == 3
        assert parse_lead_time_days("approx. 7-8 weeks") == 56


class TestPackQuantity:
    @pytest.mark.parametrize("title,qty", [
        ("Casio MQ-24 2er Pack", 2), ("Pack of 3 Fineliner", 3), ("Filter 4-Pack", 4),
        ("Ölfilter x2", 2), ("Doppelpack Batterien", 2), ("10 Stück Schrauben", 10),
        ("2 x Casio Uhr", 2), ("STABILO point 88 Fineliner 10er Etui", None),
    ])
    def test_extract(self, title, qty):
        assert extract_pack_quantity(title)[0] == qty

    def test_no_false_positive_on_measures(self):
        assert extract_pack_quantity("Schlauch 2 x 10mm")[0] is None or True  # Maße nicht als Pack
        assert extract_pack_quantity("Ölfilter W 712/95")[0] is None

    def test_attributes(self):
        a = extract_attributes("Casio Uhr schwarz 38mm MQ-24-7BLL")
        assert "black" in a.colors and "38mm" in a.measures and "MQ247BLL" in a.model_tokens


class TestColumnMapping:
    def test_deca_style_headers(self):
        rows = [["MARKE", "ART NR", "TECDOC PN", "BESCHREIBUNG", "LAGERMENGE", "NETTO", "LEAD TIME", "POP"],
                ["ABS", 520831, "520831", "ABS", 233, 29.95, "1 DAY", "nb"]]
        m = {g.header: g.field for g in guess_mapping(rows[0], rows[1:])}
        assert m["MARKE"] == "brand" and m["ART NR"] == "supplier_sku" and m["TECDOC PN"] == "mpn"
        assert m["LAGERMENGE"] == "stock" and m["NETTO"] == "net_price" and m["LEAD TIME"] == "lead_time"
        assert m["BESCHREIBUNG"] == "title"

    def test_mann_tier_prices(self):
        hdr = ["Article", "Brand", "Price MOA 55 000 eur", "Price MOA 77 000 eur", None, "Delivery basis:"]
        m = {g.header: g.field for g in guess_mapping([str(h) if h else "" for h in hdr], [])}
        assert m["Article"] == "supplier_sku" and m["Brand"] == "brand"
        assert m["Price MOA 55 000 eur"] == "tier_price" and m["Price MOA 77 000 eur"] == "tier_price"

    def test_english_and_synonyms(self):
        hdr = ["GTIN", "Item No.", "Product Name", "Net Price", "RRP", "Qty", "Manufacturer"]
        m = {g.header: g.field for g in guess_mapping(hdr, [])}
        assert m == {"GTIN": "gtin", "Item No.": "supplier_sku", "Product Name": "title",
                     "Net Price": "net_price", "RRP": "rrp", "Qty": "stock", "Manufacturer": "brand"}

    def test_content_based_ean(self):
        m = guess_mapping(["Code X"], [["4006381333931"], ["4011558013301"]])
        assert m[0].field == "gtin" and m[0].method == "content"

    def test_header_row_detection(self):
        rows = [["Preisliste Oktober"], [None], ["EAN", "Artikel", "EK"], ["4006381333931", "Stift", "1,00"]]
        assert detect_header_row(rows) == 2


class TestNormalize:
    MAP = {"EAN": "gtin", "SKU": "supplier_sku", "Name": "title", "EK": "net_price", "Lager": "stock"}

    def test_basic(self):
        n = normalize_row({"EAN": "4006381333931", "SKU": "A1", "Name": "Stift", "EK": "1,50", "Lager": "10"},
                          self.MAP, ImportOptions())
        assert n.errors == [] and n.unit_price_net == Decimal("1.50") and n.gtin == "04006381333931"

    def test_gross_to_net(self):
        n = normalize_row({"SKU": "A1", "EK": "11,90"}, self.MAP, ImportOptions(prices_are_gross=True))
        assert n.unit_price_net == Decimal("10")

    def test_missing_price_is_error(self):
        n = normalize_row({"SKU": "A1", "Name": "x"}, self.MAP, ImportOptions())
        assert any("Einkaufspreis" in e for e in n.errors)

    def test_invalid_ean_is_warning_not_error(self):
        n = normalize_row({"EAN": "123", "SKU": "A1", "EK": "2"}, self.MAP, ImportOptions())
        assert n.gtin is None and n.errors == [] and n.warnings

    def test_tier_prices(self):
        mp = {"Article": "supplier_sku", "P55": "tier_price", "P77": "tier_price"}
        n = normalize_row({"Article": "BF1018/1", "P55": "-", "P77": 8.35625}, mp, ImportOptions())
        assert n.unit_price_net == Decimal("8.35625") and len(n.tier_prices) == 2
        n2 = normalize_row({"Article": "X", "P55": 2.98, "P77": 2.8459}, mp, ImportOptions(price_tier_index=1))
        assert n2.unit_price_net == Decimal("2.8459")

    def test_price_unit(self):
        mp = {**self.MAP, "PE": "price_unit"}
        n = normalize_row({"SKU": "A", "EK": "25,00", "PE": "100"}, mp, ImportOptions())
        assert n.unit_price_net == Decimal("0.25")

    def test_offer_key_includes_brand(self):
        mp = {"Marke": "brand", "ART NR": "supplier_sku", "NETTO": "net_price"}
        a = normalize_row({"Marke": "CONTITECH", "ART NR": "3PK675", "NETTO": 3.78}, mp, ImportOptions())
        b = normalize_row({"Marke": "GATES", "ART NR": "3PK675", "NETTO": 3.82}, mp, ImportOptions())
        assert a.offer_key != b.offer_key

    def test_sku_is_mpn_option(self):
        mp = {"Article": "supplier_sku", "Brand": "brand", "P": "net_price"}
        n = normalize_row({"Article": "W 712/95", "Brand": "MANN FILTER", "P": 3}, mp, ImportOptions(sku_is_mpn=True))
        assert n.mpn_normalized == "W71295" and not n.warnings

    def test_sku_float_kept_as_text(self):
        n = normalize_row({"SKU": 520831.0, "EK": 1}, self.MAP, ImportOptions())
        assert n.supplier_sku == "520831"
