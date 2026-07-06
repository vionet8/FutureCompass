"""
MF CSV と仮計上エントリの照合（リコンサイル）サービス。

照合ロジック:
  1. MF CSVの各行に対して、同カテゴリの仮計上エントリを候補として探す
  2. 日付±3日・金額±10%の範囲でマッチング
  3. マッチした仮エントリ → confirmed に昇格（actual_amount を更新）
  4. マッチしなかった MF行 → 新規 confirmed エントリとして登録
  5. 照合結果サマリーを返す
"""

import pandas as pd
import io
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from ..models.budget import BudgetEntry, BudgetCategory, EntryStatus


def reconcile_mf_csv(
    db: Session,
    user_id: str,
    category_id: str,
    csv_content: bytes,
    year_month: str,  # "2026-06"
) -> dict:
    """
    マネーフォワードCSV（家計簿エクスポート）と仮計上を照合する。
    MF CSVフォーマット想定: 日付,内容,金額（支出）,金額（収入）,残高,メモ,振替
    """
    try:
        df = pd.read_csv(io.BytesIO(csv_content), encoding="utf-8-sig")
        df.columns = df.columns.str.strip()
    except Exception as e:
        return {"error": f"CSV解析エラー: {e}"}

    # 支出行のみ抽出（金額（支出）列が正の行）
    expense_col = _find_col(df, ["金額（支出）", "出金金額", "支出"])
    date_col = _find_col(df, ["日付", "取引日"])
    memo_col = _find_col(df, ["内容", "摘要"])

    if not expense_col:
        return {"error": "支出金額列が見つかりません"}

    df["_amount"] = (
        df[expense_col].astype(str)
        .str.replace(",", "").str.replace("−", "-").str.replace("－", "-")
    )
    df = df[df["_amount"].str.match(r"^\d+")]
    df["_amount"] = df["_amount"].astype(int)
    df["_date"] = pd.to_datetime(df[date_col], errors="coerce")
    df["_memo"] = df[memo_col].fillna("") if memo_col else ""

    # 対象月でフィルタ
    ym = pd.Timestamp(year_month + "-01")
    df = df[
        (df["_date"].dt.year == ym.year) &
        (df["_date"].dt.month == ym.month)
    ]

    # 既存の仮計上エントリを取得
    provisionals = db.query(BudgetEntry).filter(
        BudgetEntry.user_id == user_id,
        BudgetEntry.category_id == category_id,
        BudgetEntry.status == EntryStatus.provisional,
    ).all()

    matched = []
    unmatched_mf = []
    used_ids = set()

    for _, row in df.iterrows():
        mf_amount = row["_amount"]
        mf_date = row["_date"].to_pydatetime() if pd.notna(row["_date"]) else None
        mf_memo = row["_memo"]

        # 照合候補を探す（日付±3日・金額±10%）
        best = None
        best_score = float("inf")
        for entry in provisionals:
            if entry.id in used_ids:
                continue
            if mf_date and entry.entry_date:
                day_diff = abs((mf_date.date() - entry.entry_date.date()).days)
            else:
                day_diff = 999
            amount_diff_pct = abs(mf_amount - entry.amount) / max(entry.amount, 1)

            if day_diff <= 3 and amount_diff_pct <= 0.10:
                score = day_diff + amount_diff_pct * 10
                if score < best_score:
                    best = entry
                    best_score = score

        if best:
            # 照合成功 → confirmed に昇格
            best.status = EntryStatus.confirmed
            best.actual_amount = mf_amount
            best.confirmed_at = datetime.utcnow()
            best.mf_memo = mf_memo
            used_ids.add(best.id)
            matched.append({
                "entry_id": best.id,
                "description": best.description,
                "provisional_amount": best.amount,
                "actual_amount": mf_amount,
                "diff": mf_amount - best.amount,
            })
        else:
            # 照合失敗 → 新規 confirmed エントリを作成
            new_entry = BudgetEntry(
                category_id=category_id,
                user_id=user_id,
                description=mf_memo or "MF取込",
                amount=mf_amount,
                actual_amount=mf_amount,
                status=EntryStatus.confirmed,
                entry_date=mf_date or datetime.utcnow(),
                confirmed_at=datetime.utcnow(),
                mf_memo=mf_memo,
            )
            db.add(new_entry)
            unmatched_mf.append({"memo": mf_memo, "amount": mf_amount})

    db.commit()
    return {
        "matched_count": len(matched),
        "new_confirmed_count": len(unmatched_mf),
        "matched": matched,
        "new_entries": unmatched_mf,
    }


def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def calc_budget_summary(
    db: Session, user_id: str, year_month: str
) -> list[dict]:
    """
    カテゴリ別の予算サマリーを返す。
    confirmed / provisional それぞれの合計と残りを計算。
    """
    ym = datetime.strptime(year_month, "%Y-%m")
    month_start = ym.replace(day=1)
    if ym.month == 12:
        month_end = ym.replace(year=ym.year + 1, month=1, day=1)
    else:
        month_end = ym.replace(month=ym.month + 1, day=1)

    categories = db.query(BudgetCategory).filter(
        BudgetCategory.user_id == user_id
    ).order_by(BudgetCategory.sort_order).all()

    summaries = []
    for cat in categories:
        entries = [
            e for e in cat.entries
            if month_start <= e.entry_date < month_end
            and e.status != EntryStatus.cancelled
        ]

        confirmed_total = sum(
            (e.actual_amount or e.amount)
            for e in entries if e.status == EntryStatus.confirmed
        )
        provisional_total = sum(
            e.amount for e in entries if e.status == EntryStatus.provisional
        )
        total_spent = confirmed_total + provisional_total
        remaining = cat.monthly_budget - total_spent
        usage_pct = min(100, total_spent / cat.monthly_budget * 100) if cat.monthly_budget > 0 else 0

        summaries.append({
            "category_id": cat.id,
            "name": cat.name,
            "icon": cat.icon,
            "color": cat.color,
            "monthly_budget": cat.monthly_budget,
            "confirmed_total": confirmed_total,
            "provisional_total": provisional_total,
            "total_spent": total_spent,
            "remaining": remaining,
            "usage_pct": round(usage_pct, 1),
            "entries": [
                {
                    "id": e.id,
                    "description": e.description,
                    "amount": e.amount,
                    "actual_amount": e.actual_amount,
                    "status": e.status,
                    "entry_date": e.entry_date.isoformat(),
                }
                for e in sorted(entries, key=lambda x: x.entry_date, reverse=True)
            ],
        })

    return summaries
