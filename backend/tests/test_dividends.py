"""株価・配当取得と配当サマリー計算のテスト（Yahoo API はモックし実通信しない）"""
import pytest
from datetime import date, datetime, timedelta
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.database import Base
from backend.models.user import User
from backend.models.portfolio import SecurityQuote, DividendEvent
from backend.models.performance import BenchmarkPrice
from backend.services.market_data import to_yahoo_symbol, refresh_market_data
from backend.services.dividend_summary import compute_dividend_summary
from backend.services.portfolio_import import import_portfolio_paste
from backend.services.benchmark import FX_SYMBOL

SAMPLE_PASTE = """株式(現物)
合計：1,000,000円
銘柄コード
	銘柄名
	保有数
	平均取得単価
	現在値
	評価額
	前日比
	評価損益
	評価損益率
	保有金融機関
	取得日
	変更	削除
9433	KDDI	200	2,322	2,828	565,600円	0円	101,200円	21.80%	楽天証券
VYM	バンガード 米国高配当株式ETF	25	117.05	161.13	654,268円	7,635円	178,987円	37.66%	SBI証券
"""


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


class TestToYahooSymbol:
    def test_jp_stock_code_gets_t_suffix(self):
        assert to_yahoo_symbol("9433") == "9433.T"
        assert to_yahoo_symbol("8306") == "8306.T"

    def test_jp_code_with_letter_suffix(self):
        """135A のような英字付き新形式コードも東証扱い"""
        assert to_yahoo_symbol("135A") == "135A.T"

    def test_us_ticker_unchanged(self):
        assert to_yahoo_symbol("VYM") == "VYM"
        assert to_yahoo_symbol("MSFT") == "MSFT"


def _recent(months_ago: int) -> date:
    """テスト実行日に依存しない「N月前」の日付"""
    today = date.today()
    m = today.month - months_ago
    y = today.year
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 15)


class TestRefreshMarketData:
    def test_fetches_and_caches(self, db):
        fake = {"price": 2800.0, "currency": "JPY", "dividends": [(_recent(2), 70.0)]}
        with patch("backend.services.market_data.fetch_quote_and_dividends", return_value=fake):
            r = refresh_market_data(db, ["9433"])
        assert r["refreshed"] == 1
        assert db.query(SecurityQuote).filter(SecurityQuote.yahoo_symbol == "9433.T").count() == 1
        assert db.query(DividendEvent).filter(DividendEvent.yahoo_symbol == "9433.T").count() == 1

    def test_skips_fresh_cache(self, db):
        db.add(SecurityQuote(yahoo_symbol="9433.T", latest_price=2800.0, currency="JPY",
                             fetched_at=datetime.utcnow()))
        db.commit()
        with patch("backend.services.market_data.fetch_quote_and_dividends") as mock_fetch:
            r = refresh_market_data(db, ["9433"])
            mock_fetch.assert_not_called()
        assert r["skipped_fresh"] == 1

    def test_refetches_stale_cache(self, db):
        db.add(SecurityQuote(yahoo_symbol="9433.T", latest_price=2000.0, currency="JPY",
                             fetched_at=datetime.utcnow() - timedelta(hours=25)))
        db.commit()
        fake = {"price": 2900.0, "currency": "JPY", "dividends": []}
        with patch("backend.services.market_data.fetch_quote_and_dividends", return_value=fake):
            r = refresh_market_data(db, ["9433"])
        assert r["refreshed"] == 1
        quote = db.query(SecurityQuote).filter(SecurityQuote.yahoo_symbol == "9433.T").first()
        assert quote.latest_price == 2900.0

    def test_per_symbol_failure_does_not_stop_others(self, db):
        def _fetch(sym):
            if sym == "9433.T":
                raise ValueError("network error")
            return {"price": 160.0, "currency": "USD", "dividends": []}
        with patch("backend.services.market_data.fetch_quote_and_dividends", side_effect=_fetch):
            r = refresh_market_data(db, ["9433", "VYM"])
        assert r["refreshed"] == 1
        assert len(r["errors"]) == 1
        assert r["errors"][0]["symbol"] == "9433"

    def test_dividend_dedup_on_refetch(self, db):
        fake = {"price": 2800.0, "currency": "JPY", "dividends": [(_recent(2), 70.0)]}
        with patch("backend.services.market_data.fetch_quote_and_dividends", return_value=fake):
            refresh_market_data(db, ["9433"])
            # キャッシュを失効させて再取得
            db.query(SecurityQuote).first().fetched_at = datetime.utcnow() - timedelta(hours=25)
            db.commit()
            refresh_market_data(db, ["9433"])
        assert db.query(DividendEvent).count() == 1


class TestComputeDividendSummary:
    def _seed_market_data(self, db):
        """KDDI(円建て・年2回)とVYM(ドル建て・年4回)の株価・配当をキャッシュに投入"""
        db.add(SecurityQuote(yahoo_symbol="9433.T", latest_price=2800.0, currency="JPY"))
        db.add(SecurityQuote(yahoo_symbol="VYM", latest_price=160.0, currency="USD"))
        # KDDI: 3月・9月権利落ち、各70円
        db.add(DividendEvent(yahoo_symbol="9433.T", ex_date=_recent(2), amount=70.0))
        db.add(DividendEvent(yahoo_symbol="9433.T", ex_date=_recent(8), amount=70.0))
        # VYM: 四半期配当 各$0.8
        for months_ago in (1, 4, 7, 10):
            db.add(DividendEvent(yahoo_symbol="VYM", ex_date=_recent(months_ago), amount=0.8))
        # ドル円レート
        db.add(BenchmarkPrice(symbol=FX_SYMBOL, price_date=date.today(), close_price=150.0))
        db.commit()

    def test_no_holdings_returns_none(self, db):
        assert compute_dividend_summary(db, "u1") is None

    def test_annual_income_jpy_stock(self, db):
        import_portfolio_paste(db, "u1", SAMPLE_PASTE)
        self._seed_market_data(db)
        r = compute_dividend_summary(db, "u1")
        kddi = next(h for h in r["holdings"] if h["symbol_code"] == "9433")
        # 200株 × (70+70)円 = 28,000円
        assert kddi["annual_income_yen"] == 28_000
        assert kddi["annual_dividend_per_share"] == 140.0
        assert kddi["yield_pct"] == 5.0  # 140/2800

    def test_usd_stock_converted_to_yen(self, db):
        import_portfolio_paste(db, "u1", SAMPLE_PASTE)
        self._seed_market_data(db)
        r = compute_dividend_summary(db, "u1")
        vym = next(h for h in r["holdings"] if h["symbol_code"] == "VYM")
        # 25株 × $3.2 × 150円 = 12,000円
        assert vym["annual_income_yen"] == 12_000
        assert vym["yield_pct"] == 2.0  # 3.2/160

    def test_monthly_calendar_sums_to_annual_total(self, db):
        import_portfolio_paste(db, "u1", SAMPLE_PASTE)
        self._seed_market_data(db)
        r = compute_dividend_summary(db, "u1")
        assert len(r["monthly"]) == 12
        assert sum(m["total_yen"] for m in r["monthly"]) == r["total_annual_income_yen"]
        assert r["total_annual_income_yen"] == 40_000  # 28,000 + 12,000

    def test_ex_months_listed(self, db):
        import_portfolio_paste(db, "u1", SAMPLE_PASTE)
        self._seed_market_data(db)
        r = compute_dividend_summary(db, "u1")
        vym = next(h for h in r["holdings"] if h["symbol_code"] == "VYM")
        assert len(vym["ex_months"]) == 4  # 四半期配当

    def test_missing_quantity_counted_not_crashed(self, db):
        """保有株数が取れていない銘柄（旧スナップショット等）は収入計算をスキップして件数報告"""
        import_portfolio_paste(db, "u1", SAMPLE_PASTE)
        from backend.models.portfolio import Holding
        for h in db.query(Holding).all():
            h.quantity = None
        db.commit()
        self._seed_market_data(db)
        r = compute_dividend_summary(db, "u1")
        assert r["missing_quantity_count"] == 2
        assert r["total_annual_income_yen"] == 0

    def test_usd_without_fx_rate_skips_income(self, db):
        """ドル円レート未取得ならUSD銘柄の収入はNoneになる（誤った円換算をしない）"""
        import_portfolio_paste(db, "u1", SAMPLE_PASTE)
        db.add(SecurityQuote(yahoo_symbol="VYM", latest_price=160.0, currency="USD"))
        db.add(DividendEvent(yahoo_symbol="VYM", ex_date=_recent(1), amount=0.8))
        db.commit()  # FXレートは入れない
        r = compute_dividend_summary(db, "u1")
        vym = next(h for h in r["holdings"] if h["symbol_code"] == "VYM")
        assert vym["annual_income_yen"] is None


class TestParserQuantity:
    def test_quantity_parsed_for_stocks(self, db):
        import_portfolio_paste(db, "u1", SAMPLE_PASTE)
        from backend.models.portfolio import Holding
        kddi = db.query(Holding).filter(Holding.symbol_code == "9433").first()
        vym = db.query(Holding).filter(Holding.symbol_code == "VYM").first()
        assert kddi.quantity == 200.0
        assert vym.quantity == 25.0
