"""
Modified Dietz法による実績投資成績の計算。

反復計算(XIRR)を避け、閉じた式で期間リターン・年率換算リターンを求める。
"""

from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from ..models.performance import AssetSnapshot, CashFlowEvent
from .benchmark import get_price_on_or_before, get_price_jpy_on_or_before


@dataclass
class CashFlow:
    flow_date: date
    amount_yen: float  # 入金=正、出金=負


def modified_dietz(
    v0: float, v1: float, t0: date, t1: date, flows: list[CashFlow],
) -> Optional[dict]:
    """
    Modified Dietz法で期間リターンと年率換算リターンを計算する。
    計算不能な場合（期間ゼロ、分母がゼロ以下等）は例外を投げずNoneを返す。
    """
    d_total = (t1 - t0).days
    if d_total <= 0:
        return None

    net_cf = sum(f.amount_yen for f in flows)
    weighted_cf = 0.0
    for f in flows:
        # weight = 期間中その資金が「投資されていた」日数の割合。
        # t0時点の入金は期間全体で運用されるのでweight≈1、t1直前の入金はweight≈0。
        days_elapsed = (f.flow_date - t0).days
        days_elapsed = max(0, min(d_total, days_elapsed))
        weight = (d_total - days_elapsed) / d_total
        weighted_cf += weight * f.amount_yen

    denominator = v0 + weighted_cf
    if denominator <= 0:
        return None

    r = (v1 - v0 - net_cf) / denominator
    r_annualized = (1 + r) ** (365.0 / d_total) - 1
    return {"period_return": r, "annualized_return": r_annualized, "days": d_total}


def compute_user_performance(db: Session, user_id: str) -> Optional[dict]:
    """
    AssetSnapshot + CashFlowEvent からユーザーの実績Modified Dietzを計算する。

    総資産(現金+投資)ではなく投資資産(investment_assets_yen)のみを対象とする。
    総資産ベースだと「貯蓄による増加」と「運用益」が混ざり、市場平均との比較が
    意味を持たなくなるため（貯蓄率が高い人ほど見かけ上リターンが高くなってしまう）。
    """
    snapshots = (
        db.query(AssetSnapshot)
        .filter(AssetSnapshot.user_id == user_id)
        .order_by(AssetSnapshot.snapshot_date)
        .all()
    )
    if len(snapshots) < 2:
        return None

    t0, v0 = snapshots[0].snapshot_date, snapshots[0].investment_assets_yen
    t1, v1 = snapshots[-1].snapshot_date, snapshots[-1].investment_assets_yen

    flows_db = (
        db.query(CashFlowEvent)
        .filter(
            CashFlowEvent.user_id == user_id,
            CashFlowEvent.flow_date > t0,
            CashFlowEvent.flow_date <= t1,
        )
        .all()
    )
    flows = [CashFlow(f.flow_date, f.amount_yen) for f in flows_db]
    return modified_dietz(v0, v1, t0, t1, flows)


def compute_history_series(db: Session, user_id: str, symbol: str) -> Optional[dict]:
    """
    資産推移グラフ用の時系列を返す。
    各スナップショット日について、実際の投資資産額と、
    「同じ入金タイミングで円建てVTを買っていた場合」の架空評価額を並べる。

    戻り値: {"points": [{"date", "user_yen", "benchmark_yen"|None}, ...]}
    ベンチマーク価格が無い日はbenchmark_yen=Noneでユーザー系列だけ返す（劣化動作）。
    """
    snapshots = (
        db.query(AssetSnapshot)
        .filter(AssetSnapshot.user_id == user_id)
        .order_by(AssetSnapshot.snapshot_date)
        .all()
    )
    if len(snapshots) < 2:
        return None

    t0 = snapshots[0].snapshot_date
    v0 = snapshots[0].investment_assets_yen
    t1 = snapshots[-1].snapshot_date

    flows_db = (
        db.query(CashFlowEvent)
        .filter(
            CashFlowEvent.user_id == user_id,
            CashFlowEvent.flow_date > t0,
            CashFlowEvent.flow_date <= t1,
        )
        .order_by(CashFlowEvent.flow_date)
        .all()
    )
    # 開始残高v0はt0時点の架空入金として扱う（compute_benchmark_performanceと同じ対称化）
    synthetic_flows = [CashFlow(t0, float(v0))] + [CashFlow(f.flow_date, f.amount_yen) for f in flows_db]

    points = []
    units = 0.0
    flow_idx = 0
    for snap in snapshots:
        d = snap.snapshot_date
        # この日までに発生したキャッシュフローを架空VT購入としてユニット化
        while flow_idx < len(synthetic_flows) and synthetic_flows[flow_idx].flow_date <= d:
            f = synthetic_flows[flow_idx]
            p = get_price_jpy_on_or_before(db, symbol, f.flow_date)
            if p is not None and p > 0:
                units += f.amount_yen / p
            flow_idx += 1

        price = get_price_jpy_on_or_before(db, symbol, d)
        bench_yen = units * price if (price is not None and units > 0) else None
        points.append({
            "date": d,
            "user_yen": snap.investment_assets_yen,
            "benchmark_yen": bench_yen,
        })
    return {"points": points}


def compute_benchmark_performance(
    db: Session, symbol: str, t0: date, v0: float, t1: date, flows: list[CashFlow],
    in_jpy: bool = True,
) -> Optional[dict]:
    """
    ユーザーと同じキャッシュフロー・同一期間で、ベンチマークの「架空運用」リターンを計算。
    開始残高v0はt0時点の「架空入金」として扱う（ユーザー計算と対称にするため）。

    in_jpy=True（デフォルト）: ドル建て価格をドル円レートで円換算して計算する。
      キャッシュフローは円建てなので、ユーザーの円建て実績と比較するには
      「日本円でVTを買った場合」のリターン（=価格変動×為替変動）でなければ対称にならない。
    in_jpy=False: ドル建て価格のまま計算（為替の影響を除いたVT自体の成績）。
    """
    price_fn = get_price_jpy_on_or_before if in_jpy else get_price_on_or_before
    price1 = price_fn(db, symbol, t1)
    if price_fn(db, symbol, t0) is None or price1 is None:
        return None

    synthetic_flows = [CashFlow(t0, v0)] + list(flows)
    units = 0.0
    for f in synthetic_flows:
        p = price_fn(db, symbol, f.flow_date)
        if p is None or p <= 0:
            continue
        units += f.amount_yen / p

    bench_v1 = units * price1
    return modified_dietz(0.0, bench_v1, t0, t1, synthetic_flows)
