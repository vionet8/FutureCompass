from sqlalchemy import Column, String, Integer, Float, Date, DateTime, ForeignKey, UniqueConstraint
from datetime import datetime
import uuid
from ..core.database import Base


class AssetSnapshot(Base):
    """資産スナップショット（日次、マネーフォワード資産推移CSVの全履歴取込用）"""
    __tablename__ = "asset_snapshots"
    __table_args__ = (UniqueConstraint("user_id", "snapshot_date", name="uq_asset_snapshot_user_date"),)

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    snapshot_date = Column(Date, nullable=False, index=True)
    total_assets_yen = Column(Integer, nullable=False)
    cash_assets_yen = Column(Integer, nullable=True)
    investment_assets_yen = Column(Integer, nullable=True)
    source = Column(String, default="moneyforward")
    imported_at = Column(DateTime, default=datetime.utcnow)


class CashFlowEvent(Base):
    """入出金イベント（手動入力。将来は楽天CSV取込にも流用）"""
    __tablename__ = "cash_flow_events"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    flow_date = Column(Date, nullable=False, index=True)
    amount_yen = Column(Integer, nullable=False)   # 入金=正、出金=負
    flow_type = Column(String, nullable=False)     # "deposit" | "withdrawal"
    memo = Column(String, nullable=True)
    source = Column(String, default="manual")
    created_at = Column(DateTime, default=datetime.utcnow)


class BenchmarkPrice(Base):
    """ベンチマーク指数の日次終値キャッシュ（stooq.com取得結果、全ユーザー共有）"""
    __tablename__ = "benchmark_prices"
    __table_args__ = (UniqueConstraint("symbol", "price_date", name="uq_benchmark_symbol_date"),)

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    symbol = Column(String, nullable=False, index=True)
    price_date = Column(Date, nullable=False, index=True)
    close_price = Column(Float, nullable=False)
    fetched_at = Column(DateTime, default=datetime.utcnow)
