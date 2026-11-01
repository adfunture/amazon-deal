"""Mehrsprachige Titel-Normalisierung ohne KI (DE, EN, FR, IT, ES, NL, PL).

Ansatz: Produktbegriffe werden über ein Lexikon auf sprachneutrale Konzepte abgebildet
(z. B. "Fahrradträger" / "bike carrier" / "porte-vélos" → ``bike_carrier``). Füllwörter
werden entfernt. Marken, Modell- und Artikelnummern (Tokens mit Ziffern) bleiben unverändert.
Die Originaltexte werden nicht verändert; die kanonische Form dient nur dem Vergleich.

Das Lexikon ist bewusst klein und erweiterbar (``CONCEPTS``). Für Begriffe, die hier fehlen,
greifen später Embeddings (P2c) und – nur bei unklaren Fällen – die KI-Prüfung.
"""

from __future__ import annotations

import re
import unicodedata

from sbc.domain.text import _COLORS

LANGS = ("de", "en", "fr", "it", "es", "nl", "pl")

# Typische Funktionswörter je Sprache für die Spracherkennung
_MARKERS = {
    "de": {"und", "für", "mit", "der", "die", "das", "herren", "damen", "stück", "größe", "schwarz", "weiß", "inkl"},
    "en": {"and", "for", "with", "the", "men's", "mens", "women's", "pack", "size", "black", "white", "of"},
    "fr": {"et", "pour", "avec", "le", "la", "les", "homme", "femme", "noir", "blanc", "taille", "lot", "de"},
    "it": {"e", "per", "con", "il", "lo", "la", "uomo", "donna", "nero", "bianco", "taglia", "confezione", "da"},
    "es": {"y", "para", "con", "el", "la", "los", "hombre", "mujer", "negro", "blanco", "talla", "paquete", "de"},
    "nl": {"en", "voor", "met", "het", "de", "heren", "dames", "zwart", "wit", "maat", "stuks", "van"},
    "pl": {"i", "dla", "z", "ze", "na", "męski", "meski", "damski", "czarny", "biały", "rozmiar", "szt", "zestaw"},
}
_CHAR_HINTS = {"de": "äöüß", "fr": "éèêàçœ", "es": "ñ¿¡", "pl": "ąćęłńśźż", "it": "ò", "nl": "ĳ"}

# Konzept → Synonyme in allen Sprachen (Mehrwortbegriffe zuerst geprüft)
CONCEPTS: dict[str, list[str]] = {
    "men": ["herren", "herrn", "men's", "mens", "men", "man", "homme", "hommes", "uomo", "hombre", "heren", "męski",
            "meski", "męskie", "meskie", "męska", "meska", "męskich", "masculino"],
    "women": ["damen", "women's", "womens", "women", "woman", "ladies", "femme", "femmes", "donna", "mujer", "dames",
              "damski", "damskie", "damska", "femenino"],
    "kids": ["kinder", "kids", "kid's", "children", "enfant", "enfants", "bambino", "bambini", "niño", "niños",
             "infantil", "kinderen", "dziecięcy", "dzieci"],
    "unisex": ["unisex"],
    "tshirt": ["t-shirt", "t shirt", "tshirt", "tee", "tee-shirt", "camiseta", "maglietta", "koszulka"],
    "shirt": ["hemd", "shirt", "chemise", "camicia", "camisa", "overhemd", "koszula"],
    "trousers": ["hose", "trousers", "pants", "pantalon", "pantaloni", "pantalones", "broek", "spodnie"],
    "jacket": ["jacke", "jacket", "veste", "giacca", "chaqueta", "jas", "kurtka"],
    "sweater": ["pullover", "sweater", "jumper", "pull", "maglione", "jersey", "sweatshirt", "trui", "sweter", "bluza"],
    "shoes": ["schuhe", "schuh", "shoes", "shoe", "chaussures", "scarpe", "zapatos", "schoenen", "buty"],
    "socks": ["socken", "socks", "chaussettes", "calzini", "calcetines", "sokken", "skarpety", "skarpetki"],
    "watch": ["armbanduhr", "uhr", "watch", "wristwatch", "montre", "orologio", "reloj", "horloge", "zegarek"],
    "bike_carrier": ["fahrradträger", "fahrradtraeger", "fahrrad träger", "bike carrier", "bike rack", "cycle carrier",
                     "porte-vélos", "porte-vélo", "porte vélo", "portabici", "portabiciclette", "portabicicletas",
                     "fietsendrager", "bagażnik rowerowy", "bagaznik rowerowy", "uchwyt rowerowy"],
    "roof_box": ["dachbox", "roof box", "cargo box", "coffre de toit", "box da tetto", "cofre de techo", "dakkoffer",
                 "bagażnik dachowy", "box dachowy"],
    "bicycle": ["fahrräder", "fahrraeder", "fahrrad", "räder", "bikes", "bike", "bicycles", "bicycle", "vélos", "vélo",
                "velo", "bici", "biciclette", "bicicletta", "bicicletas", "bicicleta", "fietsen", "fiets", "rowery",
                "rower", "rowerów"],
    "tow_bar": ["anhängerkupplung", "tow bar", "towbar", "hitch", "attelage", "gancio traino", "enganche", "trekhaak",
                "hak holowniczy"],
    "oil_filter": ["ölfilter", "oelfilter", "oil filter", "filtre à huile", "filtre a huile", "filtro olio",
                   "filtro de aceite", "filtro aceite", "oliefilter", "filtr oleju"],
    "air_filter": ["luftfilter", "air filter", "filtre à air", "filtre a air", "filtro aria", "filtro de aire",
                   "luchtfilter", "filtr powietrza"],
    "fuel_filter": ["kraftstofffilter", "fuel filter", "filtre à carburant", "filtro carburante",
                    "filtro de combustible", "brandstoffilter", "filtr paliwa"],
    "cabin_filter": ["innenraumfilter", "pollenfilter", "cabin filter", "pollen filter", "filtre d'habitacle",
                     "filtro abitacolo", "filtro de habitáculo", "interieurfilter", "filtr kabinowy"],
    "battery": ["batterie", "batterien", "battery", "batteries", "pile", "piles", "batteria", "batterie", "pila",
                "pilas", "batería", "batterij", "batterijen", "bateria", "baterie"],
    "charger": ["ladegerät", "ladegeraet", "charger", "chargeur", "caricabatterie", "cargador", "oplader", "ładowarka"],
    "cable": ["kabel", "cable", "câble", "cavo", "cabo", "przewód"],
    "headphones": ["kopfhörer", "headphones", "earphones", "cuffie", "auriculares", "koptelefoon",
                   "słuchawki"],
    "backpack": ["rucksack", "backpack", "sac à dos", "zaino", "mochila", "rugzak", "plecak"],
    "bag": ["tasche", "bag", "sac", "borsa", "bolso", "tas", "torba"],
    "bottle": ["flasche", "bottle", "bouteille", "bottiglia", "botella", "fles", "butelka"],
    "pen": ["stift", "kugelschreiber", "pen", "stylo", "penna", "bolígrafo", "pen", "długopis"],
    "fineliner": ["fineliner", "feinschreiber", "feutre fin", "pennarello", "rotulador", "fijnschrijver", "cienkopis"],
    "case": ["etui", "hülle", "case", "cover", "étui", "custodia", "funda", "hoes", "etui"],
    "set": ["set", "kit", "satz", "ensemble", "kit", "juego", "conjunto", "zestaw", "komplet"],
    "replacement": ["ersatz", "replacement", "spare", "rechange", "ricambio", "repuesto", "vervanging", "zamiennik"],
    "tent": ["zelt", "tent", "tente", "tenda", "tienda de campaña", "namiot"],
    "sleeping_bag": ["schlafsack", "sleeping bag", "sac de couchage", "sacco a pelo", "saco de dormir", "slaapzak",
                     "śpiwór"],
    "helmet": ["helm", "helmet", "casco", "kask"],  # "casque" (FR) ist mehrdeutig: Helm oder Kopfhörer
    "gloves": ["handschuhe", "gloves", "gants", "guanti", "guantes", "handschoenen", "rękawice", "rękawiczki"],
}

# Füllwörter (werden für den Vergleich entfernt)
STOPWORDS = {
    "für", "fur", "mit", "und", "der", "die", "das", "den", "dem", "ein", "eine", "von", "aus", "zum", "zur", "inkl",
    "for", "with", "and", "the", "a", "an", "of", "to", "in", "on", "incl", "including",
    "pour", "avec", "et", "le", "la", "les", "un", "une", "de", "du", "des", "en",
    "per", "con", "e", "il", "lo", "gli", "i", "uno", "una", "di", "da", "del", "della",
    "para", "y", "el", "los", "las", "unos", "voor", "met", "het", "een", "van",
    "dla", "z", "ze", "na", "do", "oraz",
    # Größen-Schlüsselwörter (die Größe selbst bleibt als Token erhalten)
    "größe", "groesse", "gr", "size", "taille", "taglia", "talla", "maat", "rozmiar",
}

_SYN_TO_CONCEPT: dict[str, str] = {}
for _c, _syns in CONCEPTS.items():
    for _s in _syns:
        _SYN_TO_CONCEPT.setdefault(_s, _c)
# Eine einzige Alternation (längste zuerst) → jede Stelle wird genau einmal ersetzt
_PHRASE_RX = re.compile(
    r"(?<![\w-])(" + "|".join(re.escape(s) for s in sorted(_SYN_TO_CONCEPT, key=len, reverse=True)) + r")(?![\w-])"
)
_SINGLE_WORD = {s: c for s, c in _SYN_TO_CONCEPT.items() if " " not in s and "-" not in s and len(s) >= 3}


def _plain(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    s = s.replace("’", "'")
    s = re.sub(r"[^\w\s'\-+/.]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def detect_language(text: str | None) -> str | None:
    """Grobe Spracherkennung über Funktionswörter und Sonderzeichen. None = unklar."""
    if not text:
        return None
    t = _plain(text)
    words = set(re.findall(r"[\w']+", t))
    scores = {lang: len(words & markers) for lang, markers in _MARKERS.items()}
    for lang, chars in _CHAR_HINTS.items():
        if any(c in t for c in chars):
            scores[lang] += 2
    best = max(scores, key=lambda k: scores[k])
    if scores[best] == 0:
        return None
    if sorted(scores.values(), reverse=True)[1] == scores[best]:
        return None  # Gleichstand → unklar
    return best


def _split_compound(tok: str) -> list[str] | None:
    """Deutsche Komposita aus zwei bekannten Begriffen zerlegen (z. B. herrenuhr → herren + uhr)."""
    if len(tok) < 6:
        return None
    for i in range(3, len(tok) - 2):
        a, b = tok[:i], tok[i:]
        for a2 in (a, a[:-1] if a.endswith("s") else None):  # Fugen-s: "herrensocken"
            if a2 and (a2 in _SINGLE_WORD or a2 in _COLORS) and (b in _SINGLE_WORD or b in _COLORS):
                return [a2, b]
    return None


def _concept_token(tok: str) -> str | None:
    if tok in _SINGLE_WORD:
        return "c:" + _SINGLE_WORD[tok]
    if tok in _COLORS:
        return "col:" + _COLORS[tok]
    return None


def canonical_tokens(title: str | None, protected: set[str] | None = None) -> list[str]:
    """Sprachneutrale Tokenfolge: Konzepte (``c:bike_carrier``), Farben (``col:black``),
    Zahlen sowie Modell-/Markentokens (unverändert).

    ``protected``: Tokens (z. B. Markenname), die unverändert bleiben müssen.
    """
    if not title:
        return []
    t = _PHRASE_RX.sub(lambda m: f" ⟦{_SYN_TO_CONCEPT[m.group(1)]}⟧ ", " " + _plain(title) + " ")
    out: list[str] = []
    prot = {p.lower() for p in (protected or set())}
    for tok in t.split():
        if tok.startswith("⟦") and tok.endswith("⟧"):
            out.append("c:" + tok[1:-1])
            continue
        tok = tok.strip("-/.'")
        if not tok:
            continue
        if tok in prot or re.search(r"\d", tok):
            out.append(tok)  # Marken, Modelle, Mengen unverändert
            continue
        ct = _concept_token(tok)
        if ct:
            out.append(ct)
            continue
        parts = _split_compound(tok)
        if parts:
            out.extend(_concept_token(p) or p for p in parts)
        elif tok not in STOPWORDS and len(tok) > 1:
            out.append(tok)
    return out


def canonical_text(title: str | None, protected: set[str] | None = None) -> str:
    return " ".join(canonical_tokens(title, protected))


def concepts(title: str | None) -> set[str]:
    return {t[2:] for t in canonical_tokens(title) if t.startswith("c:")}
