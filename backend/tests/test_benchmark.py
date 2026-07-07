"""
ベンチマーク価格取得・キャッシュのテスト（実通信なし、monkeypatchでスタブ）
"""
import pytest
from datetime import date, datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.database import Base
from backend.models.performance import BenchmarkPrice
from backend.services import benchmark as bm


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    yield session
    session.close()


class TestGetPriceOnOrBefore:
    def test_exact_date_match(self, db):
        db.add(bm.BenchmarkPrice(symbol="vt.us", price_date=date(2026, 7, 3), close_price=100.0))
        db.commit()
        assert bm.get_price_on_or_before(db, "vt.us", date(2026, 7, 3)) == 100.0

    def test_weekend_falls_back_to_prior_business_day(self, db):
        """土日祝で価格がない日は直近の過去営業日の終値を返す"""
        db.add(bm.BenchmarkPrice(symbol="vt.us", price_date=date(2026, 7, 3), close_price=100.0))  # 金曜
        db.commit()
        # 2026/7/4,5は土日と仮定してデータなし
        assert bm.get_price_on_or_before(db, "vt.us", date(2026, 7, 5)) == 100.0

    def test_no_data_returns_none(self, db):
        assert bm.get_price_on_or_before(db, "vt.us", date(2026, 7, 3)) is None


class TestRefreshBenchmarkCache:
    def test_dedups_by_symbol_and_date(self, db, monkeypatch):
        rows = [
            {"date": date(2026, 7, 1), "close": 99.0},
            {"date": date(2026, 7, 2), "close": 100.0},
        ]
        monkeypatch.setattr(bm, "_fetch_stooq_csv", lambda symbol: rows)

        added1 = bm.refresh_benchmark_cache(db, "vt.us")
        added2 = bm.refresh_benchmark_cache(db, "vt.us")

        assert added1 == 2
        assert added2 == 0
        assert db.query(BenchmarkPrice).count() == 2


class TestEnsureCacheFresh:
    def test_success_returns_none(self, db, monkeypatch):
        monkeypatch.setattr(bm, "_fetch_stooq_csv", lambda symbol: [{"date": date(2026, 7, 1), "close": 99.0}])
        result = bm.ensure_cache_fresh(db, "vt.us")
        assert result is None
        assert db.query(BenchmarkPrice).count() == 1

    def test_fetch_failure_returns_error_string_not_raises(self, db, monkeypatch):
        def _raise(symbol):
            raise ValueError("network down")
        monkeypatch.setattr(bm, "_fetch_stooq_csv", _raise)

        result = bm.ensure_cache_fresh(db, "vt.us")
        assert result is not None
        assert "network down" in result
        # 例外が外に漏れていないことがこのテスト自体で証明される

    def test_skips_refetch_if_recently_fetched(self, db, monkeypatch):
        db.add(bm.BenchmarkPrice(
            symbol="vt.us", price_date=date(2026, 7, 1), close_price=99.0,
            fetched_at=datetime.utcnow(),
        ))
        db.commit()

        called = {"count": 0}
        def _track(symbol):
            called["count"] += 1
            return [{"date": date(2026, 7, 2), "close": 100.0}]
        monkeypatch.setattr(bm, "_fetch_stooq_csv", _track)

        result = bm.ensure_cache_fresh(db, "vt.us", max_age_hours=24)
        assert result is None
        assert called["count"] == 0  # 24時間以内なので再取得しない

    def test_refetches_if_stale(self, db, monkeypatch):
        db.add(bm.BenchmarkPrice(
            symbol="vt.us", price_date=date(2026, 7, 1), close_price=99.0,
            fetched_at=datetime.utcnow() - timedelta(hours=25),
        ))
        db.commit()

        called = {"count": 0}
        def _track(symbol):
            called["count"] += 1
            return [{"date": date(2026, 7, 2), "close": 100.0}]
        monkeypatch.setattr(bm, "_fetch_stooq_csv", _track)

        result = bm.ensure_cache_fresh(db, "vt.us", max_age_hours=24)
        assert result is None
        assert called["count"] == 1
