from decimal import Decimal

from sbc.matching.calibration import MIN_SAMPLES, _pav, build_bins, calibrate
from sbc.matching.engine import ItemView, compare, requires_review
from sbc.models import MatchClass

V = ItemView.build


def test_exact_match_by_ean():
    o = compare(V("Casio MQ-24-7BLL Uhr", "Casio", ["4971850436140"]),
                V("Casio Collection Herren-Uhr MQ-24-7BLL", "Casio", ["4971850436140"]))
    assert o.classification == MatchClass.EXACT_MATCH and o.units_required == 1


def test_same_ean_but_multipack():
    o = compare(V("Casio MQ-24-7BLL Uhr", "Casio", ["4971850436140"]),
                V("Casio MQ-24-7BLL Uhr, 2er Pack", "Casio", ["4971850436140"], unit_quantity=2))
    assert o.classification == MatchClass.MULTIPACK and o.units_required == 2


def test_supplier_pack_listing_single_not_integer():
    o = compare(V("Batterie AA 4er Pack", "Varta", ["4008496559848"]),
                V("Varta Batterie AA 6er Pack", "Varta", ["4008496559848"]))
    assert o.classification == MatchClass.REVIEW_REQUIRED and o.units_required is None


def test_color_variant():
    o = compare(V("Casio MQ-24 Uhr schwarz", "Casio", mpn="MQ-24"),
                V("Casio MQ-24 Uhr weiß", "Casio", mpn="MQ-24"))
    assert o.classification == MatchClass.VARIANT_MATCH


def test_brand_conflict_requires_review():
    o = compare(V("Ölfilter W 712/95", "MANN-FILTER", ["4011558013301"]),
                V("Ölfilter W 712/95 kompatibel", "NoName", ["4011558013301"]))
    assert o.classification == MatchClass.REVIEW_REQUIRED


def test_bundle_hint():
    o = compare(V("Casio MQ-24 Uhr", "Casio", ["4971850436140"]),
                V("Casio MQ-24 Uhr inkl. Geschenkbox und Ersatzarmband", "Casio", ["4971850436140"]))
    assert o.classification == MatchClass.BUNDLE and o.needs_ai


def test_mpn_in_title():
    o = compare(V("Ölfilter", "MANN-FILTER", mpn="W 712/95"),
                V("MANN-FILTER W 712/95 Ölfilter für VW", "MANN-FILTER"))
    assert o.matched.get("mpn_in_title") == "W71295"
    assert o.classification in (MatchClass.EXACT_MATCH, MatchClass.POSSIBLE_MATCH)


def test_no_match():
    o = compare(V("Stabilo Fineliner schwarz", "Stabilo"), V("Bosch Akkuschrauber 18V", "Bosch"))
    assert o.classification == MatchClass.NO_MATCH


def test_text_only_never_exact():
    o = compare(V("Thule EasyFold XT 2 Fahrradträger", "Thule"), V("Thule EasyFold XT 2 Fahrradträger", "Thule"))
    assert o.classification == MatchClass.POSSIBLE_MATCH and o.needs_ai


def test_condition_mismatch():
    o = compare(V("Casio Uhr", "Casio", ["4971850436140"], condition="used"),
                V("Casio Uhr", "Casio", ["4971850436140"]))
    assert o.classification == MatchClass.REVIEW_REQUIRED


def test_review_policy():
    assert requires_review(MatchClass.BUNDLE, Decimal("0.99"), Decimal("0.95"))
    assert requires_review(MatchClass.EXACT_MATCH, Decimal("0.90"), Decimal("0.95"))
    assert not requires_review(MatchClass.EXACT_MATCH, Decimal("0.97"), Decimal("0.95"))


def test_pav_monotone():
    assert _pav([(0.9, 1), (0.5, 1), (0.7, 1)]) == sorted(_pav([(0.9, 1), (0.5, 1), (0.7, 1)]))


def test_calibration_needs_samples():
    few = [(0.96, True)] * 5
    assert calibrate(Decimal("0.96"), build_bins(few)) == (Decimal("0.96"), False)
    many = [(0.96, True)] * (MIN_SAMPLES - 5) + [(0.97, False)] * 5
    conf, ok = calibrate(Decimal("0.96"), build_bins(many))
    assert ok and Decimal("0.7") < conf < Decimal("0.8")
