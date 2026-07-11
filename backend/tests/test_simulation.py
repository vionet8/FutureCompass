"""
シミュレーションエンジンのテスト

数値の正確性・整合性を検証する。FPツールとして計算が正しいことが命。
"""
import pytest
from backend.services.simulation import (
    simulate,
    LifePlanInput,
    _resolve_edu_type,
    _income_peak_factor,
    _pension_estimate,
    _education_for_year,
    EDU_ANNUAL_COST,
    CAREER_START_AGE,
)


def base_input(**overrides) -> LifePlanInput:
    defaults = dict(
        age=35,
        spouse_age=33,
        children_ages=[3, 5],
        annual_income=600,
        spouse_income=200,
        annual_expense=400,
        monthly_investment=10,
        cash_assets=500,
        investment_assets=1000,
        education_type="public",
    )
    defaults.update(overrides)
    return LifePlanInput(**defaults)


# ──────────────────────────────────────────────
# 収入カーブ
# ──────────────────────────────────────────────

class TestIncomeCurve:
    def test_first_year_income_equals_input(self):
        """初年度の収入はユーザー入力値そのもの（正規化の検証）"""
        for age in (25, 32, 38, 45, 52, 58, 62):
            r = simulate(base_input(age=age, spouse_age=None, children_ages=[]))
            assert r["snapshots"][0]["income_nominal"] == 600, f"age={age}"

    def test_income_grows_toward_peak(self):
        """30歳スタートなら45-49歳のピークに向けて収入が増える"""
        r = simulate(base_input(age=30, spouse_age=None, children_ages=[]))
        snaps = {s["age"]: s["income_nominal"] for s in r["snapshots"]}
        assert snaps[45] > snaps[30]

    def test_income_positive_when_retirement_after_65(self):
        """retirement_age=70 でも65-69歳の収入がゼロにならない"""
        r = simulate(base_input(retirement_age=70, spouse_age=None, children_ages=[]))
        s67 = next(s for s in r["snapshots"] if s["age"] == 67)
        assert s67["income_nominal"] > 0

    def test_spouse_first_year_income_equals_input(self):
        r = simulate(base_input())
        assert r["snapshots"][0]["spouse_income_nominal"] == 200


# ──────────────────────────────────────────────
# 年金
# ──────────────────────────────────────────────

class TestPension:
    def test_pension_after_retirement(self):
        r = simulate(base_input(spouse_age=None, children_ages=[]))
        s66 = next(s for s in r["snapshots"] if s["age"] == 66)
        expected_base = _pension_estimate(600, 65 - CAREER_START_AGE)
        # マクロ経済スライドで微増するので下限のみ確認
        assert s66["income_nominal"] >= expected_base

    def test_spouse_pension_starts_at_65(self):
        """配偶者が60歳退職→65歳から年金収入が発生する（空白期間は無収入）"""
        r = simulate(base_input(spouse_retirement_age=60))
        s_at_spouse_62 = next(s for s in r["snapshots"] if s["age"] == 64)  # 配偶者62
        s_at_spouse_65 = next(s for s in r["snapshots"] if s["age"] == 67)  # 配偶者65
        assert s_at_spouse_62["spouse_income_nominal"] == 0
        assert s_at_spouse_65["spouse_income_nominal"] > 0

    def test_spouse_quit_still_gets_pension(self):
        """配偶者が退職(quit)しても65歳から年金は出る"""
        r = simulate(base_input(spouse_quit_age=40))
        s_at_spouse_65 = next(s for s in r["snapshots"] if s["age"] == 67)
        assert s_at_spouse_65["spouse_income_nominal"] > 0

    def test_nonworking_spouse_gets_basic_pension(self):
        """専業配偶者でも基礎年金78万は出る"""
        r = simulate(base_input(spouse_income=0))
        s_at_spouse_65 = next(s for s in r["snapshots"] if s["age"] == 67)
        assert s_at_spouse_65["spouse_income_nominal"] >= 78


# ──────────────────────────────────────────────
# 教育費
# ──────────────────────────────────────────────

class TestEducation:
    def test_resolve_edu_type(self):
        assert _resolve_edu_type("public", "high") == "public"
        assert _resolve_edu_type("private", "elementary") == "private"
        assert _resolve_edu_type("mixed", "elementary") == "public"
        assert _resolve_edu_type("mixed", "junior") == "private"
        assert _resolve_edu_type("private_high", "junior") == "public"
        assert _resolve_edu_type("private_high", "high") == "private"
        assert _resolve_edu_type("private_middle", "junior") == "private"
        # 不正な値はpublicにフォールバック
        assert _resolve_edu_type("unknown", "high") == "public"

    def test_first_year_education_cost_no_inflation(self):
        """初年度（i=0）はインフレ調整なしの単価そのまま"""
        p = base_input(children_ages=[7], education_type="public")
        cost, _ = _education_for_year(p, 0, 0.02)
        assert cost == EDU_ANNUAL_COST["elementary"]["public"]

    def test_independence_event_fires(self):
        """子供が22歳になる年に独立イベントが発生する（回帰テスト：以前は到達不能だった）"""
        r = simulate(base_input(children_ages=[10]))
        indep = [
            e for ev in r["all_events"] for e in ev["events"]
            if "独立" in e["label"]
        ]
        assert len(indep) == 1
        # 22歳になる年 = 12年後 = 47歳
        indep_year = next(
            ev for ev in r["all_events"]
            if any("独立" in e["label"] for e in ev["events"])
        )
        assert indep_year["age"] == 35 + 12

    def test_university_entry_cost_at_18(self):
        """18歳の年に大学入学の一時費用が乗る"""
        p = base_input(children_ages=[17], education_type="public")
        cost_at_18, events = _education_for_year(p, 1, 0.0)
        expected = EDU_ANNUAL_COST["university"]["public"] + 28  # 入学金
        assert cost_at_18 == expected
        assert any("大学入学" in e.label for e in events)

    def test_no_education_cost_after_22(self):
        p = base_input(children_ages=[23])
        cost, _ = _education_for_year(p, 0, 0.02)
        assert cost == 0

    def test_private_costs_more_than_public(self):
        r_pub = simulate(base_input(education_type="public"))
        r_pri = simulate(base_input(education_type="private"))
        assert r_pri["total_education_cost_man"] > r_pub["total_education_cost_man"]


# ──────────────────────────────────────────────
# 資産分解の整合性
# ──────────────────────────────────────────────

class TestAssetDecomposition:
    def test_market_plus_behavior_equals_asset_change(self):
        """market_gain + behavior_gain = asset_change（丸め±2以内）"""
        r = simulate(base_input())
        for s in r["snapshots"]:
            if s["cash"] == 0:
                continue  # 強制取り崩し年は分解対象外
            diff = s["asset_change"] - (s["market_gain"] + s["behavior_gain"])
            assert abs(diff) <= 2, f"age={s['age']} diff={diff}"

    def test_assets_equal_cash_plus_invest(self):
        r = simulate(base_input())
        for s in r["snapshots"]:
            assert abs(s["assets"] - (s["cash"] + s["invest"])) <= 1

    def test_legacy_total_assets_split(self):
        """旧データ（total_assetsのみ）は30/70で按分される"""
        p = base_input(cash_assets=0, investment_assets=0, total_assets=1000)
        r = simulate(p)
        s0 = r["snapshots"][0]
        # 初年度スナップショットは1年運用後なので比率はおおよそ
        assert s0["cash"] > 0
        assert s0["invest"] > s0["cash"]  # 70% > 30%


# ──────────────────────────────────────────────
# 収入の内訳（就労所得/年金所得/金融所得チャート用）
# ──────────────────────────────────────────────

class TestIncomeBreakdown:
    def test_working_years_have_labor_no_pension(self):
        """就労中は労働所得のみ、年金所得は0"""
        r = simulate(base_input(age=35, retirement_age=65, spouse_age=None))
        s0 = r["snapshots"][0]
        assert s0["labor_income_nominal"] > 0
        assert s0["pension_income_nominal"] == 0

    def test_retirement_years_have_pension_no_labor(self):
        """退職後は年金所得のみ、労働所得は0（本人・配偶者とも受給年齢後）"""
        p = base_input(age=68, spouse_age=68, retirement_age=65, spouse_retirement_age=65)
        r = simulate(p)
        s0 = r["snapshots"][0]
        assert s0["labor_income_nominal"] == 0
        assert s0["pension_income_nominal"] > 0

    def test_labor_plus_pension_equals_total_income(self):
        """就労所得+年金所得 = total_income_nominal が全年で成立する"""
        r = simulate(base_input())
        for s in r["snapshots"]:
            diff = s["total_income_nominal"] - (s["labor_income_nominal"] + s["pension_income_nominal"])
            assert abs(diff) <= 1, f"age={s['age']} diff={diff}"

    def test_financial_income_matches_market_gain(self):
        """financial_income_nominalはmarket_gainと一致する（同じ運用益を指す）"""
        r = simulate(base_input())
        for s in r["snapshots"]:
            assert s["financial_income_nominal"] == s["market_gain"]

    def test_household_balance_incl_returns_formula(self):
        """household_balance_incl_returns = 総収入+金融所得-総支出"""
        r = simulate(base_input())
        for s in r["snapshots"]:
            expected = s["total_income_nominal"] + s["financial_income_nominal"] - s["total_expense_real"]
            assert abs(s["household_balance_incl_returns"] - expected) <= 1

    def test_spouse_transitions_from_labor_to_pension_at_65(self):
        """配偶者が65歳になった年から年金所得に切り替わる"""
        p = base_input(age=63, spouse_age=63, retirement_age=70, spouse_retirement_age=70)
        r = simulate(p)
        before = next(s for s in r["snapshots"] if s["age"] == 64)  # 配偶者64歳
        after = next(s for s in r["snapshots"] if s["age"] == 65)   # 配偶者65歳
        assert before["pension_income_nominal"] == 0
        assert after["pension_income_nominal"] > 0


# ──────────────────────────────────────────────
# FIRE判定
# ──────────────────────────────────────────────

class TestFire:
    def test_fire_threshold_excludes_education_from_25x(self):
        """FIRE閾値 = 生活費×25 + 残存教育費（教育費×25にはしない）"""
        p = base_input(inflation_rate=0.0, children_ages=[6], education_type="public")
        r = simulate(p)
        s0 = r["snapshots"][0]
        # 生活費400×25 = 10000 + 残存教育費
        assert s0["fire_threshold"] >= 400 * 25
        # 教育費込み支出×25（旧ロジック）よりは小さい
        assert s0["fire_threshold"] < s0["total_expense_real"] * 25

    def test_rich_user_fires_immediately(self):
        p = base_input(
            cash_assets=10000, investment_assets=40000,
            children_ages=[], spouse_age=None,
        )
        r = simulate(p)
        assert r["fire_age"] == 35

    def test_poor_user_never_fires(self):
        p = base_input(
            cash_assets=0, investment_assets=0, total_assets=0,
            annual_income=300, annual_expense=290, monthly_investment=0,
        )
        r = simulate(p)
        assert r["fire_age"] is None


# ──────────────────────────────────────────────
# 住宅購入
# ──────────────────────────────────────────────

class TestHousePurchase:
    def test_house_purchase_reduces_assets(self):
        p_no = base_input(spouse_age=None, children_ages=[])
        p_buy = base_input(
            spouse_age=None, children_ages=[],
            buy_house=True, house_price=1000, house_age=40,
        )
        r_no = simulate(p_no)
        r_buy = simulate(p_buy)
        s_no = next(s for s in r_no["snapshots"] if s["age"] == 40)
        s_buy = next(s for s in r_buy["snapshots"] if s["age"] == 40)
        assert s_no["assets"] - s_buy["assets"] >= 950  # 運用差を考慮して概ね1000万差

    def test_house_purchase_never_negative_assets(self):
        """資産を超える住宅購入でも資産が負にならない（回帰テスト）"""
        p = base_input(
            cash_assets=100, investment_assets=100,
            buy_house=True, house_price=5000, house_age=36,
        )
        r = simulate(p)
        s36 = next(s for s in r["snapshots"] if s["age"] == 36)
        assert s36["invest"] >= 0
        assert s36["cash"] >= 0


# ──────────────────────────────────────────────
# ベンチマーク
# ──────────────────────────────────────────────

class TestBenchmark:
    def test_benchmark_uses_split_assets(self):
        """cash/invest分離データでもベンチマークが資産合計から計算される（回帰テスト）"""
        p = base_input(cash_assets=500, investment_assets=1000, total_assets=0)
        r = simulate(p)
        assert r["benchmark_at_retirement_man"] > 1500  # 初期資産1500が成長

    def test_benchmark_fallback_to_total_assets(self):
        p = base_input(cash_assets=0, investment_assets=0, total_assets=1500)
        r = simulate(p)
        assert r["benchmark_at_retirement_man"] > 1500


# ──────────────────────────────────────────────
# 全体の健全性
# ──────────────────────────────────────────────

class TestSanity:
    def test_snapshot_count(self):
        p = base_input(age=35, life_expectancy=90)
        r = simulate(p)
        assert len(r["snapshots"]) == 90 - 35 + 1

    def test_no_children_no_education_cost(self):
        r = simulate(base_input(children_ages=[]))
        assert r["total_education_cost_man"] == 0

    def test_scenario_comparison_runs(self):
        from backend.services.simulation import compare_scenarios
        base = base_input()
        results = compare_scenarios(base, [
            ("ベース", {}),
            ("楽観", {"investment_return_rate": 7.0}),
            ("悲観", {"investment_return_rate": 3.0}),
        ])
        assert len(results) == 3
        # 楽観 >= ベース >= 悲観
        assert results[1]["retirement_assets_man"] >= results[0]["retirement_assets_man"]
        assert results[0]["retirement_assets_man"] >= results[2]["retirement_assets_man"]
