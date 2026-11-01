from dataclasses import replace
from decimal import Decimal as D

from sbc.profit.engine import (
    CostInputs,
    PricePoints,
    calculate,
    capital_metrics,
    max_buy_price,
    run_scenarios,
)


def base(**kw):
    defaults = dict(
        sell_price_gross=D("119.00"), vat_rate=D("0.19"), unit_cost_net=D("50.00"), units_required=1,
        inbound_shipping_per_unit=D("1.00"), referral_rate=D("0.15"), referral_min=D("0.30"),
        fulfillment_fee=D("5.00"), storage_cost_per_month=D("0.50"), packaging_cost=D("0.50"),
        return_rate=D("0.05"), return_cost=D("10.00"), ad_cost_rate=D("0.00"),
    )
    defaults.update(kw)
    return CostInputs(**defaults)


def test_hand_calculated_example():
    r = calculate(base()).result
    # Netto 100; Ware 51; Provision 17,85 (15 % von 119); FBA 5; Lager .5; Verp .5; Retoure .5
    assert r.net_revenue == D("100.0000")
    assert r.vat_amount == D("19.0000")
    assert r.referral_fee == D("17.8500")
    assert r.goods_cost == D("51.0000")
    assert r.contribution == D("24.6500")
    assert r.contribution_margin == D("0.2465")
    assert r.roi == D("0.4833")


def test_vat_is_not_a_cost():
    r = calculate(base()).result
    assert "umsatzsteuer" not in r.cost_breakdown
    assert r.total_costs + r.contribution == r.net_revenue


def test_break_even_gives_zero_contribution():
    calc = calculate(base())
    be = calc.result.break_even_price_gross
    r2 = calculate(replace(base(), sell_price_gross=be)).result
    assert abs(r2.contribution) < D("0.01")


def test_break_even_with_min_referral():
    i = base(sell_price_gross=D("3.00"), unit_cost_net=D("0.10"), inbound_shipping_per_unit=D("0"),
             fulfillment_fee=D("0.50"), referral_min=D("0.30"), storage_cost_per_month=D("0"),
             packaging_cost=D("0"), return_rate=D("0"), return_cost=D("0"))
    be = calculate(i).result.break_even_price_gross
    assert abs(calculate(replace(i, sell_price_gross=be)).result.contribution) < D("0.01")


def test_max_buy_price_hits_target():
    i = base()
    mbp = max_buy_price(i, min_margin=D("0.12"), min_contribution=D("5"))
    r = calculate(replace(i, unit_cost_net=mbp)).result
    assert abs(r.contribution_margin - D("0.12")) < D("0.0002") or abs(r.contribution - D("5")) < D("0.01")
    assert r.contribution >= D("4.99") and r.contribution_margin >= D("0.1199")


def test_missing_required_returns_no_result():
    c = calculate(base(fulfillment_fee=None))
    assert c.result is None and "Versand-/Fulfillmentkosten" in c.missing_required


def test_missing_optional_flagged_incomplete():
    c = calculate(base(return_rate=None, ad_cost_rate=None))
    assert c.result is not None and not c.is_complete
    assert "Retourenquote" in c.missing_optional and "Werbekosten" in c.missing_optional


def test_multipack_casio_example():
    # Lieferant: Einzelstück EK 20 €; Amazon: 2er-Pack 69,90 €
    i = base(sell_price_gross=D("69.90"), unit_cost_net=D("20.00"), units_required=2,
             inbound_shipping_per_unit=D("0"), bundling_cost=D("0.50"), packaging_cost=D("0.40"),
             fulfillment_fee=D("3.50"), storage_cost_per_month=D("0.10"), return_rate=D("0.03"),
             return_cost=D("5"))
    r = calculate(i).result
    assert r.goods_cost == D("40.0000")  # Bundle-EK
    assert r.cost_breakdown["konfektionierung"] == D("0.5000")
    expected = D("69.90") / D("1.19") - D("40") - D("69.90") * D("0.15") - D("3.50") - D("0.10") - D("0.40") - D("0.50") - D("0.15")
    assert r.contribution == expected.quantize(D("0.0001"))


def test_fx_and_duty():
    i = base(unit_cost_net=D("100"), fx_rate=D("0.5"), import_duty_rate=D("0.10"), inbound_shipping_per_unit=D("0"))
    assert calculate(i).result.goods_cost == D("55.0000")


def test_scenarios_without_history_are_none():
    sc = run_scenarios(base(), PricePoints(current=D("119.00")))
    assert sc["ref_30d"] is None and sc["ref_90d"] is None
    assert sc["drop_5"].inputs.sell_price_gross == D("113.0500")
    assert sc["conservative"].inputs.sell_price_gross == D("119.00")


def test_scenarios_conservative_uses_minimum():
    sc = run_scenarios(base(), PricePoints(current=D("119"), avg30=D("115"), avg90=D("110"), min90=D("105")))
    assert sc["conservative"].inputs.sell_price_gross == D("105")


def test_capital_metrics():
    calc = calculate(base())
    cm = capital_metrics(calc, quantity=20, monthly_units=D("10"), lead_time_days=30)
    assert cm.capital_required == D("1020.0000")
    # gebunden: 1020*1 + 510*2 = 2040 €·Monate; DB 24.65*20 = 493 → 241.67 je 1000 €/Monat
    assert cm.capital_binding_months == D("2040.0000")
    assert cm.contribution_per_1000_per_month == D("241.6667")
    assert capital_metrics(calc, 20, None, 30).contribution_per_1000_per_month is None
