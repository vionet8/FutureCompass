"""マネフォportfolioページのコピペテキストを取り込み、スナップショット+保有銘柄として保存する"""

from sqlalchemy.orm import Session

from ..models.portfolio import PortfolioSnapshot, Holding
from .portfolio_parser import parse_portfolio_paste_with_sections, SECTION_MARKERS

ALL_SECTIONS = set(SECTION_MARKERS.values())  # {"現金", "株式", "投資信託", "年金", "ポイント"}


class PortfolioParseError(Exception):
    pass


def import_portfolio_paste(db: Session, user_id: str, text: str) -> dict:
    """
    貼り付けテキストを解析し、新規PortfolioSnapshotとして保存する。
    貼り付けるたびに新規スナップショットを作る（内訳の推移を追えるように）。
    新規銘柄は標準の分類軸を自動分類する。

    戻り値にmissing_sectionsを含める。マネフォの保有資産ページは「預金・現金」
    「株式(現物)」「投資信託」「年金」「ポイント」の5セクションで構成されるが、
    コピー範囲の開始位置が少しずれるだけで先頭セクション（多くは現金）が
    丸ごと選択範囲から漏れることがある。パーサー自体は検出できたセクションだけで
    正常に取り込めてしまい、欠落に気づく手立てが無かったため、検出セクション数を
    明示的に返して呼び出し側で警告できるようにする。
    """
    from .classification import apply_auto_classification

    holdings, detected_sections = parse_portfolio_paste_with_sections(text)
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
            quantity=h.quantity,
        ))
        apply_auto_classification(db, user_id, h.category, h.name, h.symbol_code, h.security_key)

    db.commit()
    return {
        "status": "imported",
        "snapshot_id": snapshot.id,
        "holdings_count": len(holdings),
        "total_value_yen": sum(h.market_value_yen for h in holdings),
        "missing_sections": sorted(ALL_SECTIONS - detected_sections),
    }
