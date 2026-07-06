from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
import json

from ..core.database import get_db
from ..core.security import decrypt_value
from ..models.profile import UserProfile, Simulation
from ..models.user import User
from ..services.simulation import LifePlanInput, simulate, compare_scenarios
from ..services.ai_report import generate_fp_report, generate_scenario_comparison
from ..services.csv_parser import detect_and_parse, CSVParseError
from ..services.calc_explain import generate_explanation
from .auth import get_current_user

router = APIRouter(prefix="/simulate", tags=["simulation"])


class ScenarioOverride(BaseModel):
    name: str
    spouse_quit_age: Optional[int] = None
    buy_house: bool = False
    house_price: int = 0
    house_age: int = 0
    move_to_rural: bool = False
    rural_expense_reduction: int = 0
    # 経済前提の上書き（楽観/悲観シナリオ用）
    investment_return_rate: Optional[float] = None
    inflation_rate: Optional[float] = None
    income_real_growth_rate: Optional[float] = None
    education_type: Optional[str] = None


def _profile_to_input(profile: UserProfile, override: dict = {}) -> LifePlanInput:
    def dec(v):
        return int(decrypt_value(v)) if v else 0

    base = dict(
        age=profile.age,
        spouse_age=profile.spouse_age,
        children_ages=profile.children_ages or [],
        annual_income=dec(profile.annual_income_encrypted),
        spouse_income=dec(profile.spouse_income_encrypted),
        annual_expense=dec(profile.annual_expense_encrypted),
        cash_assets=dec(profile.cash_assets_encrypted),
        investment_assets=dec(profile.investment_assets_encrypted),
        total_assets=dec(profile.total_assets_encrypted),  # フォールバック用
        monthly_investment=dec(profile.monthly_investment_encrypted),
        fire_target_age=profile.fire_target_age,
        retirement_age=profile.retirement_age,
        life_expectancy=profile.life_expectancy,
        investment_return_rate=profile.investment_return_rate,
        inflation_rate=profile.inflation_rate,
        income_real_growth_rate=0.5,
        education_type=profile.education_type or "public",
    )
    base.update(override)
    return LifePlanInput(**base)


def _anonymize(profile: UserProfile) -> dict:
    """AIへ送信する匿名化プロフィール"""
    def dec(v):
        return int(decrypt_value(v)) if v else 0
    return {
        "年齢": profile.age,
        "配偶者年齢": profile.spouse_age,
        "子供人数": len(profile.children_ages or []),
        "世帯年収_万円": dec(profile.annual_income_encrypted) + dec(profile.spouse_income_encrypted),
        "年間支出_万円": dec(profile.annual_expense_encrypted),
        "総資産_万円": dec(profile.total_assets_encrypted),
        "月間投資額_万円": dec(profile.monthly_investment_encrypted),
        "FIRE目標年齢": profile.fire_target_age,
        "退職予定年齢": profile.retirement_age,
    }


@router.post("/run")
def run_simulation(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    profile = db.query(UserProfile).filter(UserProfile.user_id == current_user.id).first()
    if not profile or not profile.age:
        raise HTTPException(status_code=400, detail="先にプロフィールを設定してください")

    params = _profile_to_input(profile)
    result = simulate(params)

    # 計算根拠（AIなし・数式ベース）
    result["retirement_age_used"] = params.retirement_age
    result["life_expectancy"] = params.life_expectancy
    explanation = generate_explanation(result, result["snapshots"])

    # AI report（匿名化データのみ送信）
    anon = _anonymize(profile)
    ai_report = generate_fp_report(anon, result)

    # 保存
    sim = Simulation(
        user_id=current_user.id,
        scenario_name="メインシナリオ",
        result_data=result,
        ai_report=ai_report,
    )
    db.add(sim)
    db.commit()

    return {
        "simulation_id": sim.id,
        "result": result,
        "explanation": explanation,
        "ai_report": ai_report,
        "disclaimer": "本レポートは情報提供を目的としており、投資助言ではありません。",
    }


@router.post("/compare")
def compare(
    scenarios: list[ScenarioOverride],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    profile = db.query(UserProfile).filter(UserProfile.user_id == current_user.id).first()
    if not profile:
        raise HTTPException(status_code=400, detail="プロフィール未設定")

    base = _profile_to_input(profile)
    variants = [
        (s.name, {k: v for k, v in s.dict().items() if k != "name" and v is not None})
        for s in scenarios
    ]
    comparison = compare_scenarios(base, variants)

    anon = _anonymize(profile)
    ai_comparison = generate_scenario_comparison(anon, comparison)

    return {
        "comparison": comparison,
        "ai_analysis": ai_comparison,
        "disclaimer": "本レポートは情報提供を目的としており、投資助言ではありません。",
    }


@router.post("/upload-csv")
async def upload_csv(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="CSVファイルのみ対応しています")

    content = await file.read()
    try:
        parsed = detect_and_parse(file.filename, content)
    except CSVParseError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return {"parsed": parsed, "message": "CSVを読み込みました。プロフィール更新にご利用ください。"}


@router.get("/history")
def get_history(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sims = (
        db.query(Simulation)
        .filter(Simulation.user_id == current_user.id)
        .order_by(Simulation.created_at.desc())
        .limit(12)
        .all()
    )
    return [
        {
            "id": s.id,
            "created_at": s.created_at.isoformat(),
            "scenario_name": s.scenario_name,
            "fire_age": s.result_data.get("fire_age"),
            "retirement_assets_man": s.result_data.get("retirement_assets_man"),
        }
        for s in sims
    ]
