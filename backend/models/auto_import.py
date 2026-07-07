from sqlalchemy import Column, String, DateTime, Boolean, Integer, ForeignKey
from datetime import datetime
import uuid
from ..core.database import Base


class AutoImportConfig(Base):
    """ユーザーごとの自動取込設定（監視フォルダ）"""
    __tablename__ = "auto_import_configs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), unique=True, nullable=False, index=True)
    directory = Column(String, nullable=False)          # 監視するフォルダの絶対パス
    enabled = Column(Boolean, default=True)
    last_scanned_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ImportedFile(Base):
    """取込済みファイルの記録（同一ファイルの再取込を防ぐ）"""
    __tablename__ = "imported_files"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    file_name = Column(String, nullable=False)
    file_hash = Column(String, nullable=False, index=True)  # SHA-256
    imported_count = Column(Integer, default=0)
    skipped_count = Column(Integer, default=0)
    source = Column(String, default="auto")  # "auto" | "manual"
    imported_at = Column(DateTime, default=datetime.utcnow)
