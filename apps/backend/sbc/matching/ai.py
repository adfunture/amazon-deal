"""KI-Stufe (Text + optional Bilder) für unsichere, wirtschaftlich relevante Kandidaten.

Regelvalidierung nach der KI-Antwort:
* Die KI darf einen Treffer nie auf EXACT_MATCH heben, wenn GTIN/Marke/Zustand hart abweichen.
* Bilder sind zusätzliche Evidenz und überstimmen widersprüchliche Produktdaten nicht.
* Bei Verdacht auf Prompt Injection → REVIEW_REQUIRED.
* KI-Ergebnisse werden nie automatisch freigegeben (review_status bleibt pending).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from sbc.integrations.llm import LLMProvider
from sbc.matching.engine import MatchOutcome
from sbc.models import MatchClass

SYSTEM = (
    "Du bist Produktdaten-Analyst für einen deutschen Marktplatzhändler. Du vergleichst ein "
    "Lieferantenangebot mit einem Marktplatzangebot und entscheidest, ob es dasselbe Produkt in "
    "derselben Verkaufseinheit ist. Achte besonders auf Modell, Generation, Farbe, Größe, "
    "Stückzahl (Multipack), Bundles, Zubehör und Verpackung. Sei konservativ: im Zweifel "
    "REVIEW_REQUIRED oder POSSIBLE_MATCH."
)


class AIMatchVerdict(BaseModel):
    classification: Literal[
        "EXACT_MATCH", "VARIANT_MATCH", "MULTIPACK", "BUNDLE", "PACKAGING_DIFFERENCE",
        "POSSIBLE_MATCH", "NO_MATCH", "REVIEW_REQUIRED",
    ]
    confidence: float = Field(ge=0, le=1)
    units_required: int | None = Field(default=None, description="Lieferanteneinheiten je Marktplatzangebot")
    matched_attributes: list[str]
    mismatched_attributes: list[str]
    bundle_components: list[str] = Field(default_factory=list)
    reasoning: str
    flags: list[str] = Field(default_factory=list)


def ai_review(provider: LLMProvider, supplier: dict, listing: dict, rule: MatchOutcome,
              use_images: bool) -> tuple[MatchOutcome, dict]:
    images = []
    if use_images:
        images = (supplier.get("image_urls") or [])[:2] + (listing.get("image_urls") or [])[:2]
    task = (
        "Vergleiche das Lieferantenprodukt (supplier) mit dem Marktplatzangebot (listing). "
        "Die regelbasierte Vorprüfung ergab: "
        f"{rule.classification.value} (Score {rule.score}), Gründe: {rule.reasoning or '-'}. "
        + ("Die ersten Bilder gehören zum Lieferanten, die folgenden zum Marktplatz. " if images else "")
        + "Gib deine Einschätzung im Schema zurück."
    )
    res = provider.structured(system=SYSTEM, untrusted={"supplier": supplier, "listing": listing},
                              task=task, schema=AIMatchVerdict, images=images, max_tokens=3000)
    v = res.parsed
    cls = MatchClass(v.classification)
    flags = set(v.flags)

    # Regelvalidierung
    hard = {"gtin", "brand", "condition"} & set(rule.mismatched)
    if cls == MatchClass.EXACT_MATCH and hard:
        cls = MatchClass.REVIEW_REQUIRED
        flags.add("ai_overruled_by_rules")
    if "prompt_injection_suspected" in flags:
        cls = MatchClass.REVIEW_REQUIRED
    units = rule.units_required
    if v.units_required and rule.units_required and v.units_required != rule.units_required:
        cls = MatchClass.REVIEW_REQUIRED
        flags.add("unit_quantity_disagreement")
    elif units is None and v.units_required:
        units = v.units_required  # nur Vorschlag; Review bleibt Pflicht
    # Kombinierter Score: Regeln 60 %, KI 40 % – KI allein kann nicht über 0.9 heben
    combined = Decimal(str(round(float(rule.score) * 0.6 + v.confidence * 0.4, 4)))
    if not rule.matched.get("gtin") and not rule.matched.get("mpn"):
        combined = min(combined, Decimal("0.9"))
    out = MatchOutcome(
        classification=cls, score=combined, units_required=units,
        stage="vision" if images else "llm",
        matched={**rule.matched, "ai": v.matched_attributes},
        mismatched={**rule.mismatched, **({"ai": v.mismatched_attributes} if v.mismatched_attributes else {})},
        reasons=[*rule.reasons, f"KI: {v.reasoning}"],
    )
    meta = {"model": res.model, "cost_eur": str(res.cost_eur), "input_tokens": res.input_tokens,
            "output_tokens": res.output_tokens, "duration_ms": res.duration_ms,
            "flags": sorted(flags), "bundle_components": v.bundle_components}
    return out, meta
