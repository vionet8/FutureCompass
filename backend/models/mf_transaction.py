from sqlalchemy import Column, String, Integer, Boolean, Date, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
from ..core.database import Base


class MFTransaction(Base):
    __tablename__ = "mf_transactions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    mf_id = Column(String, nullable=True)           # MF側のID（重複チェック用）
    transaction_date = Column(Date, nullable=False)
    description = Column(String, nullable=True)
    amount_yen = Column(Integer, nullable=False)     # 正=収入、負=支出（円）
    institution = Column(String, nullable=True)
    category_major = Column(String, nullable=True)  # 大項目
    category_minor = Column(String, nullable=True)  # 中項目
    memo = Column(String, nullable=True)
    is_transfer = Column(Boolean, default=False)    # 振替フラグ
    is_target = Column(Boolean, default=True)       # 計算対象フラグ
    imported_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="mf_transactions")
