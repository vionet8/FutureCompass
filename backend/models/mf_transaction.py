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
    is_transfer = Column(Boolean, default=False)    # 振替フラグ（マネフォからのインポート値、そのまま保持）
    is_target = Column(Boolean, default=True)       # 計算対象フラグ
    imported_at = Column(DateTime, default=datetime.utcnow)

    # 手動修正（1件だけの個別上書き）。ルールベースの一括修正はTransactionRuleで別管理し、
    # ここでは元のインポート値を残したまま上書き値だけ持つ（再インポートしても消えない）
    category_major_override = Column(String, nullable=True)
    category_minor_override = Column(String, nullable=True)
    is_transfer_override = Column(Boolean, nullable=True)
    override_source = Column(String, nullable=True)  # "manual" | "ai_accepted"
    overridden_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="mf_transactions")


class TransactionRule(Base):
    """
    パターンルールによる一括修正（例:「保有金融機関にSBI証券を含む取引は振替扱い」）。
    既存取引・将来インポートされる取引の両方に、読み込み時点で動的に適用される
    （個別のMFTransaction.category_major等は書き換えない）。
    """
    __tablename__ = "transaction_rules"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    match_field = Column(String, nullable=False)   # "description" | "institution"
    match_value = Column(String, nullable=False)   # 大文字小文字を区別しない部分一致
    set_is_transfer = Column(Boolean, nullable=True)
    set_category_major = Column(String, nullable=True)
    set_category_minor = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
