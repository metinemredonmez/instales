from datetime import date
from decimal import Decimal

from instilens.domain.enums import ActivityType, Confidence, SignalType
from instilens.engine.positions import HoldingView, SnapshotView, diff_snapshots
from instilens.engine.scoring import (
    InstrumentActivity,
    consensus_score,
    conviction_score,
    smart_money_score,
)
from instilens.engine.signals import (
    FundMove,
    PeriodFlow,
    detect_accumulation,
    detect_cluster,
    detect_divergence,
)


def _snap(d, **qty):
    return SnapshotView("TMV", d, tuple(HoldingView(s, q, weight_pct=Decimal(w)) for s, (q, w) in qty.items()))


def test_diff_classifies_new_add_reduce_exit_hold():
    prev = _snap(date(2026, 7, 31), ASELS=(100, "5"), SASA=(50, "2"), KCHOL=(10, "1"), EREGL=(80, "3"))
    curr = _snap(date(2026, 8, 31), ASELS=(150, "7"), KCHOL=(10, "1"), EREGL=(40, "1.5"), THYAO=(20, "2"))
    by = {d.instrument_symbol: d for d in diff_snapshots(prev, curr, prices={"ASELS": Decimal("140")})}
    assert by["ASELS"].activity is ActivityType.ADD and by["ASELS"].delta_value == Decimal("7000")
    assert by["SASA"].activity is ActivityType.EXIT and by["SASA"].to_qty == 0
    assert by["THYAO"].activity is ActivityType.NEW and by["THYAO"].from_weight_pct is None
    assert by["EREGL"].activity is ActivityType.REDUCE
    assert by["KCHOL"].activity is ActivityType.HOLD
    assert all(d.confidence is Confidence.INFERRED for d in by.values())


def test_diff_without_previous_marks_everything_new():
    curr = _snap(date(2026, 8, 31), ASELS=(150, "7"))
    (d,) = diff_snapshots(None, curr)
    assert d.activity is ActivityType.NEW and d.period_start is None


def test_accumulation_needs_three_same_sign_periods():
    flows = [PeriodFlow(date(2026, m, 28), q) for m, q in [(5, -10), (6, 100), (7, 150), (8, 200)]]
    sig = detect_accumulation(flows)
    assert sig and sig.signal_type is SignalType.ACCUMULATION and sig.evidence["consecutive_periods"] == 3
    assert detect_accumulation(flows[:3]) is None  # only two positive after the negative one
    dist = detect_accumulation([PeriodFlow(date(2026, m, 28), -q) for m, q in [(6, 1), (7, 2), (8, 3)]])
    assert dist and dist.signal_type is SignalType.DISTRIBUTION


def test_cluster_counts_distinct_funds():
    moves = [FundMove(f, ActivityType.NEW, date(2026, 8, 31)) for f in ["A", "B", "B", "C"]]
    sig = detect_cluster(moves, ActivityType.NEW)
    assert sig and sig.evidence["count"] == 3 and sig.signal_type is SignalType.NEW_POSITION_CLUSTER
    assert detect_cluster(moves[:2], ActivityType.NEW) is None


def test_divergence_directions():
    pos = detect_divergence(Decimal("-12"), Decimal("27"), date(2026, 8, 15), date(2026, 9, 14), 5, 1)
    neg = detect_divergence(Decimal("35"), Decimal("-21"), date(2026, 8, 15), date(2026, 9, 14), 1, 5)
    none = detect_divergence(Decimal("-2"), Decimal("27"), date(2026, 8, 15), date(2026, 9, 14), 5, 1)
    assert pos.signal_type is SignalType.POSITIVE_DIVERGENCE
    assert neg.signal_type is SignalType.NEGATIVE_DIVERGENCE
    assert none is None


def _activity(**kw):
    base = dict(funds_increasing=12, funds_reducing=2, funds_unchanged=3, funds_new=3, funds_exited=0,
                net_flow_value=Decimal("600000000"), persistence_periods=4, avg_conviction=0.8,
                days_since_last_activity=0, flow_by_confidence={Confidence.EXACT: Decimal(1)})
    base.update(kw)
    return InstrumentActivity(**base)


def test_smart_money_score_is_bounded_and_explainable():
    top = smart_money_score(_activity())
    assert 90 <= top.raw <= 100 and top.adjusted == top.raw  # EXACT → multiplier 1
    assert set(top.components) == {"breadth", "net_flow", "persistence", "new_positions", "conviction", "freshness"}
    inferred = smart_money_score(_activity(flow_by_confidence={Confidence.INFERRED: Decimal(1)}))
    assert inferred.confidence_multiplier == 0.8 and inferred.adjusted < inferred.raw
    selling = smart_money_score(_activity(funds_increasing=1, funds_reducing=10, net_flow_value=Decimal(-1), persistence_periods=0, funds_new=0, avg_conviction=0))
    assert selling.raw < 15


def test_consensus_score_symmetry():
    assert consensus_score(_activity(funds_increasing=10, funds_reducing=0, funds_unchanged=0, funds_new=0)).raw == 100
    assert consensus_score(_activity(funds_increasing=0, funds_reducing=10, funds_unchanged=0, funds_new=0)).raw == 0
    assert consensus_score(_activity(funds_increasing=5, funds_reducing=5, funds_unchanged=0, funds_new=0)).raw == 50
    assert consensus_score(_activity(funds_increasing=0, funds_reducing=0, funds_unchanged=0)).raw == 50


def test_conviction_rewards_relative_growth():
    big = conviction_score(Decimal("1.8"), Decimal("7.4"))
    small = conviction_score(Decimal("7.0"), Decimal("7.4"))
    new = conviction_score(None, Decimal("3.0"))
    assert big.raw > 85 and small.raw < big.raw and new.raw > small.raw
