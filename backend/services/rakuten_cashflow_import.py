"""
楽天証券「入出金履歴」CSVから投資キャッシュフローを取り込むサービス。

CSVには証券口座内の自動振替（マネーブリッジ等）が大量に含まれ、その多くは
現金同士の移動（投資資産額を動かさない）なので、csv_parser.RAKUTEN_CASHFLOW_CATEGORIES
で「投資への流入/流出/対象外」に分類したうえで、対象行のみCashFlowEventとして保存する。

このCSVには一意なIDが無いため、(date, content, in_yen, out_yen) の組で重複排除する。
"""

from sqlalchemy.orm import Session

from ..models.performance import CashFlowEvent
from .csv_parser import parse_rakuten_cashflow, CSVParseError  # noqa: re-export


def import_rakuten_cashflow(db: Session, user_id: str, content: bytes) -> dict:
    """
    楽天証券の入出金履歴CSVを取り込む。
    分類済み(inflow/outflow)の行のみCashFlowEventとして保存し、対象外(exclude)は無視、
    未分類(unclassified)はスキップしつつ件数を報告する（新カテゴリの見落とし検知用）。
    """
    rows = parse_rakuten_cashflow(content)  # raises CSVParseError if invalid

    existing_keys = {
        (e.flow_date, e.memo)
        for e in db.query(CashFlowEvent)
        .filter(CashFlowEvent.user_id == user_id, CashFlowEvent.source == "rakuten_csv")
        .all()
    }

    imported = 0
    skipped_duplicate = 0
    excluded = 0
    unclassified = 0
    for r in rows:
        if r["classification"] == "exclude":
            excluded += 1
            continue
        if r["classification"] == "unclassified":
            unclassified += 1
            continue

        memo = r["content"]
        key = (r["date"], memo)
        if key in existing_keys:
            skipped_duplicate += 1
            continue

        db.add(CashFlowEvent(
            user_id=user_id,
            flow_date=r["date"],
            amount_yen=int(r["amount_yen"]),
            flow_type="deposit" if r["amount_yen"] >= 0 else "withdrawal",
            memo=memo,
            source="rakuten_csv",
        ))
        existing_keys.add(key)
        imported += 1

    db.commit()
    return {
        "status": "imported",
        "imported": imported,
        "skipped_duplicate": skipped_duplicate,
        "excluded": excluded,
        "unclassified": unclassified,
        "total_in_file": len(rows),
    }
