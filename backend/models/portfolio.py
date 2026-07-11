from sqlalchemy import Column, String, Integer, Float, Date, DateTime, ForeignKey, UniqueConstraint
from datetime import datetime
import uuid
from ..core.database import Base


class PortfolioSnapshot(Base):
    """
    保有資産の内訳スナップショット（マネフォportfolioページの手動コピペ取込）。
    貼り付けるたびに新規スナップショットとして保存し、内訳の推移も追えるようにする。
    """
    __tablename__ = "portfolio_snapshots"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    source = Column(String, default="moneyforward_portfolio_paste")


class Holding(Base):
    """個別の保有銘柄・口座（PortfolioSnapshot 1件に対して複数行）。分類タグは持たず銘柄マスターを参照する"""
    __tablename__ = "holdings"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    snapshot_id = Column(String, ForeignKey("portfolio_snapshots.id"), nullable=False, index=True)
    category = Column(String, nullable=False)        # 現金 | 株式 | 投資信託 | 年金 | ポイント
    security_key = Column(String, nullable=False, index=True)  # SecurityTagと紐づく安定キー
    symbol_code = Column(String, nullable=True)       # 証券コード・ティッカー（あれば）
    name = Column(String, nullable=False)
    institution = Column(String, nullable=True)
    market_value_yen = Column(Integer, nullable=False)
    quantity = Column(Float, nullable=True)           # 保有株数（株式のみ。配当収入の計算に使う）


class ClassificationAxis(Base):
    """
    分類軸の定義（通貨・資産クラス・商品タイプ・資金の時間軸の標準4軸＋ユーザー追加のカスタム軸）。
    is_builtin=Trueの4軸は新規銘柄の自動分類対象、カスタム軸は手動タグ付けのみ。
    """
    __tablename__ = "classification_axes"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_axis_user_key"),)

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    key = Column(String, nullable=False)              # "currency" | "asset_class" | "product_type" | カスタムslug
    label = Column(String, nullable=False)             # 表示名
    is_builtin = Column(Integer, default=0)             # SQLiteはBoolean非対応環境もあるためInt(0/1)
    display_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class SecurityTag(Base):
    """
    銘柄マスターのタグ（security_key × axis に対して1つの値）。
    貼り付けのたびに再分類する必要がないよう、Holdingではなくsecurity_keyに紐づける。
    """
    __tablename__ = "security_tags"
    __table_args__ = (UniqueConstraint("user_id", "security_key", "axis_id", name="uq_tag_user_security_axis"),)

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    security_key = Column(String, nullable=False, index=True)
    axis_id = Column(String, ForeignKey("classification_axes.id"), nullable=False, index=True)
    value = Column(String, nullable=False)
    is_auto = Column(Integer, default=1)  # 1=自動分類のデフォルト値、0=ユーザーが手動で確定済み
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class WealthBucketGoal(Base):
    """
    「3つの財布」（長期・中期・短期）ごとの目標金額。
    time_horizon軸のタグ値(bucket_value)ごとに1件、目標未設定なら行自体が存在しない。
    """
    __tablename__ = "wealth_bucket_goals"
    __table_args__ = (UniqueConstraint("user_id", "bucket_value", name="uq_bucket_goal_user_value"),)

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    bucket_value = Column(String, nullable=False)  # "長期" | "中期" | "短期（1年以内）"
    target_amount_yen = Column(Integer, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SecurityExclusion(Base):
    """
    銘柄マスター単位の「計算対象外」フラグ。行が存在する=除外。
    内訳グラフ・3つの財布などの集計から一律で除外したい銘柄
    （例: 少額のポイント類をまとめて対象外にしたい場合）に使う。
    """
    __tablename__ = "security_exclusions"
    __table_args__ = (UniqueConstraint("user_id", "security_key", name="uq_exclusion_user_security"),)

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    security_key = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class SecurityQuote(Base):
    """
    銘柄の最新株価キャッシュ（Yahoo Finance chart API取得結果、全ユーザー共有）。
    yahoo_symbolはYahoo Finance形式（日本株は"8306.T"、米国株はティッカーそのまま）。
    """
    __tablename__ = "security_quotes"

    yahoo_symbol = Column(String, primary_key=True)
    latest_price = Column(Float, nullable=False)
    currency = Column(String, nullable=False)  # "JPY" | "USD" 等（Yahooのメタ情報から）
    fetched_at = Column(DateTime, default=datetime.utcnow)


class DividendEvent(Base):
    """
    銘柄の配当履歴キャッシュ（1株あたり配当、権利落ち日ベース、全ユーザー共有）。
    金額はYahooが返すその銘柄の建値通貨（日本株=円、米国株=ドル）。
    """
    __tablename__ = "dividend_events"
    __table_args__ = (UniqueConstraint("yahoo_symbol", "ex_date", name="uq_dividend_symbol_date"),)

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    yahoo_symbol = Column(String, nullable=False, index=True)
    ex_date = Column(Date, nullable=False)
    amount = Column(Float, nullable=False)  # 1株あたり配当（建値通貨）
    fetched_at = Column(DateTime, default=datetime.utcnow)
