"""P2b: Mehrsprachiges Matching ohne KI-Übersetzung (DE, EN, FR, IT, ES, NL, PL)."""

import pytest
from rapidfuzz import fuzz

from sbc.domain.lang import canonical_text, canonical_tokens, concepts, detect_language
from sbc.domain.text import extract_attributes, extract_pack_quantity
from sbc.matching.engine import ItemView, compare
from sbc.models import MatchClass

V = ItemView.build


@pytest.mark.parametrize("title,lang", [
    ("Jack & Jones Men's T-Shirt Black XL", "en"),
    ("Jack & Jones Herren T-Shirt Schwarz Größe XL", "de"),
    ("Thule Porte-vélos pour 3 vélos", "fr"),
    ("Maglietta uomo nera taglia XL", "it"),
    ("Camiseta hombre negra talla XL", "es"),
    ("Heren T-shirt zwart maat XL", "nl"),
    ("Koszulka męska czarna rozmiar XL", "pl"),
])
def test_language_detection(title, lang):
    assert detect_language(title) == lang


def test_language_unknown_for_codes_only():
    assert detect_language("W 712/95") is None


@pytest.mark.parametrize("supplier,amazon", [
    ("Jack & Jones Men's T-Shirt Black XL", "Jack & Jones Herren T-Shirt Schwarz XL"),
    ("Thule Bike Carrier 3 Bikes", "Thule Fahrradträger für 3 Fahrräder"),
    ("Thule Porte-vélos pour 3 vélos", "Thule Fahrradträger für 3 Fahrräder"),
    ("MANN-FILTER Filtro olio W 712/95", "MANN-FILTER Ölfilter W 712/95"),
    ("Casio Montre homme noir MQ-24-7BLL", "Casio Herrenuhr schwarz MQ-24-7BLL"),
    ("Camiseta hombre negra XL Jack & Jones", "Jack & Jones Herren T-Shirt Schwarz XL"),
    ("Heren T-shirt zwart XL Jack & Jones", "Jack & Jones Herren T-Shirt Schwarz XL"),
    ("Koszulka męska czarna XL Jack & Jones", "Jack & Jones Herren T-Shirt Schwarz XL"),
])
def test_cross_language_titles_become_comparable(supplier, amazon):
    raw = fuzz.token_set_ratio(supplier.lower(), amazon.lower()) / 100
    can = fuzz.token_set_ratio(canonical_text(supplier, {"jack", "jones"}), canonical_text(amazon, {"jack", "jones"})) / 100
    assert can >= 0.9 and can > raw


def test_word_order_does_not_matter():
    a = canonical_tokens("Black XL T-Shirt Men's Jack & Jones")
    b = canonical_tokens("Jack & Jones Herren T-Shirt Schwarz XL")
    assert set(a) == set(b)


def test_brands_models_and_numbers_are_never_translated():
    toks = canonical_tokens("Thule EasyFold XT 3 Bike Carrier 934100", {"thule", "easyfold"})
    assert {"thule", "easyfold", "3", "934100"} <= set(toks)
    toks = canonical_tokens("MANN-FILTER Oil Filter W 712/95")
    assert "mann-filter" in toks and "712/95" in toks


def test_compound_words_are_split():
    assert concepts("Herrenuhr") == {"men", "watch"}
    assert canonical_tokens("Herrensocken grau")[:3] == ["c:men", "c:socks", "col:grey"]


def test_original_text_is_not_changed():
    title = "Jack & Jones Men's T-Shirt Black XL"
    canonical_text(title)
    assert title == "Jack & Jones Men's T-Shirt Black XL"


@pytest.mark.parametrize("title,qty", [
    ("Lot de 2 montres Casio", 2), ("Confezione da 3 filtri", 3), ("Paquete de 4 pilas", 4),
    ("Set van 2 fietsendragers", 2), ("Zestaw 2 szt. filtr", 2), ("Filtre 2 pièces", 2), ("Filtro olio W 712/95", None),
])
def test_multilingual_pack_quantities(title, qty):
    assert extract_pack_quantity(title)[0] == qty


def test_multilingual_colors_sizes_materials():
    a = extract_attributes("Koszulka męska czarna rozmiar M bawełna")
    assert a.colors == {"black"} and a.sizes == {"M"} and a.materials == {"cotton"}
    assert extract_attributes("Coffret montre avec bracelet et étui").bundle_hint


# --------------------------------------------------------------------------- Engine


def test_english_supplier_matches_german_amazon_by_ean():
    o = compare(V("Jack & Jones Men's T-Shirt Black XL", "Jack & Jones", ["5713441234563"]),
                V("Jack & Jones Herren T-Shirt Schwarz XL", "Jack & Jones", ["5713441234563"]))
    assert o.classification == MatchClass.EXACT_MATCH and not o.conflicts
    assert o.matched["languages"] == {"supplier": "en", "listing": "de"}
    title = next(b for b in o.breakdown if b["signal"] == "title")
    assert "sprachübergreifend" in title["detail"] and title["points"] >= 22


def test_english_color_differs_from_german_listing():
    o = compare(V("Jack & Jones Men's T-Shirt White XL", "Jack & Jones", ["5713441234563"]),
                V("Jack & Jones Herren T-Shirt Schwarz XL", "Jack & Jones", ["5713441234563"]))
    assert "color" in o.conflicts


def test_material_conflict_blocks():
    o = compare(V("Thule Fahrradträger Aluminium", "Thule", ["7313020073453"]),
                V("Thule Bike Carrier Steel", "Thule", ["7313020073453"]))
    assert "material" in o.conflicts and o.classification == MatchClass.VARIANT_MATCH


def test_text_only_cross_language_is_possible_never_exact():
    o = compare(V("Thule Bike Carrier 3 Bikes EasyFold", "Thule"),
                V("Thule EasyFold Fahrradträger für 3 Fahrräder", "Thule"))
    assert o.classification in (MatchClass.POSSIBLE_MATCH, MatchClass.VARIANT_MATCH)
    assert o.classification != MatchClass.EXACT_MATCH and o.needs_ai


def test_french_multipack_against_german_single():
    o = compare(V("Casio Montre MQ-24-7BLL", "Casio", ["4971850436140"]),
                V("Casio Uhr MQ-24-7BLL, Lot de 2", "Casio", ["4971850436140"]))
    assert o.classification == MatchClass.MULTIPACK and o.units_required == 2
