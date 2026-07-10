"""
保有銘柄の多軸タグ付け。

標準5軸（通貨・資産クラス・商品タイプ・資金の時間軸・景気敏感度）は新規銘柄が
見つかるたびにヒューリスティックでデフォルトタグを自動生成する（is_auto=1）。
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
    ("cyclicality", "景気敏感度"),
]

TIME_HORIZON_AXIS_KEY = "time_horizon"
# 「3つの財布」: 長期（絶対に動かさない）/ 中期 / 短期（1年以内に使う可能性）
TIME_HORIZON_VALUES = ["長期", "中期", "短期（1年以内）"]

CYCLICALITY_AXIS_KEY = "cyclicality"
CYCLICALITY_VALUES = ["景気敏感", "ディフェンシブ"]

# 日本個別株：証券コード → (資産クラス, 商品タイプ)。通貨は常にJPY。
# 商品タイプはGICS類似の大分類（金融・情報技術・資本財等）にまとめる。
# 東証33業種そのままだと「食品」「情報通信」等が銘柄ごとに重複表示され、
# 内訳リストが細かくなりすぎて全体像（現金がどこにあるか等）が埋もれるため。
JP_STOCK_CLASS: dict[str, tuple[str, str]] = {
    "1343": ("日本株式", "不動産"),          # NFJ-REIT
    "1475": ("日本株式", "日本株式ETF"),      # TOPIX連動ETF
    "1478": ("日本株式", "日本株式ETF"),      # 高配当ETF
    "1951": ("日本株式", "資本財"),          # 建設
    "2003": ("日本株式", "生活必需品"),       # 食品
    "2169": ("日本株式", "情報技術"),         # ITサービス
    "2296": ("日本株式", "生活必需品"),       # 食品
    "2393": ("日本株式", "一般消費財・サービス"),
    "3048": ("日本株式", "一般消費財・サービス"),  # 小売
    "3076": ("日本株式", "一般消費財・サービス"),  # 卸売
    "3231": ("日本株式", "不動産"),
    "3817": ("日本株式", "情報技術"),
    "3834": ("日本株式", "情報技術"),
    "4008": ("日本株式", "素材"),            # 化学
    "4042": ("日本株式", "素材"),
    "4248": ("日本株式", "素材"),
    "4752": ("日本株式", "情報技術"),
    "4755": ("日本株式", "通信サービス"),      # 楽天グループ
    "5388": ("日本株式", "素材"),            # ガラス・土石
    "5803": ("日本株式", "資本財"),          # 電線・電気機器
    "6073": ("日本株式", "一般消費財・サービス"),
    "7011": ("日本株式", "資本財"),          # 機械
    "7438": ("日本株式", "一般消費財・サービス"),
    "7820": ("日本株式", "一般消費財・サービス"),
    "7994": ("日本株式", "一般消費財・サービス"),
    "8130": ("日本株式", "一般消費財・サービス"),
    "8306": ("日本株式", "金融"),            # 銀行
    "8309": ("日本株式", "金融"),
    "8584": ("日本株式", "金融"),            # その他金融
    "8593": ("日本株式", "金融"),
    "9303": ("日本株式", "資本財"),          # 運輸・倉庫
    "9432": ("日本株式", "通信サービス"),      # NTT
    "9433": ("日本株式", "通信サービス"),      # KDDI
    "9513": ("日本株式", "公益事業"),         # 電力
    "9769": ("日本株式", "一般消費財・サービス"),
    "9795": ("日本株式", "一般消費財・サービス"),
    "9986": ("日本株式", "一般消費財・サービス"),
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

# 米国個別株・ETFは商品タイプが戦略ベースの名称（高配当ETF等）でセクター名と
# 一致しないため、景気敏感度は銘柄ごとに個別指定する（未指定はNoneのままにし、
# SECTOR_CYCLICALITYへのフォールバックもしない＝「未分類」になる）
US_TICKER_CYCLICALITY: dict[str, str] = {
    "VDC": "ディフェンシブ",   # 生活必需品セクター
    "MCD": "ディフェンシブ",   # 生活必需品寄りの外食
    "MSFT": "景気敏感",       # 情報技術
    "JEPQ": "景気敏感",       # Nasdaq/テック比率が高い
    "SPCX": "景気敏感",       # 未上場テック
    # VIG/VYM/SPYDは特定セクターに偏らない分散型高配当ETFのため未分類のまま
}

# 商品タイプ（GICS類似の大分類）→ 景気敏感度。日本株のproduct_typeに対して適用する。
SECTOR_CYCLICALITY: dict[str, str] = {
    "金融": "景気敏感",
    "情報技術": "景気敏感",
    "資本財": "景気敏感",
    "一般消費財・サービス": "景気敏感",
    "素材": "景気敏感",
    "不動産": "景気敏感",
    "生活必需品": "ディフェンシブ",
    "公益事業": "ディフェンシブ",
    "通信サービス": "ディフェンシブ",
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
    """標準軸が無ければ作成する。key→ClassificationAxisの辞書を返す"""
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
    銘柄をヒューリスティックで標準5軸に分類する。
    戻り値: {"currency", "asset_class", "product_type", "time_horizon", "cyclicality"}
    未知の銘柄はasset_class/product_typeが"未分類"になる（画面で手動修正する前提）。
    cyclicalityは日本株のproduct_type(セクター大分類)からSECTOR_CYCLICALITYで
    自動導出する。米国個別株・ETFはUS_TICKER_CYCLICALITYで個別指定。
    分散型インデックスファンド等、性格が定まらないものは「未分類」のままにする。
    """
    time_horizon = CATEGORY_TIME_HORIZON.get(category, "未分類")

    if category == "現金":
        for kw, (currency, product_type) in CASH_KEYWORDS:
            if kw in name:
                return {"currency": currency, "asset_class": "現金", "product_type": product_type,
                        "time_horizon": time_horizon, "cyclicality": "未分類"}
        return {"currency": "JPY", "asset_class": "現金", "product_type": "現金・預金",
                "time_horizon": time_horizon, "cyclicality": "未分類"}

    if category == "株式":
        if symbol_code and symbol_code in JP_STOCK_CLASS:
            asset_class, product_type = JP_STOCK_CLASS[symbol_code]
            cyclicality = SECTOR_CYCLICALITY.get(product_type, "未分類")
            return {"currency": "JPY", "asset_class": asset_class, "product_type": product_type,
                    "time_horizon": time_horizon, "cyclicality": cyclicality}
        if symbol_code and symbol_code in US_TICKER_CLASS:
            asset_class, product_type = US_TICKER_CLASS[symbol_code]
            cyclicality = US_TICKER_CYCLICALITY.get(symbol_code, "未分類")
            return {"currency": "USD", "asset_class": asset_class, "product_type": product_type,
                    "time_horizon": time_horizon, "cyclicality": cyclicality}
        return {"currency": "未分類", "asset_class": "未分類", "product_type": "未分類",
                "time_horizon": time_horizon, "cyclicality": "未分類"}

    if category in ("投資信託", "年金"):
        for kw, (asset_class, product_type) in FUND_CLASS_KEYWORDS:
            if kw in name:
                return {"currency": "JPY", "asset_class": asset_class, "product_type": product_type,
                        "time_horizon": time_horizon, "cyclicality": "未分類"}
        return {"currency": "JPY", "asset_class": "未分類", "product_type": "未分類",
                "time_horizon": time_horizon, "cyclicality": "未分類"}

    if category == "ポイント":
        return {"currency": "JPY", "asset_class": "ポイント", "product_type": "ポイント",
                "time_horizon": time_horizon, "cyclicality": "未分類"}

    return {"currency": "未分類", "asset_class": "未分類", "product_type": "未分類",
            "time_horizon": time_horizon, "cyclicality": "未分類"}


def apply_auto_classification(
    db: Session, user_id: str, category: str, name: str, symbol_code: str | None, security_key: str,
) -> None:
    """
    未タグの銘柄に標準軸のデフォルト値を設定する。
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
