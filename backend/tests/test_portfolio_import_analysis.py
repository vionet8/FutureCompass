"""ポートフォリオ取込・内訳集計の統合テスト"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.database import Base
from backend.models.user import User
from backend.models.portfolio import PortfolioSnapshot, Holding, SecurityTag
from backend.services.portfolio_import import import_portfolio_paste, PortfolioParseError
from backend.services.portfolio_analysis import compute_breakdown, list_securities_with_tags
from backend.services.classification import BUILTIN_AXES

SAMPLE_PASTE = """預金・現金
合計：110,000円
種類・名称	残高	保有金融機関	変更	削除
タンス預金	100,000円	タンス預金
米ドル 現金	10,000円	SBI証券
株式(現物)
合計：500,000円
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
8306	三菱UFJフィナンシャルG	100	2,500	3,000	300,000円	0円	50,000円	20.00%	楽天証券
VIG	バンガード 米国増配株式ETF	10	150.00	200.00	200,000円	1,000円	50,000円	33.33%	SBI証券
投資信託
合計：100,000円
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
SBI・iシェアーズ・ゴールドファンド(為替ヘッジなし)	50,000	20,000	22,000	100,000円	1,000円	10,000円	10.00%	SBI証券
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


class TestImportPortfolioPaste:
    def test_creates_snapshot_and_holdings(self, db):
        result = import_portfolio_paste(db, "u1", SAMPLE_PASTE)
        assert result["status"] == "imported"
        assert result["holdings_count"] == 5
        assert db.query(PortfolioSnapshot).count() == 1
        assert db.query(Holding).count() == 5

    def test_total_value_matches_sum(self, db):
        result = import_portfolio_paste(db, "u1", SAMPLE_PASTE)
        expected = 100_000 + 10_000 + 300_000 + 200_000 + 100_000
        assert result["total_value_yen"] == expected

    def test_missing_sections_reported(self, db):
        """
        SAMPLE_PASTEには年金・ポイントセクションが元々含まれていない。
        コピー範囲の見落とし（例:先頭の現金セクションが選択範囲から漏れる）に
        気づけるよう、検出できなかったセクション名を返す回帰テスト。
        """
        result = import_portfolio_paste(db, "u1", SAMPLE_PASTE)
        assert set(result["missing_sections"]) == {"年金", "ポイント"}

    def test_no_missing_sections_when_all_present(self, db):
        full_paste = SAMPLE_PASTE + (
            "年金\n合計：50,000円\n名称\t取得価額\t現在価値\t評価損益\t評価損益率\t取得日\t変更\t削除\n"
            "テストDC\t40,000円\t50,000円\t10,000円\t25.00%\n"
            "ポイント\n合計：1,000円\n名称\t種類\tポイント・マイル数\t換算レート\t現在の価値\t有効期限\t保有金融機関\t変更\t削除\n"
            "テストポイント\tポイント\t1000ポイント\t1.00\t1,000円\t\tテストカード\n"
        )
        result = import_portfolio_paste(db, "u1", full_paste)
        assert result["missing_sections"] == []

    def test_creates_auto_tags(self, db):
        import_portfolio_paste(db, "u1", SAMPLE_PASTE)
        tags = db.query(SecurityTag).filter(SecurityTag.security_key == "投資信託:SBI・iシェアーズ・ゴールドファンド(為替ヘッジなし)").all()
        assert len(tags) == len(BUILTIN_AXES)
        assert all(t.is_auto == 1 for t in tags)

    def test_repeated_paste_creates_new_snapshot_without_duplicating_tags(self, db):
        """再貼り付けは新規スナップショットを作るが、既存タグは重複登録されない"""
        import_portfolio_paste(db, "u1", SAMPLE_PASTE)
        import_portfolio_paste(db, "u1", SAMPLE_PASTE)
        assert db.query(PortfolioSnapshot).count() == 2
        assert db.query(Holding).count() == 10
        tags = db.query(SecurityTag).filter(SecurityTag.security_key == "株式:8306").all()
        assert len(tags) == len(BUILTIN_AXES)  # 2回貼っても標準軸ぶんのタグのまま

    def test_empty_paste_raises(self, db):
        with pytest.raises(PortfolioParseError):
            import_portfolio_paste(db, "u1", "no valid data here")


class TestComputeBreakdown:
    def test_breakdown_by_currency(self, db):
        import_portfolio_paste(db, "u1", SAMPLE_PASTE)
        r = compute_breakdown(db, "u1", "currency")
        assert r is not None
        by_value = {g["value"]: g["amount_yen"] for g in r["groups"]}
        # JPY: タンス預金100,000 + 三菱UFJ300,000 + ゴールドファンド100,000
        assert by_value["JPY"] == 100_000 + 300_000 + 100_000
        # USD: 米ドル現金10,000 + VIG 200,000
        assert by_value["USD"] == 10_000 + 200_000

    def test_breakdown_by_asset_class_gold_is_separate(self, db):
        """ゴールドファンドは「金・コモディティ」として他の投信と別グループになる（ユーザー要望確認）"""
        import_portfolio_paste(db, "u1", SAMPLE_PASTE)
        r = compute_breakdown(db, "u1", "asset_class")
        values = {g["value"] for g in r["groups"]}
        assert "金・コモディティ" in values
        gold_group = next(g for g in r["groups"] if g["value"] == "金・コモディティ")
        assert gold_group["amount_yen"] == 100_000

    def test_breakdown_pct_sums_to_100(self, db):
        import_portfolio_paste(db, "u1", SAMPLE_PASTE)
        r = compute_breakdown(db, "u1", "asset_class")
        total_pct = sum(g["pct"] for g in r["groups"])
        assert abs(total_pct - 100.0) < 0.5  # 丸め誤差許容

    def test_no_snapshot_returns_none(self, db):
        assert compute_breakdown(db, "u1", "currency") is None

    def test_unknown_axis_returns_none(self, db):
        import_portfolio_paste(db, "u1", SAMPLE_PASTE)
        assert compute_breakdown(db, "u1", "nonexistent_axis") is None

    def test_manual_tag_override_reflected_in_breakdown(self, db):
        """手動でタグを変更すると内訳集計にも反映される"""
        import_portfolio_paste(db, "u1", SAMPLE_PASTE)
        from backend.models.portfolio import ClassificationAxis
        axis = db.query(ClassificationAxis).filter(
            ClassificationAxis.user_id == "u1", ClassificationAxis.key == "asset_class"
        ).first()
        t = db.query(SecurityTag).filter(
            SecurityTag.security_key == "株式:8306", SecurityTag.axis_id == axis.id
        ).first()
        t.value = "コア日本株"
        t.is_auto = 0
        db.commit()

        r = compute_breakdown(db, "u1", "asset_class")
        by_value = {g["value"]: g["amount_yen"] for g in r["groups"]}
        assert by_value.get("コア日本株") == 300_000
        assert "日本株式" not in by_value  # 唯一の日本株式タグ付き銘柄が付け替わったので消える


class TestListSecuritiesWithTags:
    def test_merges_same_security_across_accounts(self, db):
        text = SAMPLE_PASTE + "9433\tKDDI\t100\t2,000\t2,500\t250,000円\t0円\t50,000円\t20.00%\t楽天証券\n"
        import_portfolio_paste(db, "u1", text)
        securities = list_securities_with_tags(db, "u1")
        keys = [s["security_key"] for s in securities]
        assert len(keys) == len(set(keys))  # 重複なし

    def test_includes_tags_dict(self, db):
        import_portfolio_paste(db, "u1", SAMPLE_PASTE)
        securities = list_securities_with_tags(db, "u1")
        mub = next(s for s in securities if s["security_key"] == "株式:8306")
        assert mub["tags"]["currency"] == "JPY"
        assert mub["tags"]["asset_class"] == "日本株式"

    def test_no_snapshot_returns_empty_list(self, db):
        assert list_securities_with_tags(db, "u1") == []
