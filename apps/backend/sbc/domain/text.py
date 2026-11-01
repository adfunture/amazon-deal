"""Regelbasierte Attribut-Extraktion aus Produkttiteln (deterministisch, testbar).

Erkennt Verkaufseinheiten (Multipacks/Sets), Mengenangaben, Farben, Modellnummern.
Die KI-Stufe ergänzt das nur und darf diese Regeln nicht stillschweigend überstimmen.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

_WS = re.compile(r"\s+")

# Verkaufseinheit: "2er Pack", "2er-Set", "Pack of 3", "3-Pack", "2 x", "x2", "2 Stück", "Doppelpack"
_PACK_PATTERNS = [
    re.compile(r"\b(\d{1,3})\s*er[\s\-]*(?:pack|packung|set|pckg|pkg)\b", re.I),
    re.compile(r"\b(?:pack|set|packung|lot|bundle)\s*(?:of|von|à|a)?\s*(\d{1,3})\b", re.I),
    re.compile(r"\b(\d{1,3})\s*[\-\s]?\s*(?:pack|pcs|pc|stk|stck|stück|stueck|teilig|tlg|pieces?|units?)\b\.?", re.I),
    re.compile(r"(?:^|\s)(\d{1,2})\s*[x×]\s+(?=[a-zäöü])", re.I),  # "2 x Casio ..."
    re.compile(r"\b[x×]\s*(\d{1,3})\b(?!\s*(?:mm|cm|m|ml|l|g|kg)\b)", re.I),  # "Filter x2"
]
_WORD_PACKS = {
    "doppelpack": 2,
    "twin pack": 2,
    "twinpack": 2,
    "2-pack": 2,
    "dreierpack": 3,
    "triple pack": 3,
    "viererpack": 4,
}
_BUNDLE_HINTS = re.compile(
    r"(\bbundle\b|\bset\b|\bkit\b|\binkl\.?|\binklusive\b|\bincl\.?|\bincluding\b|\bmit\b.+\bund\b|\s\+\s|\bgeschenkset\b|\bgift set\b|\bzubehör\b)",
    re.I,
)
_MEASURE = re.compile(
    r"\b(\d+(?:[.,]\d+)?)\s*(mm|cm|m|ml|l|g|kg|w|v|mah|gb|tb|zoll|inch|\")(?=\s|$|[,;)/])",
    re.I,
)
_MODEL_TOKEN = re.compile(r"\b(?=[A-Z0-9\-/.]*\d)(?=[A-Z0-9\-/.]*[A-Z])[A-Z0-9][A-Z0-9\-/.]{2,}\b")
_COLORS = {
    "schwarz": "black", "black": "black", "weiß": "white", "weiss": "white", "white": "white",
    "rot": "red", "red": "red", "blau": "blue", "blue": "blue", "grün": "green", "gruen": "green",
    "green": "green", "gelb": "yellow", "yellow": "yellow", "silber": "silver", "silver": "silver",
    "gold": "gold", "grau": "grey", "grey": "grey", "gray": "grey", "rosa": "pink", "pink": "pink",
    "braun": "brown", "brown": "brown", "orange": "orange", "lila": "purple", "violett": "purple",
    "purple": "purple", "beige": "beige", "anthrazit": "anthracite", "navy": "navy",
}


def normalize_text(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"[^\w\s\-+/.,×x\"]", " ", s)
    return _WS.sub(" ", s).strip()


@dataclass
class TitleAttributes:
    pack_quantity: int | None = None
    pack_evidence: str | None = None
    bundle_hint: bool = False
    measures: set[str] = field(default_factory=set)
    colors: set[str] = field(default_factory=set)
    model_tokens: set[str] = field(default_factory=set)

    def as_dict(self) -> dict:
        return {
            "pack_quantity": self.pack_quantity,
            "pack_evidence": self.pack_evidence,
            "bundle_hint": self.bundle_hint,
            "measures": sorted(self.measures),
            "colors": sorted(self.colors),
            "model_tokens": sorted(self.model_tokens),
        }


def extract_pack_quantity(title: str | None) -> tuple[int | None, str | None]:
    if not title:
        return None, None
    low = title.lower()
    for word, qty in _WORD_PACKS.items():
        if word in low:
            return qty, word
    for pat in _PACK_PATTERNS:
        m = pat.search(title)
        if m:
            qty = int(m.group(1))
            if 1 <= qty <= 500:
                return qty, m.group(0).strip()
    return None, None


def extract_attributes(title: str | None, brand: str | None = None) -> TitleAttributes:
    attrs = TitleAttributes()
    if not title:
        return attrs
    attrs.pack_quantity, attrs.pack_evidence = extract_pack_quantity(title)
    attrs.bundle_hint = bool(_BUNDLE_HINTS.search(title))
    norm = normalize_text(title)
    for m in _MEASURE.finditer(norm):
        num = m.group(1).replace(",", ".")
        if num.endswith(".0"):
            num = num[:-2]
        attrs.measures.add(f"{num}{m.group(2).lower()}")
    for word in re.findall(r"[a-zäöüß]+", norm):
        if word in _COLORS:
            attrs.colors.add(_COLORS[word])
    brand_up = (brand or "").upper()
    for tok in _MODEL_TOKEN.findall(title.upper()):
        tok = tok.strip("-/.")
        if len(tok) >= 3 and tok != brand_up and not re.fullmatch(r"\d+(ER|X|PCS|STK|ML|MM|CM|G|KG|L|W|V)", tok):
            attrs.model_tokens.add(re.sub(r"[\-/.]", "", tok))
    return attrs
