"""
FIRE到達年数計算のテスト（純粋関数、DB不要）
"""
from backend.services.fire_projection import years_to_fire, build_fire_scenarios


class TestYearsToFire:
    def test_already_reached_returns_zero(self):
        """投資資産が既に年間支出×25以上なら到達済み（0年）"""
        # 支出300万×25 = 7500万。資産8000万
        assert years_to_fire(80_000_000, 0, 3_000_000, 0.05) == 0.0

    def test_simple_growth_no_contribution(self):
        """積立なし・年率10%: 5000万→7500万は約4.3年（1.1^4.25≈1.5）"""
        y = years_to_fire(50_000_000, 0, 3_000_000, 0.10)
        assert y is not None
        assert 4.0 <= y <= 4.5

    def test_contribution_shortens_time(self):
        """毎月の積立があれば到達が早くなる"""
        without = years_to_fire(50_000_000, 0, 3_000_000, 0.10)
        with_inv = years_to_fire(50_000_000, 200_000, 3_000_000, 0.10)
        assert with_inv < without

    def test_zero_return_contribution_only(self):
        """リターン0%でも積立だけで到達できる: 不足2500万÷月20万=125ヶ月≈10.4年"""
        y = years_to_fire(50_000_000, 200_000, 3_000_000, 0.0)
        assert y is not None
        assert 10.0 <= y <= 10.9

    def test_unreachable_returns_none(self):
        """リターン0・積立なしでは永遠に到達しない → None"""
        assert years_to_fire(50_000_000, 0, 3_000_000, 0.0) is None

    def test_negative_return_unreachable(self):
        """大幅マイナスリターンでは減り続けて到達しない → None"""
        assert years_to_fire(50_000_000, 100_000, 3_000_000, -0.20) is None

    def test_zero_expense_returns_none(self):
        """年間支出が未設定（0）なら目標額が定まらない → None"""
        assert years_to_fire(50_000_000, 100_000, 0, 0.10) is None

    def test_total_loss_rate_returns_none(self):
        """年率-100%以下は資産消滅 → None（ZeroDivision等の例外を出さない）"""
        assert years_to_fire(50_000_000, 100_000, 3_000_000, -1.0) is None


class TestBuildFireScenarios:
    def test_three_scenarios_with_decreasing_returns(self):
        scenarios = build_fire_scenarios(
            current_investment_yen=50_000_000,
            monthly_investment_yen=200_000,
            annual_expense_yen=3_000_000,
            actual_annual_return=0.20,
            current_age=40,
        )
        assert len(scenarios) == 3
        assert scenarios[0].annual_return == 0.20
        assert abs(scenarios[1].annual_return - 0.15) < 1e-9
        assert abs(scenarios[2].annual_return - 0.10) < 1e-9
        # リターンが低いほど到達は遅い（すべて到達可能な前提の入力）
        assert scenarios[0].years_to_fire <= scenarios[1].years_to_fire <= scenarios[2].years_to_fire

    def test_fire_age_computed_from_current_age(self):
        scenarios = build_fire_scenarios(
            current_investment_yen=50_000_000,
            monthly_investment_yen=200_000,
            annual_expense_yen=3_000_000,
            actual_annual_return=0.20,
            current_age=40,
        )
        s = scenarios[0]
        assert s.fire_age == 40 + int(s.years_to_fire + 0.999)

    def test_no_age_gives_none_fire_age(self):
        scenarios = build_fire_scenarios(
            current_investment_yen=50_000_000,
            monthly_investment_yen=200_000,
            annual_expense_yen=3_000_000,
            actual_annual_return=0.20,
            current_age=None,
        )
        assert all(s.fire_age is None for s in scenarios)
