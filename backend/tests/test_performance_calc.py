"""
Modified Dietz法の計算テスト（純粋関数、DB不要）
"""
import pytest
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.database import Base
from backend.models.user import User
from backend.models.performance import AssetSnapshot, CashFlowEvent, BenchmarkPrice
from backend.services.benchmark import FX_SYMBOL
from backend.services.performance_calc import (
    modified_dietz,
    compute_user_performance,
    compute_history_series,
    CashFlow,
)


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


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    session.add(User(id="u1", email="t@example.com", hashed_password="x"))
    session.commit()
    yield session
    session.close()


class TestComputeUserPerformanceUsesInvestmentOnly:
    def test_ignores_cash_only_growth(self, db):
        """
        総資産(現金+投資)は増えても投資資産(investment_assets_yen)が変わらなければ
        リターンはゼロ付近になる（貯蓄と運用益を混同しないための回帰テスト）。
        """
        db.add(AssetSnapshot(
            user_id="u1", snapshot_date=date(2025, 1, 1),
            total_assets_yen=1_000_000, cash_assets_yen=500_000, investment_assets_yen=500_000,
        ))
        db.add(AssetSnapshot(
            user_id="u1", snapshot_date=date(2026, 1, 1),
            # 総資産は倍増しているが、これは全額現金の貯蓄によるもの
            total_assets_yen=2_000_000, cash_assets_yen=1_500_000, investment_assets_yen=500_000,
        ))
        db.commit()

        r = compute_user_performance(db, "u1")
        assert r is not None
        assert abs(r["period_return"]) < 0.01  # 投資資産は無変化なのでリターンはほぼ0

    def test_reflects_investment_only_growth(self, db):
        """投資資産自体が増えていればリターンに反映される"""
        db.add(AssetSnapshot(
            user_id="u1", snapshot_date=date(2025, 1, 1),
            total_assets_yen=1_000_000, cash_assets_yen=500_000, investment_assets_yen=500_000,
        ))
        db.add(AssetSnapshot(
            user_id="u1", snapshot_date=date(2026, 1, 1),
            total_assets_yen=1_050_000, cash_assets_yen=500_000, investment_assets_yen=550_000,
        ))
        db.commit()

        r = compute_user_performance(db, "u1")
        assert r is not None
        assert abs(r["period_return"] - 0.10) < 0.01


class TestComputeHistorySeries:
    def _add_prices(self, db, symbol_prices, fx_rate=100.0):
        """VTドル価格とドル円レートを両方入れる（円建て換算に両方必要）"""
        for d, p in symbol_prices:
            db.add(BenchmarkPrice(symbol="VT", price_date=d, close_price=p))
            db.add(BenchmarkPrice(symbol=FX_SYMBOL, price_date=d, close_price=fx_rate))
        db.commit()

    def test_returns_none_with_insufficient_snapshots(self, db):
        db.add(AssetSnapshot(
            user_id="u1", snapshot_date=date(2025, 1, 1),
            total_assets_yen=1_000_000, cash_assets_yen=0, investment_assets_yen=1_000_000,
        ))
        db.commit()
        assert compute_history_series(db, "u1", "VT") is None

    def test_benchmark_tracks_price_growth(self, db):
        """
        VT価格が100→110（+10%）なら、架空VT評価額も開始残高の+10%になる。
        開始残高100万円: 1万円/unit(=100ドル×100円)で100units → 110万円
        """
        db.add(AssetSnapshot(
            user_id="u1", snapshot_date=date(2025, 1, 1),
            total_assets_yen=1_000_000, cash_assets_yen=0, investment_assets_yen=1_000_000,
        ))
        db.add(AssetSnapshot(
            user_id="u1", snapshot_date=date(2026, 1, 1),
            total_assets_yen=1_200_000, cash_assets_yen=0, investment_assets_yen=1_200_000,
        ))
        self._add_prices(db, [(date(2025, 1, 1), 100.0), (date(2026, 1, 1), 110.0)])

        s = compute_history_series(db, "u1", "VT")
        assert s is not None
        assert len(s["points"]) == 2
        assert s["points"][0]["user_yen"] == 1_000_000
        assert abs(s["points"][0]["benchmark_yen"] - 1_000_000) < 1
        assert s["points"][1]["user_yen"] == 1_200_000
        assert abs(s["points"][1]["benchmark_yen"] - 1_100_000) < 1

    def test_cash_flow_buys_units_at_flow_date_price(self, db):
        """途中入金はその日の価格でVTを架空購入する"""
        db.add(AssetSnapshot(
            user_id="u1", snapshot_date=date(2025, 1, 1),
            total_assets_yen=1_000_000, cash_assets_yen=0, investment_assets_yen=1_000_000,
        ))
        db.add(AssetSnapshot(
            user_id="u1", snapshot_date=date(2026, 1, 1),
            total_assets_yen=2_000_000, cash_assets_yen=0, investment_assets_yen=2_000_000,
        ))
        db.add(CashFlowEvent(
            user_id="u1", flow_date=date(2025, 7, 1), amount_yen=550_000,
            flow_type="deposit", source="manual",
        ))
        self._add_prices(db, [
            (date(2025, 1, 1), 100.0),   # 100万円→100units
            (date(2025, 7, 1), 110.0),   # 55万円→50units
            (date(2026, 1, 1), 120.0),   # 150units×1.2万円=180万円
        ])

        s = compute_history_series(db, "u1", "VT")
        assert abs(s["points"][1]["benchmark_yen"] - 1_800_000) < 1

    def test_missing_prices_degrade_to_user_series_only(self, db):
        """ベンチマーク価格が無くてもユーザー系列は返る（benchmark_yen=None）"""
        db.add(AssetSnapshot(
            user_id="u1", snapshot_date=date(2025, 1, 1),
            total_assets_yen=1_000_000, cash_assets_yen=0, investment_assets_yen=1_000_000,
        ))
        db.add(AssetSnapshot(
            user_id="u1", snapshot_date=date(2026, 1, 1),
            total_assets_yen=1_200_000, cash_assets_yen=0, investment_assets_yen=1_200_000,
        ))
        db.commit()

        s = compute_history_series(db, "u1", "VT")
        assert s is not None
        assert all(p["benchmark_yen"] is None for p in s["points"])
        assert [p["user_yen"] for p in s["points"]] == [1_000_000, 1_200_000]
