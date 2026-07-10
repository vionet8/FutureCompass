"""
マネフォportfolioページのコピペテキスト解析テスト。
銘柄名・保有金融機関は実在の名称を使うが、保有数・金額はすべて架空の値。
（各セクションの行構造・ヘッダー折り返しパターンは実際のコピー結果を再現）
"""
import pytest
from backend.services.portfolio_parser import parse_portfolio_paste

SAMPLE_PASTE = """預金・現金
合計：1,000,000円
種類・名称	残高	保有金融機関	変更	削除
タンス預金	100,000円	タンス預金
電子マネー	-3,000円	電子マネー
米ドル 現金	10,000円	SBI証券
株式(現物)
合計：2,000,000円
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
1343	NFJ-REIT	10	1,000	1,100	11,000円	100円	1,000円	10.00%	SBI証券
8306	三菱UFJフィナンシャルG	100	2,500	3,000	300,000円	0円	50,000円	20.00%	楽天証券
VIG	バンガード 米国増配株式ETF	10	150.00	200.00	200,000円	1,000円	50,000円	33.33%	SBI証券
投資信託
合計：3,000,000円
銘柄名
	保有数
	平均取得単価
	基準価額
	評価額
	前日比
	評価損益
	評価損益率
	保有金融機関
	取得日
	変更	削除
eMAXIS Slim 全世界株式(オール・カントリー)	100,000	20,000	25,000	250,000円	0円	50,000円	25.00%	SBI証券
SBI・iシェアーズ・ゴールドファンド(為替ヘッジなし)	50,000	20,000	22,000	110,000円	1,000円	10,000円	10.00%	SBI証券
年金
合計：500,000円
名称	取得価額	現在価値	評価損益	評価損益率	取得日	変更	削除
楽天S&P500楽天DC	100,000円	120,000円	20,000円	20.00%
未納手数料		-100円
ポイント
合計：10,000円
名称	種類	ポイント・マイル数	換算レート	現在の価値	有効期限	保有金融機関	変更	削除
永久不滅ポイント	ポイント	200ポイント	5.00	1,000円		セゾンパールカード
"""


class TestParsePortfolioPaste:
    def test_parses_all_sections(self):
        holdings = parse_portfolio_paste(SAMPLE_PASTE)
        categories = {h.category for h in holdings}
        assert categories == {"現金", "株式", "投資信託", "年金", "ポイント"}

    def test_cash_section(self):
        holdings = parse_portfolio_paste(SAMPLE_PASTE)
        cash = [h for h in holdings if h.category == "現金"]
        assert len(cash) == 3
        tansu = next(h for h in cash if h.name == "タンス預金")
        assert tansu.market_value_yen == 100_000
        assert tansu.institution == "タンス預金"
        assert tansu.symbol_code is None

    def test_negative_cash_balance(self):
        """マイナス残高（未払い分）も正しく符号付きで解析される"""
        holdings = parse_portfolio_paste(SAMPLE_PASTE)
        emoney = next(h for h in holdings if h.name == "電子マネー")
        assert emoney.market_value_yen == -3_000

    def test_stock_section_with_multiline_header_skipped(self):
        """株式セクションは「1ラベル1行」のヘッダー折り返しが正しくスキップされる"""
        holdings = parse_portfolio_paste(SAMPLE_PASTE)
        stocks = [h for h in holdings if h.category == "株式"]
        assert len(stocks) == 3
        mitsubishi = next(h for h in stocks if h.symbol_code == "8306")
        assert mitsubishi.name == "三菱UFJフィナンシャルG"
        assert mitsubishi.market_value_yen == 300_000
        assert mitsubishi.institution == "楽天証券"

    def test_us_etf_ticker_as_symbol_code(self):
        holdings = parse_portfolio_paste(SAMPLE_PASTE)
        vig = next(h for h in holdings if h.symbol_code == "VIG")
        assert vig.market_value_yen == 200_000

    def test_fund_section(self):
        holdings = parse_portfolio_paste(SAMPLE_PASTE)
        funds = [h for h in holdings if h.category == "投資信託"]
        assert len(funds) == 2
        gold = next(h for h in funds if "ゴールド" in h.name)
        assert gold.market_value_yen == 110_000
        assert gold.symbol_code is None

    def test_pension_section_handles_missing_acquisition_cost(self):
        """「未納手数料」行は取得価額(1列目)が空でも現在価値(2列目)があれば正しく解析される"""
        holdings = parse_portfolio_paste(SAMPLE_PASTE)
        pension = [h for h in holdings if h.category == "年金"]
        names = [h.name for h in pension]
        assert "楽天S&P500楽天DC" in names
        assert "未納手数料" in names
        misshou = next(h for h in pension if h.name == "未納手数料")
        assert misshou.market_value_yen == -100

    def test_point_section(self):
        holdings = parse_portfolio_paste(SAMPLE_PASTE)
        points = [h for h in holdings if h.category == "ポイント"]
        assert len(points) == 1
        assert points[0].market_value_yen == 1_000

    def test_empty_text_returns_empty_list(self):
        assert parse_portfolio_paste("") == []

    def test_unrecognized_text_returns_empty_list(self):
        assert parse_portfolio_paste("hello\tworld\n123\t456\n") == []


class TestSecurityKey:
    def test_stock_uses_symbol_code(self):
        holdings = parse_portfolio_paste(SAMPLE_PASTE)
        mitsubishi = next(h for h in holdings if h.symbol_code == "8306")
        assert mitsubishi.security_key == "株式:8306"

    def test_fund_uses_category_and_name(self):
        holdings = parse_portfolio_paste(SAMPLE_PASTE)
        gold = next(h for h in holdings if "ゴールド" in h.name)
        assert gold.security_key == f"投資信託:{gold.name}"

    def test_same_stock_different_accounts_share_key(self):
        """同一銘柄を複数口座で保有していても証券コードが同じならsecurity_keyは同じになる"""
        marker = "8306\t三菱UFJフィナンシャルG\t100\t2,500\t3,000\t300,000円\t0円\t50,000円\t20.00%\t楽天証券"
        extra_line = "8306\t三菱UFJフィナンシャルG\t50\t2,400\t3,000\t150,000円\t0円\t30,000円\t25.00%\tSBI証券"
        assert marker in SAMPLE_PASTE, "テストの前提となる行がフィクスチャに見つからない"
        text = SAMPLE_PASTE.replace(marker, marker + "\n" + extra_line)
        holdings = parse_portfolio_paste(text)
        mub_holdings = [h for h in holdings if h.symbol_code == "8306"]
        assert len(mub_holdings) == 2
        assert mub_holdings[0].security_key == mub_holdings[1].security_key
