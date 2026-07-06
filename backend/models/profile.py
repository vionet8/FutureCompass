from sqlalchemy import Column, String, Integer, Float, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
from ..core.database import Base


class UserProfile(Base):
    """暗号化フィールドで個人情報を保護"""
    __tablename__ = "user_profiles"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), unique=True, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 暗号化して保存（core/security.pyのencrypt_value使用）
    name_encrypted = Column(String, nullable=True)

    # 数値（暗号化不要だが精度のため整数で管理）
    age = Column(Integer, nullable=True)
    spouse_age = Column(Integer, nullable=True)
    children_ages = Column(JSON, default=list)  # [5, 8] など

    # 年収・資産は暗号化
    annual_income_encrypted = Column(String, nullable=True)
    spouse_income_encrypted = Column(String, nullable=True)
    annual_expense_encrypted = Column(String, nullable=True)
    total_assets_encrypted = Column(String, nullable=True)       # 後方互換用（旧データ）
    cash_assets_encrypted = Column(String, nullable=True)        # 現金・預金
    investment_assets_encrypted = Column(String, nullable=True)  # 投資資産（NISA等）
    monthly_investment_encrypted = Column(String, nullable=True)

    # 目標
    fire_target_age = Column(Integer, nullable=True)
    retirement_age = Column(Integer, default=65)
    life_expectancy = Column(Integer, default=90)

    # 想定リターン（%）
    investment_return_rate = Column(Float, default=5.0)
    inflation_rate = Column(Float, default=2.0)

    # 子供の教育方針
    education_type = Column(String, default="public")  # public|private_middle|private_high|private

    user = relationship("User", back_populates="profile")


class Simulation(Base):
    __tablename__ = "simulations"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    scenario_name = Column(String, default="メインシナリオ")
    scenario_params = Column(JSON, default=dict)  # シナリオ変更パラメータ
    result_data = Column(JSON, default=dict)       # 計算結果
    ai_report = Column(String, nullable=True)      # AIレポート文章

    user = relationship("User", back_populates="simulations")
