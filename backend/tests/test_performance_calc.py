"""
Modified Dietz法の計算テスト（純粋関数、DB不要）
"""
from datetime import date

from backend.services.performance_calc import modified_dietz, CashFlow


class TestModifiedDietz:
    def test_no_cash_flow_simple_growth(self):
        """入出金なし：100万→110万、365日 → period_return == 0.10"""
        r = modified_dietz(
            v0=1_000_000, v1=1_100_000,
            t0=date(2025, 1, 1), t1=date(2026, 1, 1),
            flows=[],
        )
        assert r is not None
        assert abs(r["period_return"] - 0.10) < 1e-9
        assert abs(r["annualized_return"] - 0.10) < 1e-6
        assert r["days"] == 365

    def test_mid_period_deposit_hand_computed(self):
        """
        期間中央で50万入金：
        net_cf=500000, weighted_cf=250000(weight0.5), denominator=1,250,000
        numerator = 1,700,000-1,000,000-500,000=200,000, R=0.16
        """
        t0 = date(2025, 1, 1)
        t1 = date(2026, 1, 1)  # 365日後
        mid = date(2025, 7, 2)  # ちょうど中間付近
        d_total = (t1 - t0).days
        days_elapsed = (mid - t0).days
        expected_weight = (d_total - days_elapsed) / d_total

        r = modified_dietz(
            v0=1_000_000, v1=1_700_000, t0=t0, t1=t1,
            flows=[CashFlow(mid, 500_000)],
        )
        assert r is not None
        expected_denominator = 1_000_000 + expected_weight * 500_000
        expected_r = (1_700_000 - 1_000_000 - 500_000) / expected_denominator
        assert abs(r["period_return"] - expected_r) < 1e-9

    def test_deposit_at_period_start_has_near_full_weight(self):
        """
        期間開始直後の入金は「ほぼ全期間投資されていた」ので weight≈1。
        これが逆転しているとdenominatorが実質増えず計算結果が歪む（回帰テスト）。
        """
        t0 = date(2025, 1, 1)
        t1 = date(2026, 1, 1)
        # 期間開始翌日に100万円入金 → ほぼ丸々1年運用された扱いになるはず
        r = modified_dietz(
            v0=0, v1=1_100_000, t0=t0, t1=t1,
            flows=[CashFlow(date(2025, 1, 2), 1_000_000)],
        )
        assert r is not None
        # weightがほぼ1なので、単純な「100万→110万」に近いリターン（約10%）になるはず
        assert 0.08 < r["period_return"] < 0.12

    def test_deposit_at_period_end_has_near_zero_weight(self):
        """期間終了直前の入金はほぼ運用期間がないのでweight≈0、denominatorはv0のまま近似される"""
        t0 = date(2025, 1, 1)
        t1 = date(2026, 1, 1)
        r = modified_dietz(
            v0=1_000_000, v1=2_000_000, t0=t0, t1=t1,
            flows=[CashFlow(date(2025, 12, 31), 1_000_000)],
        )
        assert r is not None
        # 期末の入金はほぼ運用に寄与しないので、実質リターンは (2,000,000-1,000,000-1,000,000)/denominator≈0付近
        assert abs(r["period_return"]) < 0.05

    def test_withdrawal_negative_cash_flow(self):
        """出金（マイナスcash flow）：資産が減っても計算できる"""
        r = modified_dietz(
            v0=1_000_000, v1=400_000,
            t0=date(2025, 1, 1), t1=date(2026, 1, 1),
            flows=[CashFlow(date(2025, 7, 1), -500_000)],
        )
        assert r is not None
        # 出金後も残った400,000は運用益込みなので period_return は正になりうる

    def test_zero_day_period_returns_none(self):
        r = modified_dietz(
            v0=1_000_000, v1=1_000_000,
            t0=date(2025, 1, 1), t1=date(2025, 1, 1),
            flows=[],
        )
        assert r is None

    def test_negative_period_returns_none(self):
        r = modified_dietz(
            v0=1_000_000, v1=1_000_000,
            t0=date(2026, 1, 1), t1=date(2025, 1, 1),
            flows=[],
        )
        assert r is None

    def test_nonpositive_denominator_returns_none(self):
        """
        期間開始直後（weight≈1）に大きな出金があると分母が0以下になりうる
        → 例外を投げずNoneを返す
        """
        r = modified_dietz(
            v0=100_000, v1=10_000,
            t0=date(2025, 1, 1), t1=date(2025, 12, 31),
            flows=[CashFlow(date(2025, 1, 1), -200_000)],  # weight≈1 → denominator≈100000-200000<0
        )
        assert r is None

    def test_flow_outside_range_is_clamped(self):
        """期間外の日付を持つ入出金でも例外にならず妥当な範囲にクランプされる"""
        r = modified_dietz(
            v0=1_000_000, v1=1_100_000,
            t0=date(2025, 1, 1), t1=date(2026, 1, 1),
            flows=[CashFlow(date(2024, 1, 1), 100_000)],  # t0より前
        )
        assert r is not None
