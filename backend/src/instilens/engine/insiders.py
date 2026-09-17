"""Insider cluster detection (US, SEC Form 4). Pure: rows in, a verdict out — no session, no clock.

INSIDER_BUY_CLUSTER fires when at least CLUSTER_MIN_INSIDERS distinct insiders of one issuer made open-market
purchases (transaction code P, non-derivative table) inside the CLUSTER_WINDOW_DAYS ending on `as_of`. Grants (A),
option exercises and RSU settlements (M), tax withholding (F), gifts (G) and every derivative-table row never count:
they are not a decision to pay market price for the stock. Thresholds are module constants — part of the published
methodology (docs/04-confidence-and-scoring.md), never per request.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from instilens.domain.enums import Confidence, SignalType
from instilens.engine.signals import DetectedSignal, _clamp

CLUSTER_WINDOW_DAYS = 30
CLUSTER_MIN_INSIDERS = 3
CLUSTER_INSIDERS_SATURATION = 5  # five distinct buyers → the breadth factor is 1
CLUSTER_VALUE_SATURATION = Decimal(1_000_000)  # $1M of purchases → the value factor is 1
OPEN_MARKET_PURCHASE = "P"
CONFIDENCE_RANK = {Confidence.EXACT: 0, Confidence.GROUPED: 1, Confidence.INFERRED: 2}  # the signal takes the weakest row


@dataclass(frozen=True)
class InsiderTrade:
    insider_cik: str
    insider_name: str
    transaction_date: date
    code: str
    derivative: bool
    shares: Decimal
    price: Decimal | None
    accession: str
    # Every reporting owner of the filing (a director and their trust, a fund and its general partner) — the row is
    # attributed to one of them, the cluster counts a person once however the owners were listed. Empty: the
    # attributed owner alone.
    owner_ciks: tuple[str, ...] = ()
    confidence: str = Confidence.EXACT


def open_market_purchases(trades: list[InsiderTrade], start: date, as_of: date) -> list[InsiderTrade]:
    """Code P rows of the non-derivative table dated in (start, as_of]."""
    return [t for t in trades if t.code == OPEN_MARKET_PURCHASE and not t.derivative and start < t.transaction_date <= as_of]


def cluster_strength(insiders: int, value: Decimal) -> int:
    """0..100 = 100 × min(1, insiders / 5) × (0.5 + 0.5 × min(1, value / $1M)). Breadth is the main factor; the
    value term lifts a cluster the more the insiders paid, but a cluster whose filings state no prices (value 0)
    still scores half its breadth — three buyers at $0 known value = 30, three at ≥ $1M = 60, five at ≥ $1M = 100."""
    breadth = min(Decimal(1), Decimal(insiders) / CLUSTER_INSIDERS_SATURATION)
    money = min(Decimal(1), max(Decimal(0), value) / CLUSTER_VALUE_SATURATION)
    return _clamp(float(100 * breadth * (Decimal("0.5") + Decimal("0.5") * money)))


def insider_groups(hits: list[InsiderTrade]) -> list[list[InsiderTrade]]:
    """The purchases grouped by the person behind them. A joint filing names every reporting owner, and the same
    person leads one filing and follows another (the trust listed first, then the director), so two filings whose
    owner sets overlap are one insider — the groups are the connected components of those sets, ordered by their
    smallest CIK; each group's trades keep their date order."""
    parent: dict[str, str] = {}

    def find(c: str) -> str:
        while parent.setdefault(c, c) != c:
            c = parent[c]
        return c

    for t in hits:
        for c in (t.insider_cik, *t.owner_ciks):
            parent[find(c)] = find(t.insider_cik)
    groups: dict[str, list[InsiderTrade]] = defaultdict(list)
    for t in sorted(hits, key=lambda t: (t.transaction_date, t.accession)):
        groups[find(t.insider_cik)].append(t)
    return sorted(groups.values(), key=lambda g: min(t.insider_cik for t in g))


def cluster(trades: list[InsiderTrade], as_of: date, window_days: int = CLUSTER_WINDOW_DAYS, min_insiders: int = CLUSTER_MIN_INSIDERS) -> dict | None:
    """None, or the cluster: since (first purchase date in the window), insiders (distinct count), value (Σ shares ×
    price of the priced purchases, as a str), unpriced (purchases without a stated price, counted in breadth but
    not in value), accessions and names (one per insider, as the first filing printed it) — the evidence shown to
    the user."""
    hits = open_market_purchases(trades, as_of - timedelta(days=window_days), as_of)
    groups = insider_groups(hits)
    if len(groups) < min_insiders:
        return None
    value = sum((t.shares * t.price for t in hits if t.price is not None), Decimal(0))
    return {
        "since": min(t.transaction_date for t in hits).isoformat(),
        "insiders": len(groups),
        "value": str(value.quantize(Decimal("0.01"))),
        "unpriced": sum(1 for t in hits if t.price is None),
        "purchases": len(hits),
        "accessions": sorted({t.accession for t in hits}),
        "names": [g[0].insider_name for g in groups],
    }


def detect_insider_buy_cluster(trades: list[InsiderTrade], as_of: date) -> DetectedSignal | None:
    """The cluster as a signal row: window = [since, as_of], confidence = the weakest of its purchases' (EXACT for
    every Form 4 row today — each purchase is the insider's own report)."""
    found = cluster(trades, as_of)
    if found is None:
        return None
    hits = open_market_purchases(trades, as_of - timedelta(days=CLUSTER_WINDOW_DAYS), as_of)
    return DetectedSignal(
        signal_type=SignalType.INSIDER_BUY_CLUSTER,
        strength=cluster_strength(found["insiders"], Decimal(found["value"])),
        window_start=date.fromisoformat(found["since"]),
        window_end=as_of,
        confidence=max((t.confidence for t in hits), key=lambda c: CONFIDENCE_RANK.get(c, len(CONFIDENCE_RANK))),
        evidence={**found, "window_days": CLUSTER_WINDOW_DAYS},
    )
