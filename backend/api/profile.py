from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from ..core.database import get_db
from ..core.security import encrypt_value, decrypt_value
from ..models.profile import UserProfile
from ..models.user import User
from .auth import get_current_user

router = APIRouter(prefix="/profile", tags=["profile"])


class ProfileInput(BaseModel):
    name: Optional[str] = None
    age: int
    spouse_age: Optional[int] = None
    children_ages: list[int] = []
    annual_income: int
    spouse_income: int = 0
    annual_expense: int
    cash_assets: int = 0        # 現金・預金
    investment_assets: int = 0  # 投資資産（NISA・iDeCo・株等）
    total_assets: int = 0       # 後方互換用（旧データからのフォールバック）
    monthly_investment: int
    fire_target_age: Optional[int] = None
    retirement_age: int = 65
    spouse_retirement_age: int = 65
    life_expectancy: int = 90
    investment_return_rate: float = 5.0
    inflation_rate: float = 2.0
    education_type: str = "public"


class ProfileResponse(BaseModel):
    age: Optional[int]
    spouse_age: Optional[int]
    children_ages: list[int]
    annual_income: Optional[int]
    spouse_income: Optional[int]
    annual_expense: Optional[int]
    cash_assets: Optional[int]
    investment_assets: Optional[int]
    total_assets: Optional[int]  # 後方互換：cash + investment の合計
    monthly_investment: Optional[int]
    fire_target_age: Optional[int]
    retirement_age: int
    life_expectancy: int
    investment_return_rate: float
    inflation_rate: float
    education_type: str


@router.put("/")
def upsert_profile(
    req: ProfileInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    profile = db.query(UserProfile).filter(UserProfile.user_id == current_user.id).first()
    if not profile:
        profile = UserProfile(user_id=current_user.id)
        db.add(profile)

    if req.name:
        profile.name_encrypted = encrypt_value(req.name)

    profile.age = req.age
    profile.spouse_age = req.spouse_age
    profile.children_ages = req.children_ages
    profile.annual_income_encrypted = encrypt_value(str(req.annual_income))
    profile.spouse_income_encrypted = encrypt_value(str(req.spouse_income))
    profile.annual_expense_encrypted = encrypt_value(str(req.annual_expense))
    profile.cash_assets_encrypted = encrypt_value(str(req.cash_assets))
    profile.investment_assets_encrypted = encrypt_value(str(req.investment_assets))
    # total_assets は cash + invest の合計として更新（後方互換）
    profile.total_assets_encrypted = encrypt_value(str(req.cash_assets + req.investment_assets))
    profile.monthly_investment_encrypted = encrypt_value(str(req.monthly_investment))
    profile.fire_target_age = req.fire_target_age
    profile.retirement_age = req.retirement_age
    profile.life_expectancy = req.life_expectancy
    profile.investment_return_rate = req.investment_return_rate
    profile.inflation_rate = req.inflation_rate
    profile.education_type = req.education_type

    db.commit()
    return {"message": "プロフィールを保存しました"}


@router.get("/", response_model=ProfileResponse)
def get_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    profile = db.query(UserProfile).filter(UserProfile.user_id == current_user.id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="プロフィールが未設定です")

    def dec_int(v):
        return int(decrypt_value(v)) if v else None

    cash = dec_int(profile.cash_assets_encrypted)
    invest = dec_int(profile.investment_assets_encrypted)
    total = dec_int(profile.total_assets_encrypted)

    return ProfileResponse(
        age=profile.age,
        spouse_age=profile.spouse_age,
        children_ages=profile.children_ages or [],
        annual_income=dec_int(profile.annual_income_encrypted),
        spouse_income=dec_int(profile.spouse_income_encrypted),
        annual_expense=dec_int(profile.annual_expense_encrypted),
        cash_assets=cash,
        investment_assets=invest,
        total_assets=total,
        monthly_investment=dec_int(profile.monthly_investment_encrypted),
        fire_target_age=profile.fire_target_age,
        retirement_age=profile.retirement_age,
        life_expectancy=profile.life_expectancy,
        investment_return_rate=profile.investment_return_rate,
        inflation_rate=profile.inflation_rate,
        education_type=profile.education_type or "public",
    )
