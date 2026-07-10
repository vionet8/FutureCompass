"""
保有銘柄の多軸タグ付け。

標準3軸（通貨・資産クラス・商品タイプ）は新規銘柄が見つかるたびに
ヒューリスティックでデフォルトタグを自動生成する（is_auto=1）。
ユーザーが画面で修正すると is_auto=0 になり、以後の自動分類で上書きされない。
カスタム軸はユーザーが追加し、値は手動でのみ設定する（自動分類の対象外）。
"""

from datetime import datetime
from sqlalchemy.orm import Session

from ..models.portfolio import ClassificationAxis, SecurityTag

BUILTIN_AXES = [
    ("currency", "通貨"),
    ("asset_class", "資産クラス"),
    ("product_type", "商品タイプ"),
    ("time_horizon", "資金の時間軸"),
]

TIME_HORIZON_AXIS_KEY = "time_horizon"
# 「3つの財布」: 長期（絶対に動かさない）/ 中期 / 短期（1年以内に使う可能性）
TIME_HORIZON_VALUES = ["長期", "中期", "短期（1年以内）"]

# 日本個別株：証券コード → (資産クラス, 商品タイプ)。通貨は常にJPY
JP_STOCK_CLASS: dict[str, tuple[str, str]] = {
    "1343": ("日本株式", "REIT"),
    "1475": ("日本株式", "TOPIX連動ETF"),
    "1478": ("日本株式", "高配当ETF"),
    "1951": ("日本株式", "建設"),
    "2003": ("日本株式", "食品"),
    "2169": ("日本株式", "情報通信"),
    "2296": ("日本株式", "食品"),
    "2393": ("日本株式", "サービス"),
    "3048": ("日本株式", "小売"),
    "3076": ("日本株式", "卸売"),
    "3231": ("日本株式", "不動産"),
    "3817": ("日本株式", "情報通信"),
    "3834": ("日本株式", "情報通信"),
    "4008": ("日本株式", "化学"),
    "4042": ("日本株式", "化学"),
    "4248": ("日本株式", "化学"),
    "4752": ("日本株式", "情報通信"),
    "4755": ("日本株式", "情報通信"),
    "5388": ("日本株式", "ガラス・土石"),
    "5803": ("日本株式", "電気機器"),
    "6073": ("日本株式", "サービス"),
    "7011": ("日本株式", "機械"),
    "7438": ("日本株式", "卸売"),
    "7820": ("日本株式", "その他製品"),
    "7994": ("日本株式", "その他製品"),
    "8130": ("日本株式", "卸売"),
    "8306": ("日本株式", "銀行"),
    "8309": ("日本株式", "銀行"),
    "8584": ("日本株式", "その他金融"),
    "8593": ("日本株式", "その他金融"),
    "9303": ("日本株式", "運輸・倉庫"),
    "9432": ("日本株式", "情報通信"),
    "9433": ("日本株式", "情報通信"),
    "9513": ("日本株式", "電力・ガス"),
    "9769": ("日本株式", "サービス"),
    "9795": ("日本株式", "サービス"),
    "9986": ("日本株式", "卸売"),
}

# 米国個別株・ETF：ティッカー → (資産クラス, 商品タイプ)。通貨は常にUSD
US_TICKER_CLASS: dict[str, tuple[str, str]] = {
    "VIG": ("米国株式", "高配当・増配ETF"),
    "VYM": ("米国株式", "高配当ETF"),
    "VDC": ("米国株式", "生活必需品セクターETF"),
    "SPYD": ("米国株式", "高配当ETF"),
    "MCD": ("米国株式", "生活必需品(個別株)"),
    "MSFT": ("米国株式", "情報技術(個別株)"),
    "JEPQ": ("米国株式", "グロース/インカムETF"),
    "SPCX": ("米国株式", "未上場テック"),
}

# 投資信託・年金：名称に含まれるキーワード → (資産クラス, 商品タイプ)。先頭一致で最初にマッチしたものを採用
FUND_CLASS_KEYWORDS: list[tuple[str, tuple[str, str]]] = [
    ("ゴールド", ("金・コモディティ", "ゴールド")),
    ("インド株", ("インド株式", "インド株インデックス")),
    ("NASDAQ", ("米国株式", "NASDAQ-100インデックス")),
    ("FANG", ("米国株式", "FANG+インデックス")),
    ("S&P500", ("米国株式", "S&P500インデックス")),
    ("米国高配当", ("米国株式", "米国高配当")),
    ("米国株式", ("米国株式", "米国株式インデックス")),
    ("米ドルMMF", ("現金", "米ドルMMF")),
    ("米ドル・リクイディティ", ("現金", "米ドルMMF")),
    ("JPXプライム", ("日本株式", "日本株インデックス")),
    ("オールカントリー", ("全世界株式", "全世界株式インデックス")),
    ("全世界株式", ("全世界株式", "全世界株式インデックス")),
    ("外国株式インデックス", ("全世界株式", "全世界株式インデックス")),
    ("外国株式ファンド", ("全世界株式", "全世界株式インデックス")),
]

CASH_KEYWORDS: list[tuple[str, tuple[str, str]]] = [
    ("米ドル", ("USD", "外貨現金")),
    ("香港ドル", ("その他外貨", "外貨現金")),
]


def ensure_builtin_axes(db: Session, user_id: str) -> dict[str, ClassificationAxis]:
    """標準3軸が無ければ作成する。key→ClassificationAxisの辞書を返す"""
    existing = {
        a.key: a for a in db.query(ClassificationAxis)
        .filter(ClassificationAxis.user_id == user_id).all()
    }
    for order, (key, label) in enumerate(BUILTIN_AXES):
        if key not in existing:
            axis = ClassificationAxis(
                user_id=user_id, key=key, label=label, is_builtin=1, display_order=order,
            )
            db.add(axis)
            existing[key] = axis
    db.commit()
    return existing


# カテゴリ→時間軸のデフォルト推定。年金は制度上引き出せないため確実に長期、
# ポイント・現金は流動性が高く近々使う想定で短期、株式・投信は長期保有前提が
# 多数派という前提でのデフォルト（住宅頭金用の課税口座など例外は手動で直す想定）。
CATEGORY_TIME_HORIZON: dict[str, str] = {
    "年金": "長期",
    "ポイント": "短期（1年以内）",
    "現金": "短期（1年以内）",
    "株式": "長期",
    "投資信託": "長期",
}


def auto_classify(category: str, name: str, symbol_code: str | None) -> dict[str, str]:
    """
    銘柄をヒューリスティックで標準4軸に分類する。
    戻り値: {"currency": ..., "asset_class": ..., "product_type": ..., "time_horizon": ...}
    未知の銘柄はasset_class/product_typeが"未分類"になる（画面で手動修正する前提）。
    time_horizonはカテゴリからの大まかな既定値であり、実際の資金使途に応じて
    ユーザーが個別に修正することを前提とする。
    """
    time_horizon = CATEGORY_TIME_HORIZON.get(category, "未分類")

    if category == "現金":
        for kw, (currency, product_type) in CASH_KEYWORDS:
            if kw in name:
                return {"currency": currency, "asset_class": "現金", "product_type": product_type, "time_horizon": time_horizon}
        return {"currency": "JPY", "asset_class": "現金", "product_type": "現金・預金", "time_horizon": time_horizon}

    if category == "株式":
        if symbol_code and symbol_code in JP_STOCK_CLASS:
            asset_class, product_type = JP_STOCK_CLASS[symbol_code]
            return {"currency": "JPY", "asset_class": asset_class, "product_type": product_type, "time_horizon": time_horizon}
        if symbol_code and symbol_code in US_TICKER_CLASS:
            asset_class, product_type = US_TICKER_CLASS[symbol_code]
            return {"currency": "USD", "asset_class": asset_class, "product_type": product_type, "time_horizon": time_horizon}
        return {"currency": "未分類", "asset_class": "未分類", "product_type": "未分類", "time_horizon": time_horizon}

    if category in ("投資信託", "年金"):
        for kw, (asset_class, product_type) in FUND_CLASS_KEYWORDS:
            if kw in name:
                return {"currency": "JPY", "asset_class": asset_class, "product_type": product_type, "time_horizon": time_horizon}
        return {"currency": "JPY", "asset_class": "未分類", "product_type": "未分類", "time_horizon": time_horizon}

    if category == "ポイント":
        return {"currency": "JPY", "asset_class": "ポイント", "product_type": "ポイント", "time_horizon": time_horizon}

    return {"currency": "未分類", "asset_class": "未分類", "product_type": "未分類", "time_horizon": time_horizon}


def apply_auto_classification(
    db: Session, user_id: str, category: str, name: str, symbol_code: str | None, security_key: str,
) -> None:
    """
    未タグの銘柄に標準3軸のデフォルト値を設定する。
    既存タグ（is_auto=0のユーザー手動修正、または既にis_auto=1で設定済み）は上書きしない。
    """
    axes = ensure_builtin_axes(db, user_id)
    existing_tags = {
        t.axis_id for t in db.query(SecurityTag)
        .filter(SecurityTag.user_id == user_id, SecurityTag.security_key == security_key).all()
    }
    values = auto_classify(category, name, symbol_code)
    for axis_key, value in values.items():
        axis = axes[axis_key]
        if axis.id in existing_tags:
            continue
        db.add(SecurityTag(
            user_id=user_id, security_key=security_key, axis_id=axis.id, value=value, is_auto=1,
        ))
