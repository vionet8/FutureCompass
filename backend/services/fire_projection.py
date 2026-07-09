"""
実績リターンに基づくFIRE到達年数の予測。

「今の運用成績（実績年率）が続いたら何年でFIREできるか」を、
実績そのまま／-5pt／-10pt の3シナリオで計算する。
FIRE目標額は4%ルール（年間支出×25倍）を採用。

シミュレーション（simulation.py）が「想定リターン(プロフィール設定値)」で
将来を描くのに対し、こちらは「実際に出せている運用成績」を起点にする点が違う。
"""

from dataclasses import dataclass
from typing import Optional

# 4%ルール: 年間支出の25倍の資産があれば取り崩しで生活できるとする経験則
FIRE_MULTIPLIER = 25
MAX_YEARS = 100  # これを超えたら「到達不可」扱い


@dataclass
class FireScenario:
    label: str
    annual_return: float        # 小数（0.23 = 23%）
    years_to_fire: Optional[float]  # None = 100年以内に到達しない
    fire_age: Optional[int]     # 現在年齢が不明ならNone


def years_to_fire(
    current_investment_yen: float,
    monthly_investment_yen: float,
    annual_expense_yen: float,
    annual_return: float,
) -> Optional[float]:
    """
    月次複利で資産を積み上げ、FIRE目標額（年間支出×25）に到達するまでの年数を返す。
    100年以内に到達しなければNone。すでに到達済みなら0.0。
    """
    if annual_expense_yen <= 0:
        return None
    target = annual_expense_yen * FIRE_MULTIPLIER
    if current_investment_yen >= target:
        return 0.0

    # 年率→月率（幾何平均）。annual_return <= -1 は資産が消滅するため到達不可
    if annual_return <= -1:
        return None
    monthly_rate = (1 + annual_return) ** (1 / 12) - 1

    assets = current_investment_yen
    for month in range(1, MAX_YEARS * 12 + 1):
        assets = assets * (1 + monthly_rate) + monthly_investment_yen
        if assets >= target:
            return round(month / 12, 1)
    return None


def build_fire_scenarios(
    current_investment_yen: float,
    monthly_investment_yen: float,
    annual_expense_yen: float,
    actual_annual_return: float,
    current_age: Optional[int] = None,
) -> list[FireScenario]:
    """実績リターンそのまま／-5pt／-10ptの3シナリオでFIRE到達年数を計算する"""
    scenarios = []
    for label, r in [
        ("実績リターン継続", actual_annual_return),
        ("実績 -5pt", actual_annual_return - 0.05),
        ("実績 -10pt", actual_annual_return - 0.10),
    ]:
        y = years_to_fire(current_investment_yen, monthly_investment_yen, annual_expense_yen, r)
        fire_age = None
        if y is not None and current_age is not None:
            fire_age = current_age + int(y + 0.999)  # 端数年は切り上げ（その年齢中に到達）
        scenarios.append(FireScenario(label=label, annual_return=r, years_to_fire=y, fire_age=fire_age))
    return scenarios
