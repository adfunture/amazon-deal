"""Konfidenz-Kalibrierung anhand manuell geprüfter Matching-Entscheidungen.

Verfahren: Score-Bins (Breite 0.05) je Klassifikation; je Bin die beobachtete Präzision
(Anteil bestätigter Matches) mit Laplace-Glättung. Ein Bin gilt erst ab ``MIN_SAMPLES``
Labels als kalibriert. Monotonie wird per Pool-Adjacent-Violators erzwungen (isotone
Regression). Ohne ausreichende Labels bleibt die Konfidenz = heuristischer Score und
wird als ``calibrated=False`` gespeichert.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from sbc.models import MatchingFeedback, MatchingResult

BIN = 0.05
MIN_SAMPLES = 20


@dataclass
class Bin:
    lo: float
    hi: float
    n: int
    positives: int
    precision: float


def _pav(values: list[tuple[float, int]]) -> list[float]:
    """Pool-Adjacent-Violators: (mean, weight) → monoton steigende Folge."""
    blocks = [[v, w, 1] for v, w in values]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] > blocks[i + 1][0]:
            v1, w1, c1 = blocks[i]
            v2, w2, c2 = blocks[i + 1]
            w = w1 + w2
            blocks[i] = [(v1 * w1 + v2 * w2) / w if w else 0, w, c1 + c2]
            del blocks[i + 1]
            i = max(i - 1, 0)
        else:
            i += 1
    out: list[float] = []
    for v, _, c in blocks:
        out.extend([v] * c)
    return out


def build_bins(labels: list[tuple[float, bool]]) -> list[Bin]:
    buckets: dict[int, list[bool]] = defaultdict(list)
    for score, positive in labels:
        buckets[min(int(score / BIN), int(1 / BIN) - 1)].append(positive)
    keys = sorted(buckets)
    raw = [((sum(buckets[k]) + 1) / (len(buckets[k]) + 2), len(buckets[k])) for k in keys]
    smooth = _pav(raw)
    return [
        Bin(k * BIN, (k + 1) * BIN, len(buckets[k]), sum(buckets[k]), round(p, 4))
        for k, p in zip(keys, smooth, strict=True)
    ]


def load_labels(db: Session, org_id, classification: str) -> list[tuple[float, bool]]:
    rows = db.execute(
        select(MatchingFeedback.score_at_review, MatchingFeedback.is_positive)
        .join(MatchingResult, MatchingResult.id == MatchingFeedback.matching_result_id)
        .where(
            MatchingFeedback.org_id == org_id,
            MatchingFeedback.previous_classification == classification,
            MatchingFeedback.is_positive.is_not(None),
        )
    ).all()
    return [(float(s), bool(p)) for s, p in rows]


def calibrate(score: Decimal, bins: list[Bin]) -> tuple[Decimal, bool]:
    s = float(score)
    for b in bins:
        if b.lo <= s < b.hi or (b.hi >= 1.0 and s >= b.lo):
            if b.n >= MIN_SAMPLES:
                return Decimal(str(b.precision)), True
            break
    return score, False
