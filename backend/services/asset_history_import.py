"""
資産推移CSV（マネーフォワード）の全履歴取込サービス。

マネフォは毎回「全履歴」を再出力するため、日付単位でdedupする。
既存日付は上書きせずスキップする（過去の値をMF側が事後修正した場合は
手動でその行を削除してから再取込すること）。
"""

from sqlalchemy.orm import Session

from ..models.performance import AssetSnapshot
from .csv_parser import parse_moneyforward_asset_history_full, CSVParseError  # noqa: re-export


def import_asset_history(db: Session, user_id: str, content: bytes) -> dict:
    """資産推移CSV全履歴を取り込む。(user_id, date) でdedup、冪等。"""
    rows = parse_moneyforward_asset_history_full(content)  # raises CSVParseError if invalid

    existing_dates = {
        d for (d,) in db.query(AssetSnapshot.snapshot_date)
        .filter(AssetSnapshot.user_id == user_id).all()
    }

    new_count = 0
    skip_count = 0
    for r in rows:
        if r["date"] in existing_dates:
            skip_count += 1
            continue
        db.add(AssetSnapshot(
            user_id=user_id,
            snapshot_date=r["date"],
            total_assets_yen=r["total_assets_yen"],
            cash_assets_yen=r["cash_assets_yen"],
            investment_assets_yen=r["investment_assets_yen"],
        ))
        existing_dates.add(r["date"])
        new_count += 1

    db.commit()
    return {"status": "imported", "imported": new_count, "skipped": skip_count, "total_in_file": len(rows)}
