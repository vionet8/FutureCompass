"""
ライフプランシミュレーションエンジン

インフレ・教育費・ライフステージを正確に計算する。

インフレ適用方針:
  - 生活費:   インフレ率で毎年上昇
  - 教育費:   インフレ率で毎年上昇（今後の物価上昇を反映）
  - 収入:     実質賃金上昇率（income_real_growth_rate）で毎年上昇
              ※退職後は公的年金（部分的にインフレ連動）に切替
  - 投資:     名目リターン = 実質リターン + インフレ率 で設定済みと想定
  - FIRE閾値: その時点のインフレ調整済み支出 × 25 で判定
"""

from dataclasses import dataclass, field
from typing import Optional


# ──────────────────────────────────────────────
# 教育費テーブル（文科省「子供の学習費調査」ベース、万円/年）
# ──────────────────────────────────────────────
EDU_ANNUAL_COST = {
    "nursery":    {"public": 17,  "private": 30},   # 0-2歳 保育園
    "preschool":  {"public": 23,  "private": 53},   # 3-5歳 幼稚園/保育園
    "elementary": {"public": 32,  "private": 160},  # 6-11歳 小学校
    "junior":     {"public": 49,  "private": 140},  # 12-14歳 中学校
    "high":       {"public": 46,  "private": 97},   # 15-17歳 高校
    "university": {"public": 64,  "private": 112},  # 18-21歳 大学（自宅通学ベース）
}

# 大学入学時の一時費用（入学金等）万円
EDU_ENTRY_COST = {"university": {"public": 28, "private": 60}}

# 収入のピーク年齢係数（国税庁「民間給与実態統計調査」基準の簡易モデル）
# age → 年齢別の給与水準カーブ。40-55歳がピーク。
# 実際の適用時は「現在年齢の係数」で正規化する（ユーザー入力＝現在の年収のため）。
INCOME_PEAK_CURVE = {
    (20, 29): 0.72,
    (30, 34): 0.88,
    (35, 39): 0.97,
    (40, 44): 1.05,
    (45, 49): 1.10,
    (50, 54): 1.08,
    (55, 59): 1.02,
    (60, 64): 0.78,  # 再雇用・役職定年
    (65, 69): 0.55,  # シニア継続就労（retirement_age>65の場合のみ到達）
    (70, 99): 0.40,
}

# 年金モデルの就労開始年齢（大卒想定）
CAREER_START_AGE = 22


def _income_peak_factor(age: int) -> float:
    for (lo, hi), factor in INCOME_PEAK_CURVE.items():
        if lo <= age <= hi:
            return factor
    return 1.0


def _edu_stage(child_age: int) -> Optional[str]:
    if 0 <= child_age <= 2:
        return "nursery"
    if 3 <= child_age <= 5:
        return "preschool"
    if 6 <= child_age <= 11:
        return "elementary"
    if 12 <= child_age <= 14:
        return "junior"
    if 15 <= child_age <= 17:
        return "high"
    if 18 <= child_age <= 21:
        return "university"
    return None


def _resolve_edu_type(education_type: str, stage: str) -> str:
    """学歴タイプ×ステージから public / private を決定する"""
    if education_type in ("mixed", "private_middle"):
        # 小学校まで公立、中学以降私立
        return "public" if stage in ("nursery", "preschool", "elementary") else "private"
    if education_type == "private_high":
        # 高校・大学のみ私立
        return "private" if stage in ("high", "university") else "public"
    return education_type if education_type in ("public", "private") else "public"


# ──────────────────────────────────────────────
# データクラス
# ──────────────────────────────────────────────

@dataclass
class LifePlanInput:
    age: int
    spouse_age: Optional[int]
    children_ages: list[int]

    annual_income: int          # 万円（現在の額面）
    spouse_income: int          # 万円
    annual_expense: int         # 万円（教育費除く生活費）
    monthly_investment: int     # 万円

    cash_assets: int = 0        # 万円（銀行預金・現金）→ 年率0.1%
    investment_assets: int = 0  # 万円（NISA・iDeCo・株等）→ investment_return_rate%
    total_assets: int = 0       # 万円（後方互換用。cash+investが0のとき使用）

    fire_target_age: Optional[int] = None
    retirement_age: int = 65
    spouse_retirement_age: int = 65
    life_expectancy: int = 90

    investment_return_rate: float = 5.0   # 名目 %
    inflation_rate: float = 2.0           # %
    income_real_growth_rate: float = 0.5  # 実質賃金上昇率 %（インフレ上乗せ分）

    education_type: str = "public"        # "public" | "private" | "mixed"

    # シナリオ変更
    spouse_quit_age: Optional[int] = None
    buy_house: bool = False
    house_price: int = 0
    house_age: int = 0
    move_to_rural: bool = False
    rural_expense_reduction: int = 0      # 万円/年（実質削減額、インフレ前）


@dataclass
class LifeStageEvent:
    """その年に発生するライフイベントのタグ"""
    label: str        # 表示名
    category: str     # "education" | "income" | "housing" | "family" | "retirement"
    impact_man: int   # 資産への影響（負=支出増、正=収入増）万円
    severity: str     # "high" | "medium" | "low"


def _grade(score: float, thresholds: list[tuple[float, str]]) -> str:
    """スコアに対してA/B/C/Dを返す。thresholdsは (下限値, グレード) の降順リスト。"""
    for threshold, grade in thresholds:
        if score >= threshold:
            return grade
    return thresholds[-1][1]


# 家計健全度グレード（貯蓄率ベース）
HOUSEHOLD_GRADES = [(0.20, "A"), (0.10, "B"), (0.0, "C"), (float("-inf"), "D")]

# 資産形成力グレード（年間資産増加率ベース）
WEALTH_GRADES = [(0.12, "A"), (0.05, "B"), (0.0, "C"), (float("-inf"), "D")]

# グレード→コメントマッピング
GRADE_COMMENT = {
    "household": {
        "A": "支出管理が優秀。毎月しっかり黒字です。",
        "B": "家計は概ね健全。もう少し貯蓄率を上げられると理想的。",
        "C": "収支はほぼ均衡。支出の見直し余地があります。",
        "D": "毎月赤字の状態。支出が収入を超えています。",
    },
    "wealth": {
        "A": "資産は力強く増加中。運用が効いています。",
        "B": "資産は着実に増えています。",
        "C": "資産はほぼ横ばい。インフレ負けに注意。",
        "D": "資産が減少中。市場要因か支出過多かを確認してください。",
    },
}


@dataclass
class YearlySnapshot:
    age: int
    year: int

    # 収入（名目）
    income_nominal: int
    spouse_income_nominal: int
    total_income_nominal: int

    # 収入の内訳（家計収支チャート用: 就労所得/金融所得/年金所得の積み上げに使う）
    labor_income_nominal: int      # 本人+配偶者の就労収入（退職後・年金受給後は0）
    pension_income_nominal: int    # 本人+配偶者の公的年金収入（就労中は0）
    financial_income_nominal: int  # その年に投資資産が生んだ運用収益（現金の利息含む）
    household_balance_incl_returns: int  # (就労+年金+金融所得) - 消費。運用益を収入とみなした場合の家計収支

    # 支出（インフレ調整済み実質）
    living_expense_real: int     # 生活費
    education_expense_real: int  # 教育費
    total_expense_real: int

    # 投資・純余剰
    investment: int
    net_cashflow: int            # 総収入 - 総支出（投資含む）
    assets: int
    cash: int                    # うち現金・預金（0.1%運用）
    invest: int                  # うち投資資産（r%運用）

    # FIRE
    fire_threshold: int          # その年のFIRE達成ライン（支出×25）
    fire_possible: bool

    # ── 3指標分離 ────────────────────────────────
    # ① 家計健全度（フロー）
    savings_rate: float          # 貯蓄率 = net_cashflow / total_income
    household_grade: str         # A/B/C/D

    # ② 資産形成力（ストック）
    asset_change: int            # 今年の資産増減額
    market_gain: int             # うち市場要因（前期資産 × 運用利回り）
    behavior_gain: int           # うち行動要因（自分でコントロールできた分）
    behavior_pct: float          # 行動要因の割合（0〜100%）
    wealth_grade: str            # A/B/C/D

    # ③ 将来達成率
    fire_progress_pct: float     # 現資産 / FIRE閾値 × 100

    # ライフステージ
    events: list[LifeStageEvent]
    life_phase: str              # "稼ぎ時" | "かかり時" | "安定期" | "老後"
    urgency_score: int           # -100〜+100（+が稼ぎ時、-がかかり時）


def _education_for_year(
    params: LifePlanInput, i: int, inf: float,
) -> tuple[float, list[LifeStageEvent]]:
    """
    シミュレーション i 年目の教育費（インフレ調整済み・万円）とイベントを返す。
    FIRE閾値の残存教育費計算とメインループの両方から呼ばれる唯一の計算源。
    """
    education_expense = 0.0
    events: list[LifeStageEvent] = []

    for child_age_now in params.children_ages:
        child_age = child_age_now + i

        # 子供独立イベント（大学卒業の翌年 = 22歳）
        if child_age == 22:
            last_type = _resolve_edu_type(params.education_type, "university")
            last_cost = EDU_ANNUAL_COST["university"][last_type] * (1 + inf) ** i
            events.append(LifeStageEvent(
                label="子供独立（教育費終了）",
                category="education",
                impact_man=int(last_cost),
                severity="medium",
            ))

        stage = _edu_stage(child_age)
        if stage is None:
            continue

        edu_type = _resolve_edu_type(params.education_type, stage)
        annual_edu = EDU_ANNUAL_COST[stage][edu_type] * (1 + inf) ** i
        education_expense += annual_edu

        # 大学入学時の一時費用
        if stage == "university" and child_age == 18:
            entry = EDU_ENTRY_COST["university"][edu_type] * (1 + inf) ** i
            education_expense += entry
            events.append(LifeStageEvent(
                label=f"大学入学（{edu_type}）",
                category="education",
                impact_man=-int(entry),
                severity="high",
            ))

        # ステージ変化のイベント記録
        prev_stage = _edu_stage(child_age - 1)
        if prev_stage != stage:
            labels = {
                "nursery": "保育園入園",
                "preschool": "幼稚園入園",
                "elementary": "小学校入学",
                "junior": "中学校入学",
                "high": "高校入学",
                "university": "大学入学",
            }
            events.append(LifeStageEvent(
                label=labels.get(stage, stage),
                category="education",
                impact_man=-int(annual_edu),
                severity="medium" if stage not in ("university", "high") else "high",
            ))

    return education_expense, events


# ──────────────────────────────────────────────
# メインシミュレーション
# ──────────────────────────────────────────────

def simulate(params: LifePlanInput) -> dict:
    r = params.investment_return_rate / 100
    inf = params.inflation_rate / 100
    income_growth = (params.income_real_growth_rate / 100) + inf  # 名目賃金上昇率

    current_year = 2026
    CASH_RATE = 0.001  # 現金・預金の年率（0.1%）

    # 現金と投資資産を分離。旧データ（total_assetsのみ）は 30/70 で按分
    if params.cash_assets or params.investment_assets:
        cash = float(params.cash_assets)
        invest = float(params.investment_assets)
    else:
        cash = float(params.total_assets) * 0.3
        invest = float(params.total_assets) * 0.7

    n_years = params.life_expectancy - params.age + 1

    # 残存教育費（FIRE閾値用）を先に計算しておく
    # edu_costs[i] = i年目の教育費（名目・万円）
    edu_costs = [_education_for_year(params, i, inf)[0] for i in range(n_years)]
    edu_remaining = [0.0] * (n_years + 1)
    for i in range(n_years - 1, -1, -1):
        edu_remaining[i] = edu_remaining[i + 1] + edu_costs[i]

    # 収入カーブは「現在年齢の係数」で正規化する
    # （ユーザー入力＝現在の実年収なので、初年度の係数は必ず1.0になる）
    base_peak = _income_peak_factor(params.age) or 1.0
    spouse_base_peak = (
        _income_peak_factor(params.spouse_age) or 1.0
    ) if params.spouse_age is not None else 1.0

    fire_age = None
    snapshots: list[YearlySnapshot] = []

    for i in range(n_years):
        age = params.age + i
        year = current_year + i
        events: list[LifeStageEvent] = []

        # ── 収入計算（名目） ──────────────────────
        # is_pension: この年のincome/spouse_incomeが「年金」由来かどうか
        # （就労所得/年金所得の積み上げチャート用に、後段でどちらへ計上するか判定する）
        self_is_pension = age >= params.retirement_age
        if not self_is_pension:
            # 年功序列カーブ（現在年齢で正規化）× 名目賃金成長
            base_income = params.annual_income * (1 + income_growth) ** i
            peak_factor = _income_peak_factor(age) / base_peak
            income = base_income * peak_factor
        else:
            # 公的年金（部分インフレ連動、簡易モデル）
            # 加入期間は22歳就労開始と仮定
            working_years = max(params.retirement_age - CAREER_START_AGE, 0)
            income = _pension_estimate(params.annual_income, working_years)
            income *= (1 + inf * 0.5) ** (age - params.retirement_age)  # マクロ経済スライド

        spouse_income = 0.0
        spouse_is_pension = False
        if params.spouse_age is not None:
            spouse_current_age = params.spouse_age + i
            if spouse_current_age >= 65:
                # 配偶者の公的年金（65歳から）
                spouse_is_pension = True
                quit = params.spouse_quit_age
                career_end = min(quit, params.spouse_retirement_age) if quit else params.spouse_retirement_age
                s_working_years = max(career_end - CAREER_START_AGE, 0)
                if params.spouse_income > 0 and s_working_years > 0:
                    spouse_income = _pension_estimate(params.spouse_income, s_working_years)
                else:
                    spouse_income = 78  # 基礎年金のみ（万円/年）
                spouse_income *= (1 + inf * 0.5) ** (spouse_current_age - 65)
            elif params.spouse_quit_age and spouse_current_age >= params.spouse_quit_age:
                spouse_income = 0
                if spouse_current_age == params.spouse_quit_age:
                    events.append(LifeStageEvent(
                        label="配偶者退職",
                        category="family",
                        impact_man=-int(params.spouse_income),
                        severity="high",
                    ))
            elif spouse_current_age >= params.spouse_retirement_age:
                spouse_income = 0
            else:
                spouse_peak = _income_peak_factor(spouse_current_age) / spouse_base_peak
                spouse_income = params.spouse_income * (1 + income_growth) ** i * spouse_peak

        total_income = income + spouse_income

        labor_income = (0 if self_is_pension else income) + (0 if spouse_is_pension else spouse_income)
        pension_income = (income if self_is_pension else 0) + (spouse_income if spouse_is_pension else 0)

        # ── 支出計算（インフレ調整済み） ─────────
        # 生活費（インフレ連動）
        living_base = params.annual_expense
        if params.move_to_rural and age >= 50:
            living_base -= params.rural_expense_reduction
        living_expense = living_base * (1 + inf) ** i

        # 教育費（インフレ調整 + ステージ別単価）
        education_expense, edu_events = _education_for_year(params, i, inf)
        events.extend(edu_events)

        total_expense = living_expense + education_expense

        # 住宅購入（まず現金、不足分は投資資産から。保有資産を超える購入は不可）
        if params.buy_house and age == params.house_age:
            available = cash + invest
            price = min(float(params.house_price), available)
            deduct_cash = min(cash, price)
            cash -= deduct_cash
            invest -= (price - deduct_cash)
            label = f"住宅購入（{params.house_price:,}万円）"
            if price < params.house_price:
                label += "※資産不足のため一部のみ"
            events.append(LifeStageEvent(
                label=label,
                category="housing",
                impact_man=-int(price),
                severity="high",
            ))

        # ── 投資・資産更新 ───────────────────────
        investment = params.monthly_investment * 12
        net_cashflow = int(total_income - total_expense - investment)

        # 退職後は取り崩し（投資積立なし）
        if age >= params.retirement_age:
            investment = 0
            net_cashflow = int(total_income - total_expense)

        cash_before = cash
        invest_before = invest
        assets_before = cash_before + invest_before  # 3指標計算用

        # ── 現金・投資の二本立て複利計算 ─────────────
        # 現金：0.1%利息 + 純余剰（給与天引き後の手残り）
        # 投資：investment_return_rate% + 積立額（月次投資の年額）
        # 退職後は積立停止。純余剰（年金−支出）はまず現金へ。
        cash = cash_before * (1 + CASH_RATE) + (net_cashflow if age < params.retirement_age else net_cashflow)
        invest = invest_before * (1 + r) + (investment * (1 + r / 2) if age < params.retirement_age else 0)

        # 現金がマイナスになった場合は投資資産から補填（強制取り崩し）
        if cash < 0:
            invest += cash
            cash = 0.0

        assets = cash + invest

        # ── 3指標計算 ────────────────────────────
        # ① 家計健全度（フロー）
        savings_rate = net_cashflow / total_income if total_income > 0 else 0.0
        household_grade = _grade(savings_rate, HOUSEHOLD_GRADES)

        # ② 資産形成力（ストック）を市場要因と行動要因に分解
        #   市場要因 = 運用リターン分（前期資産×利回り + 年内積立の運用益）
        #   行動要因 = 自分の行動（収支黒字＋積立）でもたらした増分
        #   ※ market_gain + behavior_gain = asset_change が成立する（丸め誤差除く）
        market_gain = int(cash_before * CASH_RATE + invest_before * r + investment * r / 2)
        behavior_gain = int(net_cashflow + investment)
        # 家計収支チャート用: 運用益をその年の「収入」とみなした場合の家計収支
        # （実際の資産計算では運用益は取り崩さず複利で残すが、このチャートは
        #   「投資が生む収入」を可視化する目的の別レンズとして市場要因分をそのまま使う）
        financial_income = market_gain
        household_balance_incl_returns = int(total_income + financial_income - total_expense)
        asset_change = int(assets - assets_before)
        total_controllable = abs(market_gain) + abs(behavior_gain)
        behavior_pct = (
            abs(behavior_gain) / total_controllable * 100
            if total_controllable > 0 else 0.0
        )
        asset_growth_rate = asset_change / assets_before if assets_before > 0 else 0.0
        wealth_grade = _grade(asset_growth_rate, WEALTH_GRADES)

        # ③ 将来達成率
        # FIRE閾値 = 恒久支出（生活費）×25 + 残存教育費の総額
        # 教育費は期間限定の支出なので4%ルールの対象にせず、残額を上乗せする
        fire_threshold = living_expense * 25 + edu_remaining[i]
        fire_progress_pct = min(100.0, assets / fire_threshold * 100) if fire_threshold > 0 else 0.0

        # ── FIRE判定 ─────────────────────────────
        is_fire = assets >= fire_threshold and age < params.retirement_age
        if is_fire and fire_age is None:
            fire_age = age

        # ── ライフフェーズ判定 ────────────────────
        phase, urgency = _classify_phase(
            age=age,
            params=params,
            net_cashflow=net_cashflow,
            education_expense=education_expense,
            living_expense=living_expense,
            total_income=total_income,
            i=i,
        )

        snapshots.append(YearlySnapshot(
            age=age,
            year=year,
            income_nominal=int(income),
            spouse_income_nominal=int(spouse_income),
            total_income_nominal=int(total_income),
            labor_income_nominal=int(labor_income),
            pension_income_nominal=int(pension_income),
            financial_income_nominal=int(financial_income),
            household_balance_incl_returns=household_balance_incl_returns,
            living_expense_real=int(living_expense),
            education_expense_real=int(education_expense),
            total_expense_real=int(total_expense),
            investment=int(investment),
            net_cashflow=net_cashflow,
            assets=int(assets),
            cash=int(cash),
            invest=int(invest),
            fire_threshold=int(fire_threshold),
            fire_possible=is_fire,
            # 3指標
            savings_rate=round(savings_rate, 4),
            household_grade=household_grade,
            asset_change=asset_change,
            market_gain=market_gain,
            behavior_gain=behavior_gain,
            behavior_pct=round(behavior_pct, 1),
            wealth_grade=wealth_grade,
            fire_progress_pct=round(fire_progress_pct, 1),
            # ライフステージ
            events=events,
            life_phase=phase,
            urgency_score=urgency,
        ))

    # ── 集計 ──────────────────────────────────
    retirement_snap = next((s for s in snapshots if s.age == params.retirement_age), snapshots[-1])
    retirement_assets = retirement_snap.assets

    initial_assets = (params.cash_assets + params.investment_assets) or params.total_assets
    benchmark = _benchmark_growth(
        initial_assets, params.monthly_investment,
        params.retirement_age - params.age, rate=0.06,  # 全世界株式インデックスの長期期待リターン想定
    )
    benchmark_at_retirement = benchmark[-1] if benchmark else 0

    # 稼ぎ時・かかり時サマリー
    peak_expense_years = sorted(
        [s for s in snapshots if s.urgency_score < -30],
        key=lambda s: s.urgency_score,
    )[:5]
    peak_earn_years = sorted(
        [s for s in snapshots if s.urgency_score > 30 and s.age < params.retirement_age],
        key=lambda s: -s.urgency_score,
    )[:5]

    # 教育費ピーク年
    edu_peak = max(snapshots, key=lambda s: s.education_expense_real, default=None)

    # 全イベント一覧
    all_events = [
        {"age": s.age, "year": s.year, "events": [vars(e) for e in s.events]}
        for s in snapshots if s.events
    ]

    return {
        "snapshots": [_snap_to_dict(s) for s in snapshots],
        "fire_age": fire_age,
        "fire_possible": fire_age is not None and (
            params.fire_target_age is None or fire_age <= params.fire_target_age
        ),
        "retirement_assets_man": int(retirement_assets),
        "monthly_pension_estimate_man": int(
            retirement_assets / ((params.life_expectancy - params.retirement_age) * 12)
        ) if retirement_assets > 0 else 0,
        "benchmark_at_retirement_man": int(benchmark_at_retirement),
        "vs_benchmark_diff_man": int(retirement_assets - benchmark_at_retirement),
        "total_education_cost_man": int(
            sum(s.education_expense_real for s in snapshots)
        ),
        "shortfall_man": max(0, int(snapshots[-1].fire_threshold - snapshots[-1].assets)),
        # ライフステージ分析
        "life_stages": {
            "peak_expense_ages": [s.age for s in peak_expense_years],
            "peak_earn_ages": [s.age for s in peak_earn_years],
            "education_peak_age": edu_peak.age if edu_peak else None,
            "education_peak_cost_man": edu_peak.education_expense_real if edu_peak else 0,
        },
        "all_events": all_events,
        "phase_timeline": [
            {"age": s.age, "phase": s.life_phase, "urgency": s.urgency_score}
            for s in snapshots if s.age < params.retirement_age
        ],
    }


def _classify_phase(
    age: int,
    params: LifePlanInput,
    net_cashflow: int,
    education_expense: float,
    living_expense: float,
    total_income: float,
    i: int,
) -> tuple[str, int]:
    """
    ライフフェーズと緊急度スコアを返す。
    urgency_score: +100 = 最高の稼ぎ時、-100 = 最大のかかり時
    """
    if age >= params.retirement_age:
        return "老後", 0

    # 基礎スコア：貯蓄率
    if total_income > 0:
        saving_rate = net_cashflow / total_income
    else:
        saving_rate = 0

    # 教育費負担率
    edu_ratio = education_expense / total_income if total_income > 0 else 0

    # 収入ピーク係数（40-55歳が高い）
    income_peak = _income_peak_factor(age)

    # スコア計算
    urgency = int(saving_rate * 60)            # 貯蓄率が高い＝稼ぎ時
    urgency -= int(edu_ratio * 80)             # 教育費が重い＝かかり時
    urgency += int((income_peak - 0.9) * 40)  # ピーク収入期にボーナス

    urgency = max(-100, min(100, urgency))

    # 子供が多い教育ピーク期
    children_in_edu = sum(
        1 for ca in params.children_ages
        if 6 <= (ca + i) <= 22
    )
    if children_in_edu >= 2:
        urgency -= 20

    # フェーズ名
    if age >= params.retirement_age - 5:
        phase = "老後準備期"
    elif urgency >= 30:
        phase = "稼ぎ時"
    elif urgency <= -30:
        phase = "かかり時"
    else:
        phase = "安定期"

    return phase, urgency


def _pension_estimate(annual_income: int, working_years: int) -> int:
    """
    厚生年金の簡易推計（年額、万円）。
    working_years は加入期間（22歳就労開始と仮定した通算年数）。
    報酬比例部分 = 平均標準報酬月額 × 0.5481% × 加入月数 の近似。
    """
    monthly_salary = annual_income / 12
    annual_pension = working_years * monthly_salary * 0.005481 * 12
    # 基礎年金（国民年金）加算: 約78万円/年
    annual_pension += 78
    return int(annual_pension)


def _benchmark_growth(initial: int, monthly_invest: int, years: int, rate: float) -> list[float]:
    result = []
    assets = float(initial)
    for _ in range(years):
        assets = assets * (1 + rate) + monthly_invest * 12
        result.append(assets)
    return result


def _snap_to_dict(s: YearlySnapshot) -> dict:
    return {
        "age": s.age,
        "year": s.year,
        # 収入
        "income_nominal": s.income_nominal,
        "spouse_income_nominal": s.spouse_income_nominal,
        "total_income_nominal": s.total_income_nominal,
        "labor_income_nominal": s.labor_income_nominal,
        "pension_income_nominal": s.pension_income_nominal,
        "financial_income_nominal": s.financial_income_nominal,
        "household_balance_incl_returns": s.household_balance_incl_returns,
        # 支出
        "living_expense_real": s.living_expense_real,
        "education_expense_real": s.education_expense_real,
        "total_expense_real": s.total_expense_real,
        # 収支
        "investment": s.investment,
        "net_cashflow": s.net_cashflow,
        "assets": s.assets,
        "cash": s.cash,
        "invest": s.invest,
        # FIRE
        "fire_threshold": s.fire_threshold,
        "fire_possible": s.fire_possible,
        "fire_progress_pct": s.fire_progress_pct,
        # ① 家計健全度（フロー）
        "savings_rate": s.savings_rate,
        "household_grade": s.household_grade,
        # ② 資産形成力（ストック）
        "asset_change": s.asset_change,
        "market_gain": s.market_gain,
        "behavior_gain": s.behavior_gain,
        "behavior_pct": s.behavior_pct,
        "wealth_grade": s.wealth_grade,
        # ライフステージ
        "life_phase": s.life_phase,
        "urgency_score": s.urgency_score,
        "events": [vars(e) for e in s.events],
    }


def compare_scenarios(base: LifePlanInput, variants: list[tuple[str, dict]]) -> list[dict]:
    results = []
    for name, overrides in variants:
        import dataclasses
        modified = dataclasses.replace(base, **overrides)
        sim = simulate(modified)
        results.append({
            "scenario": name,
            "fire_age": sim["fire_age"],
            "retirement_assets_man": sim["retirement_assets_man"],
            "shortfall_man": sim["shortfall_man"],
            "total_education_cost_man": sim["total_education_cost_man"],
            "vs_benchmark_diff_man": sim["vs_benchmark_diff_man"],
        })
    return results
