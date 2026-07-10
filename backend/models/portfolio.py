from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, UniqueConstraint
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
