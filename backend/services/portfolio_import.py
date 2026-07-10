"""マネフォportfolioページのコピペテキストを取り込み、スナップショット+保有銘柄として保存する"""

from sqlalchemy.orm import Session

from ..models.portfolio import PortfolioSnapshot, Holding
from .portfolio_parser import parse_portfolio_paste


class PortfolioParseError(Exception):
    pass


def import_portfolio_paste(db: Session, user_id: str, text: str) -> dict:
    """
    貼り付けテキストを解析し、新規PortfolioSnapshotとして保存する。
    貼り付けるたびに新規スナップショットを作る（内訳の推移を追えるように）。
    新規銘柄は標準3軸（通貨・資産クラス・商品タイプ）を自動分類する。
    """
    from .classification import apply_auto_classification

    holdings = parse_portfolio_paste(text)
    if not holdings:
        raise PortfolioParseError(
            "保有銘柄を認識できませんでした。マネフォの保有資産ページ全体をコピーして貼り付けてください。"
        )

    snapshot = PortfolioSnapshot(user_id=user_id, source="moneyforward_portfolio_paste")
    db.add(snapshot)
    db.flush()  # snapshot.id を確定

    for h in holdings:
        db.add(Holding(
            snapshot_id=snapshot.id,
            category=h.category,
            security_key=h.security_key,
            symbol_code=h.symbol_code,
            name=h.name,
            institution=h.institution,
            market_value_yen=h.market_value_yen,
        ))
        apply_auto_classification(db, user_id, h.category, h.name, h.symbol_code, h.security_key)

    db.commit()
    return {
        "status": "imported",
        "snapshot_id": snapshot.id,
        "holdings_count": len(holdings),
        "total_value_yen": sum(h.market_value_yen for h in holdings),
    }
