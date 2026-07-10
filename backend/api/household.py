from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.orm import Session
from sqlalchemy import extract, func, text
from typing import Optional
from pathlib import Path
from pydantic import BaseModel

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
from ..services.mf_import import (
    import_file_content,
    scan_directory_for_user,
    default_watch_directory,
)
from ..models.auto_import import AutoImportConfig, ImportedFile
from ..services.ai_report import generate_household_analysis
from ..services.transaction_rules import (
    apply_rules_and_overrides,
    list_rules,
    create_rule,
    delete_rule,
    set_transaction_override,
)
from ..services.transaction_ai_review import request_ai_suggestions
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
    result = import_file_content(db, current_user.id, file.filename, content, source="manual")

    if result["status"] == "not_mf_csv":
        raise HTTPException(status_code=422, detail="マネーフォワードの入出金明細CSVではないようです")
    if result["status"] == "no_transactions":
        raise HTTPException(status_code=422, detail="有効なトランザクションが見つかりませんでした")
    if result["status"] == "already_imported":
        raise HTTPException(status_code=409, detail="このファイルは既にインポート済みです")

    # インポートされた月の一覧（再パースせずDBから）
    transactions = parse_mf_csv(content)
    months = sorted(set(
        (t["date"].year, t["date"].month)
        for t in transactions
        if t["is_target"] and not t["is_transfer"]
    ), reverse=True)

    return {
        "imported": result["imported"],
        "skipped": result["skipped"],
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


def _load_transactions(user_id: str, db: Session, apply_corrections: bool = True) -> list[dict]:
    """
    取引一覧を返す。apply_corrections=True（既定）の場合、個別上書き・パターンルールを
    適用した実効値（category_major/category_minor/is_transfer）に差し替える。
    元のインポート値は category_major_raw 等に残る。
    """
    rows = (
        db.query(MFTransaction)
        .filter(MFTransaction.user_id == user_id)
        .order_by(MFTransaction.transaction_date)
        .all()
    )
    transactions = [
        {
            "id": r.id,
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
            "category_major_override": r.category_major_override,
            "category_minor_override": r.category_minor_override,
            "is_transfer_override": r.is_transfer_override,
        }
        for r in rows
    ]
    if not apply_corrections:
        return transactions

    rules = list_rules(db, user_id)
    return apply_rules_and_overrides(transactions, rules)


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


# ──────────────────────────────────────────────
# 取引の分類修正（個別上書き・パターンルール・AI提案）
# ──────────────────────────────────────────────

class TransactionOverrideInput(BaseModel):
    category_major: Optional[str] = None
    category_minor: Optional[str] = None
    is_transfer: Optional[bool] = None


class RuleInput(BaseModel):
    match_field: str  # "description" | "institution"
    match_value: str
    set_is_transfer: Optional[bool] = None
    set_category_major: Optional[str] = None
    set_category_minor: Optional[str] = None


@router.put("/transactions/{transaction_id}/override")
def put_transaction_override(
    transaction_id: str,
    body: TransactionOverrideInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """1件の取引を手動で修正する（再インポートしても保持される）"""
    txn = set_transaction_override(
        db, current_user.id, transaction_id,
        category_major=body.category_major,
        category_minor=body.category_minor,
        is_transfer=body.is_transfer,
        source="manual",
    )
    if txn is None:
        raise HTTPException(status_code=404, detail="取引が見つかりません")
    return {"status": "updated", "transaction_id": transaction_id}


@router.get("/rules")
def get_rules(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """登録済みのパターンルール一覧を返す"""
    rules = list_rules(db, current_user.id)
    return [
        {
            "id": r.id,
            "match_field": r.match_field,
            "match_value": r.match_value,
            "set_is_transfer": r.set_is_transfer,
            "set_category_major": r.set_category_major,
            "set_category_minor": r.set_category_minor,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rules
    ]


@router.post("/rules")
def post_rule(
    body: RuleInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """パターンルールを追加する。既存の該当取引にも読み込み時点で即座に反映される"""
    try:
        rule = create_rule(
            db, current_user.id, body.match_field, body.match_value,
            set_is_transfer=body.set_is_transfer,
            set_category_major=body.set_category_major,
            set_category_minor=body.set_category_minor,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"id": rule.id, "match_field": rule.match_field, "match_value": rule.match_value}


@router.delete("/rules/{rule_id}")
def delete_rule_endpoint(
    rule_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """パターンルールを削除する"""
    ok = delete_rule(db, current_user.id, rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="ルールが見つかりません")
    return {"deleted": True}


@router.post("/ai-review")
def post_ai_review(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    直近の取引をAIでレビューし、振替漏れ・分類誤りの疑いがある取引の修正案を返す。
    ここでは修正を確定させない（提案の提示のみ）。適用は
    PUT /transactions/{id}/override または POST /rules で行う。
    """
    transactions = _load_transactions(current_user.id, db, apply_corrections=False)
    if not transactions:
        return {"has_data": False, "message": "取引データがありません。先にCSVを取り込んでください。"}

    suggestions = request_ai_suggestions(transactions)

    txn_by_id = {t["id"]: t for t in transactions}
    enriched = []
    for s in suggestions:
        txn = txn_by_id.get(s.get("transaction_id"))
        if txn is None:
            continue
        enriched.append({
            "transaction_id": txn["id"],
            "date": txn["date"].isoformat(),
            "description": txn["description"],
            "amount_yen": txn["amount_yen"],
            "institution": txn["institution"],
            "current_category_major": txn["category_major"],
            "current_category_minor": txn["category_minor"],
            "current_is_transfer": txn["is_transfer"],
            "issue": s.get("issue"),
            "suggested_category_major": s.get("suggested_category_major"),
            "suggested_category_minor": s.get("suggested_category_minor"),
            "suggested_is_transfer": s.get("suggested_is_transfer"),
            "reasoning": s.get("reasoning"),
        })

    return {"has_data": True, "reviewed_count": len(transactions), "suggestions": enriched}


# ──────────────────────────────────────────────
# 自動取込（フォルダ監視）
# ──────────────────────────────────────────────

class AutoImportInput(BaseModel):
    enabled: bool = True
    directory: str = ""


def _config_response(config: Optional[AutoImportConfig], db: Session, user_id: str) -> dict:
    recent_files = (
        db.query(ImportedFile)
        .filter(ImportedFile.user_id == user_id)
        .order_by(ImportedFile.imported_at.desc())
        .limit(10)
        .all()
    )
    return {
        "configured": config is not None,
        "enabled": config.enabled if config else False,
        "directory": config.directory if config else default_watch_directory(),
        "last_scanned_at": config.last_scanned_at.isoformat() if config and config.last_scanned_at else None,
        "recent_files": [
            {
                "file_name": f.file_name,
                "imported": f.imported_count,
                "skipped": f.skipped_count,
                "source": f.source,
                "imported_at": f.imported_at.isoformat() if f.imported_at else None,
            }
            for f in recent_files
        ],
    }


@router.get("/auto-import")
def get_auto_import_config(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """自動取込の設定と直近の取込履歴を返す"""
    config = db.query(AutoImportConfig).filter(AutoImportConfig.user_id == current_user.id).first()
    return _config_response(config, db, current_user.id)


@router.put("/auto-import")
def update_auto_import_config(
    req: AutoImportInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """自動取込設定を保存する"""
    directory = req.directory.strip() or default_watch_directory()
    if req.enabled and not Path(directory).is_dir():
        raise HTTPException(status_code=422, detail=f"フォルダが見つかりません: {directory}")

    config = db.query(AutoImportConfig).filter(AutoImportConfig.user_id == current_user.id).first()
    if config:
        config.directory = directory
        config.enabled = req.enabled
    else:
        config = AutoImportConfig(
            user_id=current_user.id,
            directory=directory,
            enabled=req.enabled,
        )
        db.add(config)
    db.commit()
    return _config_response(config, db, current_user.id)


@router.post("/auto-import/scan-now")
def scan_now(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """監視フォルダを今すぐスキャンして取り込む"""
    config = db.query(AutoImportConfig).filter(AutoImportConfig.user_id == current_user.id).first()
    if not config:
        raise HTTPException(status_code=404, detail="自動取込が未設定です。先に設定を保存してください")

    results = scan_directory_for_user(db, config)
    return {
        "scanned_directory": config.directory,
        "imported_files": results,
        "total_new_transactions": sum(r["imported"] for r in results),
        **_config_response(config, db, current_user.id),
    }
