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
    # FR/IT/ES/NL/PL: "lot de 2", "pack de 3", "confezione da 2", "set van 2", "zestaw 2", "paquete de 4"
    re.compile(r"\b(?:lot|pack|paquete|confezione|conf\.?|set|zestaw|opakowanie|verpakking)\s*(?:de|da|di|van|of|po)?\s*(\d{1,3})\b", re.I),
    # "2 pièces", "2 pezzi", "2 piezas", "2 stuks", "2 szt", "2 sztuki", "2 unités", "2 unidades"
    re.compile(r"\b(\d{1,3})\s*(?:pièces?|pieces|pcs|pezzi|pz|piezas|uds|unidades|unités|stuks|st|szt\.?|sztuki|sztuk)\b", re.I),
]
_WORD_PACKS = {
    "doppelpack": 2,
    "twin pack": 2,
    "twinpack": 2,
    "2-pack": 2,
    "dreierpack": 3,
    "triple pack": 3,
    "viererpack": 4,
    "duo pack": 2, "lot de deux": 2, "confezione doppia": 2, "pack doble": 2, "duopak": 2, "dwupak": 2,
}
_BUNDLE_HINTS = re.compile(
    r"(\bbundle\b|\bset\b|\bkit\b|\binkl\.?|\binklusive\b|\bincl\.?|\bincluding\b|\bmit\b.+\bund\b|\s\+\s|\bgeschenkset\b|\bgift set\b|\bzubehör\b"
    r"|\bcoffret\b|\bavec\b.+\bet\b|\bincluso\b|\bcon\b.+\be\b|\bincluye\b|\bjuego\b|\binclusief\b|\bzestaw\b|\bw zestawie\b"
    r"|\baccessoires?\b|\baccessori\b|\baccesorios\b|\baccessories\b)",
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
    # ES/FR/IT – Amazon-EU-Listings tauchen häufig marktplatzübergreifend auf
    "negro": "black", "noir": "black", "nero": "black", "blanco": "white", "blanc": "white", "bianco": "white",
    "rojo": "red", "rouge": "red", "rosso": "red", "azul": "blue", "bleu": "blue", "blu": "blue",
    "verde": "green", "vert": "green", "amarillo": "yellow", "jaune": "yellow", "giallo": "yellow",
    "gris": "grey", "grigio": "grey", "plata": "silver", "argent": "silver", "argento": "silver",
    "rose": "pink", "marrón": "brown", "marron": "brown", "marrone": "brown",
    "naranja": "orange", "arancione": "orange", "morado": "purple", "violet": "purple", "viola": "purple",
    # NL/PL
    "zwart": "black", "wit": "white", "rood": "red", "blauw": "blue", "groen": "green", "geel": "yellow",
    "grijs": "grey", "zilver": "silver", "bruin": "brown", "roze": "pink", "paars": "purple",
    "czarny": "black", "czarna": "black", "czarne": "black", "biały": "white", "biała": "white", "białe": "white",
    "czerwony": "red", "czerwona": "red", "niebieski": "blue", "niebieska": "blue", "zielony": "green",
    "zielona": "green", "żółty": "yellow", "szary": "grey", "szara": "grey", "srebrny": "silver",
    "brązowy": "brown", "różowy": "pink", "fioletowy": "purple", "pomarańczowy": "orange",
    "noire": "black", "blanche": "white", "nera": "black", "bianca": "white", "negra": "black", "blanca": "white",
}


# Größen: XS/XL/XXL… immer; S/M/L nur nach Größen-Schlüsselwort (sonst Verwechslung mit Meter/Liter)
_SIZE_KEYWORD = r"(?:größe|groesse|gr\.?|size|taille|taglia|talla|maat|rozmiar|eu)"
_SIZE_LETTER = re.compile(r"(?<![\w-])(\d?x{0,3}[sl]|xxs|xs|[2-5]xl)(?![\w-])", re.I)
_SIZE_WITH_KEYWORD = re.compile(_SIZE_KEYWORD + r"\s*:?\s*(\d{1,3}(?:[.,/]\d{1,2})?|x{0,3}[sml]|[2-5]xl)(?![\w])", re.I)


def _norm_size(raw: str) -> str:
    r = raw.upper().replace(",", ".")
    m = re.fullmatch(r"([2-5])XL", r)
    if m:
        return "X" * int(m.group(1)) + "L"
    return r


_MATERIALS = {
    "cotton": ["baumwolle", "cotton", "coton", "cotone", "algodón", "algodon", "katoen", "bawełna", "bawelna"],
    "leather": ["leder", "echtleder", "leather", "cuir", "pelle", "cuoio", "cuero", "piel", "leer", "skóra", "skora"],
    "steel": ["edelstahl", "stahl", "steel", "stainless", "inox", "acier", "acciaio", "acero", "staal", "rvs", "stal"],
    "aluminium": ["aluminium", "aluminum", "alu", "alluminio", "aluminio", "aluminium"],
    "plastic": ["kunststoff", "plastik", "plastic", "plastique", "plastica", "plástico", "plastico", "kunststof",
                "tworzywo", "plastikowy"],
    "wood": ["holz", "wood", "wooden", "bois", "legno", "madera", "hout", "drewno", "drewniany"],
    "glass": ["glas", "glass", "verre", "vetro", "vidrio", "szkło", "szklo"],
    "polyester": ["polyester", "poliestere", "poliéster", "poliester"],
    "wool": ["wolle", "wool", "merino", "laine", "lana", "wol", "wełna", "welna"],
    "silicone": ["silikon", "silicone", "silicona", "siliconen"],
}
_MATERIAL_WORDS = {w: m for m, ws in _MATERIALS.items() for w in ws}


def extract_materials(title: str | None) -> set[str]:
    if not title:
        return set()
    words = re.findall(r"[^\W\d_]+", unicodedata.normalize("NFKC", title).lower())
    return {_MATERIAL_WORDS[w] for w in words if w in _MATERIAL_WORDS}


def extract_sizes(title: str | None) -> set[str]:
    if not title:
        return set()
    sizes = {_norm_size(m.group(1)) for m in _SIZE_WITH_KEYWORD.finditer(title)}
    for m in _SIZE_LETTER.finditer(title):
        tok = m.group(1)
        if tok.upper() in {"S", "L"}:  # nackte S/L nur mit Schlüsselwort (siehe oben)
            continue
        sizes.add(_norm_size(tok))
    return sizes


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
    sizes: set[str] = field(default_factory=set)
    materials: set[str] = field(default_factory=set)
    model_tokens: set[str] = field(default_factory=set)

    def as_dict(self) -> dict:
        return {
            "pack_quantity": self.pack_quantity,
            "pack_evidence": self.pack_evidence,
            "bundle_hint": self.bundle_hint,
            "measures": sorted(self.measures),
            "colors": sorted(self.colors),
            "sizes": sorted(self.sizes),
            "materials": sorted(self.materials),
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
    for word in re.findall(r"[^\W\d_]+", norm):
        if word in _COLORS:
            attrs.colors.add(_COLORS[word])
    attrs.sizes = extract_sizes(title)
    attrs.materials = extract_materials(title)
    brand_up = (brand or "").upper()
    for tok in _MODEL_TOKEN.findall(title.upper()):
        tok = tok.strip("-/.")
        if (len(tok) >= 3 and tok != brand_up and not re.fullmatch(r"\d+(ER|X|PCS|STK|ML|MM|CM|G|KG|L|W|V)", tok)
                and not re.fullmatch(r"[2-5]XL|X{1,3}[SL]", tok)):
            attrs.model_tokens.add(re.sub(r"[\-/.]", "", tok))
    return attrs
