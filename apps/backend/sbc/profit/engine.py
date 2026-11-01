"""Deterministische Kalkulations-Engine.

Konventionen
------------
* Alle Beträge sind ``Decimal`` in der Zielwährung (Standard EUR). Fremdwährungen werden
  VOR der Kalkulation mit einem dokumentierten Kurs umgerechnet (``fx_rate`` im Snapshot).
* Verkaufspreise am Marktplatz sind Bruttopreise (inkl. USt). Die USt ist KEIN Kostenpunkt:
  Nettoerlös = Brutto / (1 + USt-Satz). Gebühren werden netto angesetzt (Vorsteuerabzug).
* Prozentuale Gebühren (Provision, Zahlung, Werbung) beziehen sich auf den Bruttopreis –
  so berechnen Amazon und eBay ihre Provisionen.
* Fehlende Pflichtwerte → keine Ergebnisberechnung (``result=None``) mit Begründung.
  Fehlende optionale Kosten → Berechnung mit Kennzeichnung ``is_complete=False``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

ENGINE_VERSION = "profit-1.0.0"

ZERO = Decimal(0)
ONE = Decimal(1)
_Q = Decimal("0.0001")


def _q(x: Decimal) -> Decimal:
    return x.quantize(_Q, rounding=ROUND_HALF_UP)


@dataclass
class CostInputs:
    # Verkauf
    sell_price_gross: Decimal | None
    vat_rate: Decimal | None
    # Einkauf (je Lieferanteneinheit, netto, bereits in Zielwährung oder mit fx_rate)
    unit_cost_net: Decimal | None
    units_required: int | None = 1  # Lieferanteneinheiten je verkaufter Marktplatzeinheit
    fx_rate: Decimal = ONE  # Zielwährung je 1 Einheit Lieferantenwährung
    supplier_discount_rate: Decimal | None = ZERO
    inbound_shipping_per_unit: Decimal | None = None
    import_duty_rate: Decimal | None = ZERO
    # Marktplatz
    referral_rate: Decimal | None = None
    referral_min: Decimal | None = ZERO
    fixed_fee: Decimal | None = ZERO
    fulfillment_fee: Decimal | None = None  # FBA-Gebühr bzw. FBM-Versandkosten
    storage_cost_per_month: Decimal | None = None
    expected_storage_months: Decimal = ONE
    packaging_cost: Decimal | None = None
    bundling_cost: Decimal | None = ZERO  # Konfektionierung je Bundle (nur wenn units_required>1)
    payment_fee_rate: Decimal | None = ZERO
    return_rate: Decimal | None = None
    return_cost: Decimal | None = None
    ad_cost_rate: Decimal | None = None
    other_cost: Decimal | None = ZERO

    def snapshot(self) -> dict[str, Any]:
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(self).items()}


REQUIRED = {
    "sell_price_gross": "Verkaufspreis",
    "vat_rate": "USt-Satz des Zielmarktes",
    "unit_cost_net": "Einkaufspreis",
    "units_required": "Verkaufseinheit (benötigte Stückzahl je Angebot)",
    "referral_rate": "Marktplatzprovision",
    "fulfillment_fee": "Versand-/Fulfillmentkosten",
}
OPTIONAL = {
    "inbound_shipping_per_unit": "Lieferantenversand je Stück",
    "storage_cost_per_month": "Lagerkosten",
    "packaging_cost": "Verpackung",
    "return_rate": "Retourenquote",
    "return_cost": "Retourenkosten",
    "ad_cost_rate": "Werbekosten",
}


@dataclass
class CalcResult:
    net_revenue: Decimal
    vat_amount: Decimal
    goods_cost: Decimal  # Wareneinsatz je verkaufter Einheit (landed)
    referral_fee: Decimal
    fulfillment_fee: Decimal
    other_variable_costs: Decimal
    total_costs: Decimal
    contribution: Decimal  # Deckungsbeitrag je Stück
    contribution_margin: Decimal | None  # DB / Nettoerlös
    roi: Decimal | None  # DB / Wareneinsatz
    break_even_price_gross: Decimal | None
    cost_breakdown: dict[str, Decimal] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        out = {}
        for k, v in asdict(self).items():
            if isinstance(v, Decimal):
                out[k] = str(v)
            elif isinstance(v, dict):
                out[k] = {kk: str(vv) for kk, vv in v.items()}
            else:
                out[k] = v
        return out


@dataclass
class Calculation:
    inputs: CostInputs
    result: CalcResult | None
    missing_required: list[str]
    missing_optional: list[str]

    @property
    def is_complete(self) -> bool:
        return self.result is not None and not self.missing_optional

    @property
    def missing(self) -> list[str]:
        return [*self.missing_required, *self.missing_optional]


def _v(x: Decimal | None) -> Decimal:
    return ZERO if x is None else x


def landed_unit_cost(i: CostInputs) -> Decimal:
    """Einstandspreis je Lieferanteneinheit in Zielwährung."""
    base = _v(i.unit_cost_net) * (ONE - _v(i.supplier_discount_rate)) * i.fx_rate
    return base * (ONE + _v(i.import_duty_rate)) + _v(i.inbound_shipping_per_unit)


def calculate(i: CostInputs) -> Calculation:
    missing_req = [label for k, label in REQUIRED.items() if getattr(i, k) is None]
    missing_opt = [label for k, label in OPTIONAL.items() if getattr(i, k) is None]
    if i.units_required is not None and i.units_required < 1:
        missing_req.append("Verkaufseinheit (ungültig)")
    if missing_req:
        return Calculation(i, None, missing_req, missing_opt)

    assert i.sell_price_gross is not None and i.vat_rate is not None
    assert i.referral_rate is not None and i.fulfillment_fee is not None
    units = i.units_required or 1
    p = i.sell_price_gross
    net_revenue = p / (ONE + i.vat_rate)
    vat_amount = p - net_revenue

    goods = landed_unit_cost(i) * units
    referral = max(p * i.referral_rate, _v(i.referral_min))
    pct_other = p * (_v(i.payment_fee_rate) + _v(i.ad_cost_rate))
    storage = _v(i.storage_cost_per_month) * i.expected_storage_months
    returns = _v(i.return_rate) * _v(i.return_cost)
    bundling = _v(i.bundling_cost) if units > 1 else ZERO
    fixed_other = _v(i.fixed_fee) + storage + _v(i.packaging_cost) + bundling + returns + _v(i.other_cost)
    other = pct_other + fixed_other
    total = goods + referral + i.fulfillment_fee + other
    contribution = net_revenue - total

    breakdown = {
        "wareneinsatz": goods,
        "provision": referral,
        "fulfillment_versand": i.fulfillment_fee,
        "zahlungsgebuehr": p * _v(i.payment_fee_rate),
        "werbung": p * _v(i.ad_cost_rate),
        "lager": storage,
        "verpackung": _v(i.packaging_cost),
        "konfektionierung": bundling,
        "retouren": returns,
        "fixgebuehr": _v(i.fixed_fee),
        "sonstiges": _v(i.other_cost),
    }

    result = CalcResult(
        net_revenue=_q(net_revenue),
        vat_amount=_q(vat_amount),
        goods_cost=_q(goods),
        referral_fee=_q(referral),
        fulfillment_fee=_q(i.fulfillment_fee),
        other_variable_costs=_q(other),
        total_costs=_q(total),
        contribution=_q(contribution),
        contribution_margin=_q(contribution / net_revenue) if net_revenue > 0 else None,
        roi=_q(contribution / goods) if goods > 0 else None,
        break_even_price_gross=break_even_price(i),
        cost_breakdown={k: _q(v) for k, v in breakdown.items()},
    )
    return Calculation(i, result, [], missing_opt)


def _proportional_rate(i: CostInputs) -> Decimal:
    """Anteil des Bruttopreises, der als Nettoerlös nach prozentualen Kosten übrig bleibt."""
    assert i.vat_rate is not None and i.referral_rate is not None
    return ONE / (ONE + i.vat_rate) - i.referral_rate - _v(i.payment_fee_rate) - _v(i.ad_cost_rate)


def _fixed_costs(i: CostInputs, include_goods: bool = True) -> Decimal:
    units = i.units_required or 1
    storage = _v(i.storage_cost_per_month) * i.expected_storage_months
    bundling = _v(i.bundling_cost) if units > 1 else ZERO
    fixed = (
        _v(i.fulfillment_fee) + _v(i.fixed_fee) + storage + _v(i.packaging_cost) + bundling
        + _v(i.return_rate) * _v(i.return_cost) + _v(i.other_cost)
    )
    if include_goods:
        fixed += landed_unit_cost(i) * units
    return fixed


def break_even_price(i: CostInputs) -> Decimal | None:
    """Bruttopreis, bei dem DB = 0 (berücksichtigt Mindestprovision)."""
    if i.vat_rate is None or i.referral_rate is None or i.unit_cost_net is None:
        return None
    rate = _proportional_rate(i)
    if rate <= 0:
        return None
    fixed = _fixed_costs(i)
    p = fixed / rate
    # Mindestprovision greift? Dann ist die Provision fix.
    if i.referral_min and p * i.referral_rate < i.referral_min:
        rate2 = ONE / (ONE + i.vat_rate) - _v(i.payment_fee_rate) - _v(i.ad_cost_rate)
        if rate2 <= 0:
            return None
        p = (fixed + i.referral_min) / rate2
    return _q(p)


def max_buy_price(i: CostInputs, min_margin: Decimal, min_contribution: Decimal) -> Decimal | None:
    """Maximal zulässiger Netto-EK je Lieferanteneinheit (Lieferantenwährung, vor Rabatt),
    damit Mindestmarge UND Mindest-DB erreicht werden."""
    if (
        i.sell_price_gross is None or i.vat_rate is None or i.referral_rate is None
        or i.fulfillment_fee is None
    ):
        return None
    p = i.sell_price_gross
    net_revenue = p / (ONE + i.vat_rate)
    referral = max(p * i.referral_rate, _v(i.referral_min))
    non_goods = referral + p * (_v(i.payment_fee_rate) + _v(i.ad_cost_rate)) + _fixed_costs(i, include_goods=False)
    target = max(net_revenue * min_margin, min_contribution)
    goods_budget = net_revenue - non_goods - target
    units = i.units_required or 1
    per_unit_landed = goods_budget / units
    # landed = cost*(1-disc)*fx*(1+duty) + ship  →  invert
    denom = (ONE - _v(i.supplier_discount_rate)) * i.fx_rate * (ONE + _v(i.import_duty_rate))
    if denom <= 0:
        return None
    value = (per_unit_landed - _v(i.inbound_shipping_per_unit)) / denom
    return _q(value) if value > 0 else ZERO


# --------------------------------------------------------------------------- Szenarien


@dataclass
class PricePoints:
    current: Decimal | None
    avg30: Decimal | None = None
    avg90: Decimal | None = None
    min90: Decimal | None = None


def scenario_prices(pp: PricePoints) -> dict[str, Decimal | None]:
    """Szenarien nur mit vorhandenen Daten; fehlende Historie → None (nicht erfunden)."""
    refs = [x for x in (pp.current, pp.avg30, pp.avg90, pp.min90) if x is not None]
    return {
        "current": pp.current,
        "ref_30d": pp.avg30,
        "ref_90d": pp.avg90,
        "conservative": min(refs) if refs else None,
        "drop_5": _q(pp.current * Decimal("0.95")) if pp.current is not None else None,
        "drop_10": _q(pp.current * Decimal("0.90")) if pp.current is not None else None,
    }


SCENARIO_LABELS = {
    "current": "Aktueller Verkaufspreis",
    "ref_30d": "30-Tage-Referenzpreis",
    "ref_90d": "90-Tage-Referenzpreis",
    "conservative": "Konservativ (Minimum der Referenzen)",
    "drop_5": "Preisrückgang 5 %",
    "drop_10": "Preisrückgang 10 %",
}


def run_scenarios(base: CostInputs, pp: PricePoints) -> dict[str, Calculation | None]:
    from dataclasses import replace

    out: dict[str, Calculation | None] = {}
    for name, price in scenario_prices(pp).items():
        out[name] = calculate(replace(base, sell_price_gross=price)) if price is not None else None
    return out


# --------------------------------------------------------------------------- Kapital


@dataclass
class CapitalMetrics:
    quantity: int
    capital_required: Decimal
    months_to_sell: Decimal | None
    capital_binding_months: Decimal | None  # Kapital x Monate gebunden (durchschnittlich)
    contribution_per_1000_per_month: Decimal | None

    def to_json(self) -> dict[str, Any]:
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(self).items()}


def capital_metrics(
    calc: Calculation, quantity: int, monthly_units: Decimal | None, lead_time_days: int | None
) -> CapitalMetrics | None:
    """Kapitalbedarf und DB je 1.000 € gebundenes Warenkapital und Monat.

    Annahmen (dokumentiert): Kapital ist ab Bezahlung bis zum Abverkauf gebunden; Abverkauf
    linear → durchschnittlich gebunden ist das halbe Kapital über die Abverkaufsdauer plus das
    volle Kapital während der Lieferzeit.
    """
    if calc.result is None or quantity <= 0:
        return None
    r = calc.result
    capital = _q(r.goods_cost * quantity)
    if not monthly_units or monthly_units <= 0:
        return CapitalMetrics(quantity, capital, None, None, None)
    sell_months = Decimal(quantity) / monthly_units
    lead_months = Decimal(lead_time_days or 0) / Decimal(30)
    avg_bound = capital * lead_months + capital / 2 * sell_months  # € x Monate
    total_months = lead_months + sell_months
    total_db = r.contribution * quantity
    per_1000 = (total_db / avg_bound * 1000) if avg_bound > 0 else None
    return CapitalMetrics(
        quantity=quantity,
        capital_required=capital,
        months_to_sell=_q(total_months),
        capital_binding_months=_q(avg_bound),
        contribution_per_1000_per_month=_q(per_1000) if per_1000 is not None else None,
    )
