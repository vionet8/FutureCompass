from datetime import date

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..models.user import User
from ..models.performance import AssetSnapshot, CashFlowEvent
from ..services.asset_history_import import import_asset_history
from ..services.rakuten_cashflow_import import import_rakuten_cashflow
from ..services.csv_parser import CSVParseError
from ..services.cash_flow import add_cash_flow, list_cash_flows, delete_cash_flow
from ..services.benchmark import ensure_cache_fresh, DEFAULT_SYMBOL, FX_SYMBOL
from ..services.performance_calc import (
    compute_user_performance,
    compute_benchmark_performance,
    CashFlow,
)
from .auth import get_current_user

router = APIRouter(prefix="/performance", tags=["performance"])


class CashFlowInput(BaseModel):
    flow_date: date
    amount_yen: int
    flow_type: str  # "deposit" | "withdrawal"
    memo: str | None = None


@router.post("/import-asset-history")
async def import_asset_history_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """マネーフォワード資産推移CSV（全履歴）をインポートする"""
    content = await file.read()
    try:
        result = import_asset_history(db, current_user.id, content)
    except CSVParseError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return result


@router.post("/import-rakuten-cashflow")
async def import_rakuten_cashflow_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """楽天証券の入出金履歴CSVをインポートし、投資キャッシュフローとして記録する"""
    content = await file.read()
    try:
        result = import_rakuten_cashflow(db, current_user.id, content)
    except CSVParseError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return result


@router.post("/cash-flows")
def create_cash_flow(
    req: CashFlowInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if req.flow_type not in ("deposit", "withdrawal"):
        raise HTTPException(status_code=400, detail="flow_typeはdepositまたはwithdrawalを指定してください")
    amount = abs(req.amount_yen) if req.flow_type == "deposit" else -abs(req.amount_yen)
    event = add_cash_flow(db, current_user.id, req.flow_date, amount, req.flow_type, req.memo)
    return {"id": event.id}


@router.get("/cash-flows")
def get_cash_flows(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    events = list_cash_flows(db, current_user.id)
    return [
        {
            "id": e.id,
            "date": e.flow_date.isoformat(),
            "amount_yen": e.amount_yen,
            "flow_type": e.flow_type,
            "memo": e.memo,
        }
        for e in events
    ]


@router.delete("/cash-flows/{flow_id}")
def remove_cash_flow(
    flow_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ok = delete_cash_flow(db, current_user.id, flow_id)
    if not ok:
        raise HTTPException(status_code=404, detail="見つかりません")
    return {"deleted": True}


@router.get("/summary")
def get_performance_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """実績投資成績 vs ベンチマーク比較のサマリー"""
    user_perf = compute_user_performance(db, current_user.id)
    if user_perf is None:
        return {
            "has_data": False,
            "message": "資産推移データが不足しています（最低2時点分の資産スナップショットが必要です）",
        }

    # VT価格とドル円レートの両方が必要（円建てVTリターン = ドル価格変動 × 為替変動）
    benchmark_error = ensure_cache_fresh(db, DEFAULT_SYMBOL)
    if benchmark_error is None:
        benchmark_error = ensure_cache_fresh(db, FX_SYMBOL)

    snapshots = (
        db.query(AssetSnapshot)
        .filter(AssetSnapshot.user_id == current_user.id)
        .order_by(AssetSnapshot.snapshot_date)
        .all()
    )
    t0, v0 = snapshots[0].snapshot_date, snapshots[0].investment_assets_yen
    t1 = snapshots[-1].snapshot_date
    flows_db = (
        db.query(CashFlowEvent)
        .filter(
            CashFlowEvent.user_id == current_user.id,
            CashFlowEvent.flow_date > t0,
            CashFlowEvent.flow_date <= t1,
        )
        .all()
    )
    flows = [CashFlow(f.flow_date, f.amount_yen) for f in flows_db]

    bench_perf_jpy = None
    bench_perf_usd = None
    if benchmark_error is None:
        # 円建て（日本円でVTを買った場合。ユーザーの円建て実績と対称な比較）
        bench_perf_jpy = compute_benchmark_performance(db, DEFAULT_SYMBOL, t0, v0, t1, flows, in_jpy=True)
        # ドル建て（為替の影響を除いたVT自体の成績。参考値）
        bench_perf_usd = compute_benchmark_performance(db, DEFAULT_SYMBOL, t0, v0, t1, flows, in_jpy=False)

    diff_pct = None
    if bench_perf_jpy is not None:
        diff_pct = round((user_perf["annualized_return"] - bench_perf_jpy["annualized_return"]) * 100, 2)

    return {
        "has_data": True,
        "period": {"start": t0.isoformat(), "end": t1.isoformat(), "days": user_perf["days"]},
        "user_annualized_return_pct": round(user_perf["annualized_return"] * 100, 2),
        "benchmark_symbol": DEFAULT_SYMBOL,
        "benchmark_annualized_return_pct": (
            round(bench_perf_jpy["annualized_return"] * 100, 2) if bench_perf_jpy else None
        ),
        "benchmark_usd_annualized_return_pct": (
            round(bench_perf_usd["annualized_return"] * 100, 2) if bench_perf_usd else None
        ),
        "diff_pct": diff_pct,
        "benchmark_error": benchmark_error,
    }
