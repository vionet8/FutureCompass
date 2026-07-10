from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..models.user import User
from ..models.profile import UserProfile
from ..models.performance import AssetSnapshot
from ..services.wealth_percentile import (
    compute_wealth_percentile,
    compute_wealth_percentile_for_band,
    age_band,
    AGE_BAND_RAW_PCT,
)
from .auth import get_current_user

router = APIRouter(prefix="/wealth", tags=["wealth"])


@router.get("/percentile")
def get_wealth_percentile(
    age_band_override: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    年齢と最新の総資産額から、同世代内での資産パーセンタイルを推定する。
    出典: 家計の金融行動に関する世論調査（二人以上世帯）の年代別分布データによる近似。

    age_band_override: 年齢が世代境界付近（例: 39歳）の場合など、比較対象の年代を
      ユーザーが選び直せるようにするための任意パラメータ（例: "40代"）。
    """
    profile = db.query(UserProfile).filter(UserProfile.user_id == current_user.id).first()
    if profile is None or profile.age is None:
        return {
            "has_data": False,
            "message": "年齢が未設定です。プロフィールで年齢を設定してください。",
        }

    latest = (
        db.query(AssetSnapshot)
        .filter(AssetSnapshot.user_id == current_user.id)
        .order_by(AssetSnapshot.snapshot_date.desc())
        .first()
    )
    if latest is None:
        return {
            "has_data": False,
            "message": "資産推移データがありません。運用成績ページでマネーフォワードの資産推移CSVを取り込んでください。",
        }

    total_assets_man = latest.total_assets_yen / 10000

    if age_band_override and age_band_override in AGE_BAND_RAW_PCT:
        result = compute_wealth_percentile_for_band(age_band_override, total_assets_man)
    else:
        result = compute_wealth_percentile(profile.age, total_assets_man)

    if result is None:
        return {
            "has_data": False,
            "message": "この年齢に対応する統計データがありません（20歳未満）。",
        }

    return {
        "has_data": True,
        "age_band": result.age_band,
        "actual_age_band": age_band(profile.age),
        "available_age_bands": list(AGE_BAND_RAW_PCT.keys()),
        "total_assets_man": round(total_assets_man),
        "top_percent": result.top_percent,
        "percentile_from_bottom": result.percentile_from_bottom,
        "pyramid": result.pyramid,
        "user_threshold_man": result.user_threshold_man,
        "source": "家計の金融行動に関する世論調査（二人以上世帯、金融資産保有額）の年代別分布データに基づく近似値",
    }
