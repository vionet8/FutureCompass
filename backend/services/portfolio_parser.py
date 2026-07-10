"""
マネフォ「保有資産（portfolio）」ページからのコピー&ペーストテキストを解析する。

このページに公式CSVエクスポートはない。ブラウザでテーブルを選択コピーすると、
タブ区切りの行が並ぶが、列数の多いセクション（株式・投資信託）はヘッダーセルが
折り返されて「1ラベル1行」に分解されて出力される（現金・年金・ポイントは1行ヘッダー）。
この形式の違いを吸収するため、ヘッダー行は「数字を含まない行」として一律スキップし、
データ行は「数字を含むタブ区切り行」として扱う。
"""

import re
from dataclasses import dataclass
from typing import Optional

SECTION_MARKERS = {
    "預金・現金": "現金",
    "株式(現物)": "株式",
    "投資信託": "投資信託",
    "年金": "年金",
    "ポイント": "ポイント",
}

# 各セクションの列レイアウト（タブ分割後のインデックス）
# 値は (name_idx, value_idx, institution_idx, symbol_code_idx|None)
SECTION_COLUMNS = {
    "現金": {"name": 0, "value": 1, "institution": 2, "symbol": None},
    "株式": {"name": 1, "value": 5, "institution": 9, "symbol": 0},
    "投資信託": {"name": 0, "value": 4, "institution": 8, "symbol": None},
    "年金": {"name": 0, "value": 2, "institution": None, "symbol": None},  # value=現在価値
    "ポイント": {"name": 0, "value": 4, "institution": 6, "symbol": None},  # value=現在の価値
}


@dataclass
class ParsedHolding:
    category: str
    name: str
    market_value_yen: int
    institution: Optional[str]
    symbol_code: Optional[str]

    @property
    def security_key(self) -> str:
        """
        銘柄マスター（タグ付け対象）を一意に識別するキー。
        証券コードがあればそれを使い、無ければカテゴリ+名称で代用する
        （現金口座・投資信託・年金・ポイントは証券コードを持たないため）。
        """
        if self.symbol_code:
            return f"{self.category}:{self.symbol_code}"
        return f"{self.category}:{self.name}"


def _parse_yen(text: str) -> Optional[int]:
    """'174,982円' や '-4,746円' を整数に変換。空文字・変換不能はNone"""
    if not text:
        return None
    cleaned = text.strip().replace(",", "").replace("円", "")
    if not cleaned or cleaned in ("-", "―"):
        return None
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def _looks_like_data_row(line: str) -> bool:
    """ヘッダーラベル行（数字を含まない）と実データ行（金額・数量等の数字を含む）を判別"""
    return any(ch.isdigit() for ch in line)


def parse_portfolio_paste(text: str) -> list[ParsedHolding]:
    """
    マネフォportfolioページのコピペテキストを解析し、保有銘柄・口座のリストを返す。
    未知のセクション・解析できない行は無視する（部分的に崩れたペーストでも動くように）。
    """
    holdings: list[ParsedHolding] = []
    current_section: Optional[str] = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\n")
        stripped = line.strip()
        if not stripped:
            continue

        # セクション見出し判定（前方一致。"ポイント "のような末尾空白にも対応）
        matched_section = None
        for marker, category in SECTION_MARKERS.items():
            if stripped.startswith(marker):
                matched_section = category
                break
        if matched_section:
            current_section = matched_section
            continue

        if current_section is None:
            continue
        if stripped.startswith("合計"):
            continue
        if not _looks_like_data_row(line):
            continue  # ヘッダーラベル行（数字なし）

        cols = SECTION_COLUMNS[current_section]
        fields = line.split("\t")

        max_idx = max(v for v in cols.values() if v is not None)
        if len(fields) <= max_idx:
            continue  # 列数不足の壊れた行はスキップ

        name = fields[cols["name"]].strip()
        value = _parse_yen(fields[cols["value"]])
        if not name or value is None:
            continue

        institution = None
        if cols["institution"] is not None:
            institution = fields[cols["institution"]].strip() or None

        symbol_code = None
        if cols["symbol"] is not None:
            symbol_code = fields[cols["symbol"]].strip() or None

        holdings.append(ParsedHolding(
            category=current_section,
            name=name,
            market_value_yen=value,
            institution=institution,
            symbol_code=symbol_code,
        ))

    return holdings
