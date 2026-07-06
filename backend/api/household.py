from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.orm import Session
from sqlalchemy import extract, func, text
from typing import Optional

from ..core.database import get_db
from ..models.mf_transaction import MFTransaction
from ..models.profile import UserProfile
from ..models.user import User
from ..services.mf_analyzer import (
    parse_mf_csv,
    monthly_summary,
    compare_yoy,
    detect_trend,
    build_ai_analysis_payload,
)
from ..services.ai_report import generate_household_analysis
from ..core.security import decrypt_value
from .auth import get_current_user

router = APIRouter(prefix="/household", tags=["household"])


def _profile_hint(profile: Optional[UserProfile]) -> dict:
    """AIに送るプロフィール情報（匿名）"""
    if not profile:
        return {}
    def dec(v):
        return int(decrypt_value(v)) if v else 0
    return {
        "年齢帯": f"{(profile.age // 10) * 10}代" if profile.age else "不明",
        "子供人数": len(profile.children_ages or []),
        "世帯年収帯_万円": f"{(dec(profile.annual_income_encrypted) // 100) * 100}〜{(dec(profile.annual_income_encrypted) // 100) * 100 + 100}",
    }


@router.post("/import-csv")
async def import_mf_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """マネーフォワード入出金明細CSVをインポートする"""
    if not file.filename.endswith((".csv", ".tsv", ".txt")):
        raise HTTPException(status_code=400, detail="CSV/TSVファイルのみ対応しています")

    content = await file.read()
    try:
        transactions = parse_mf_csv(content)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if not transactions:
        raise HTTPException(status_code=422, detail="有効なトランザクションが見つかりませんでした")

    # 重複チェック：同一mf_idがあればスキップ
    existing_ids = set(
        row[0] for row in db.query(MFTransaction.mf_id)
        .filter(
            MFTransaction.user_id == current_user.id,
            MFTransaction.mf_id.isnot(None),
        ).all()
    )

    new_count = 0
    skip_count = 0
    for t in transactions:
        if t["mf_id"] and t["mf_id"] in existing_ids:
            skip_count += 1
            continue
        db.add(MFTransaction(
            user_id=current_user.id,
            mf_id=t["mf_id"],
            transaction_date=t["date"],
            description=t["description"],
            amount_yen=t["amount_yen"],
            institution=t["institution"],
            category_major=t["category_major"],
            category_minor=t["category_minor"],
            memo=t["memo"],
            is_transfer=t["is_transfer"],
            is_target=t["is_target"],
        ))
        new_count += 1

    db.commit()

    # インポートされた月の一覧
    months = sorted(set(
        (t["date"].year, t["date"].month)
        for t in transactions
        if t["is_target"] and not t["is_transfer"]
    ), reverse=True)

    return {
        "imported": new_count,
        "skipped": skip_count,
        "total_in_file": len(transactions),
        "available_months": [{"year": y, "month": m} for y, m in months[:24]],
    }


@router.get("/months")
def get_available_months(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """インポート済みのデータがある月の一覧を返す"""
    rows = (
        db.query(
            extract("year", MFTransaction.transaction_date).label("year"),
            extract("month", MFTransaction.transaction_date).label("month"),
            func.count(MFTransaction.id).label("count"),
        )
        .filter(
            MFTransaction.user_id == current_user.id,
            MFTransaction.is_target == True,
            MFTransaction.is_transfer == False,
        )
        .group_by("year", "month")
        .order_by(text("year desc"), text("month desc"))
        .all()
    )
    return [{"year": int(r.year), "month": int(r.month), "count": r.count} for r in rows]


def _load_transactions(user_id: str, db: Session) -> list[dict]:
    rows = (
        db.query(MFTransaction)
        .filter(MFTransaction.user_id == user_id)
        .order_by(MFTransaction.transaction_date)
        .all()
    )
    return [
        {
            "date": r.transaction_date,
            "description": r.description,
            "amount_yen": r.amount_yen,
            "institution": r.institution,
            "category_major": r.category_major,
            "category_minor": r.category_minor,
            "memo": r.memo,
            "is_transfer": r.is_transfer,
            "is_target": r.is_target,
            "mf_id": r.mf_id,
        }
        for r in rows
    ]


@router.get("/summary/{year}/{month}")
def get_monthly_summary(
    year: int,
    month: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """指定月のカテゴリ別支出サマリー"""
    transactions = _load_transactions(current_user.id, db)
    summary = monthly_summary(transactions, year, month)
    return summary


@router.get("/compare/{year}/{month}")
def get_yoy_comparison(
    year: int,
    month: int,
    threshold: float = Query(default=0.10, ge=0.0, le=1.0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """当月 vs 前年同月の比較。threshold=0.10 → ±10%以上でフラグ"""
    transactions = _load_transactions(current_user.id, db)

    current = monthly_summary(transactions, year, month)
    prev_year_val = year - 1
    prev = monthly_summary(transactions, prev_year_val, month)

    comparison = compare_yoy(current, prev, threshold)
    return {
        "current": current,
        "prev_year": prev,
        "comparison": comparison,
        "threshold_pct": int(threshold * 100),
    }


@router.get("/trend")
def get_spending_trend(
    months: int = Query(default=6, ge=3, le=12),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """直近N月のカテゴリ別支出トレンドと「じわじわ増加」検出。
    基準月は「データが存在する最新月」（実行日ではない）。"""
    transactions = _load_transactions(current_user.id, db)

    valid = [t for t in transactions if t["is_target"] and not t["is_transfer"]]
    if not valid:
        return {"monthly_summaries": [], "trends": []}

    latest = max(t["date"] for t in valid)
    summaries = []
    for i in range(months - 1, -1, -1):
        m = latest.month - i
        y = latest.year
        while m <= 0:
            m += 12
            y -= 1
        summaries.append(monthly_summary(transactions, y, m))

    trends = detect_trend(summaries)
    return {
        "monthly_summaries": summaries,
        "trends": trends,
    }


@router.post("/ai-analysis/{year}/{month}")
def get_ai_analysis(
    year: int,
    month: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """AI家計分析・削減提案を生成（匿名化データのみ送信）"""
    transactions = _load_transactions(current_user.id, db)

    current = monthly_summary(transactions, year, month)
    if current["transaction_count"] == 0:
        raise HTTPException(status_code=404, detail=f"{year}年{month}月のデータがありません")

    prev = monthly_summary(transactions, year - 1, month)
    comparison = compare_yoy(current, prev)

    # トレンド（直近3ヶ月）
    trend_summaries = []
    for i in range(2, -1, -1):
        m = month - i
        y = year
        while m <= 0:
            m += 12
            y -= 1
        trend_summaries.append(monthly_summary(transactions, y, m))
    trends = detect_trend(trend_summaries)

    profile = db.query(UserProfile).filter(UserProfile.user_id == current_user.id).first()
    hint = _profile_hint(profile)

    payload = build_ai_analysis_payload(current, comparison, trends, hint)
    report = generate_household_analysis(payload)

    return {
        "year": year,
        "month": month,
        "summary": current,
        "yoy_comparison": comparison,
        "trends": trends,
        "ai_report": report,
        "disclaimer": "本レポートは情報提供を目的としており、投資助言ではありません。",
    }
