from sqlalchemy import Column, String, Integer, Float, DateTime, ForeignKey, Boolean, Enum
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
import enum
from ..core.database import Base


class EntryStatus(str, enum.Enum):
    provisional = "provisional"  # 仮計上（自己入力）
    confirmed   = "confirmed"    # 確定済み（MF取込）
    cancelled   = "cancelled"    # キャンセル（仮が不一致で除外）


class BudgetCategory(Base):
    """月次予算カテゴリ"""
    __tablename__ = "budget_categories"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)          # 例: 旅行費, お小遣い（夫）
    icon = Column(String, default="💴")
    monthly_budget = Column(Integer, nullable=False)  # 月次予算（円）
    color = Column(String, default="#6c63ff")
    sort_order = Column(Integer, default=0)
    is_shared = Column(Boolean, default=True)      # 夫婦共有フラグ（Phase2用）
    created_at = Column(DateTime, default=datetime.utcnow)

    entries = relationship("BudgetEntry", back_populates="category", cascade="all, delete-orphan")


class BudgetEntry(Base):
    """支出エントリ（仮計上 or 確定）"""
    __tablename__ = "budget_entries"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    category_id = Column(String, ForeignKey("budget_categories.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)

    description = Column(String, nullable=False)   # 内容（例: 沖縄旅行ホテル）
    amount = Column(Integer, nullable=False)        # 金額（円）
    actual_amount = Column(Integer, nullable=True)  # MF確定後の実額（差異がある場合）

    status = Column(String, default=EntryStatus.provisional)
    entry_date = Column(DateTime, nullable=False)   # 支出予定日 or 実日付
    confirmed_at = Column(DateTime, nullable=True)  # MF確定日時

    # MF照合用
    mf_transaction_id = Column(String, nullable=True)  # MFの取引ID（将来のAPI連携用）
    mf_memo = Column(String, nullable=True)             # MF側のメモ

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    category = relationship("BudgetCategory", back_populates="entries")
