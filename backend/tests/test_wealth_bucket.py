"""「3つの財布」(長期・中期・短期) 現在額・目標額・達成率のテスト"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.database import Base
from backend.models.user import User
from backend.models.portfolio import ClassificationAxis, SecurityTag, WealthBucketGoal
from backend.services.portfolio_import import import_portfolio_paste
from backend.services.classification import ensure_builtin_axes
from backend.services.wealth_bucket import get_bucket_summary, set_bucket_goal

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
年金
合計：200,000円
名称	取得価額	現在価値	評価損益	評価損益率	取得日	変更	削除
楽天S&P500楽天DC	150,000円	200,000円	50,000円	33.33%
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


class TestGetBucketSummaryDefaults:
    def test_no_data_returns_none(self, db):
        assert get_bucket_summary(db, "u1") is None

    def test_default_classification_by_category(self, db):
        """
        カテゴリ既定値の確認: 年金→長期、現金→短期（1年以内）、株式・投信→長期。
        目標未設定なのでachievement_pctはNone。
        """
        import_portfolio_paste(db, "u1", SAMPLE_PASTE)
        r = get_bucket_summary(db, "u1")
        assert r is not None
        by_value = {b["bucket_value"]: b for b in r["buckets"]}

        # 短期: 現金2口座(100,000+10,000)
        assert by_value["短期（1年以内）"]["current_amount_yen"] == 110_000
        # 長期: 株式300,000 + 投信100,000 + 年金200,000
        assert by_value["長期"]["current_amount_yen"] == 600_000
        assert by_value["中期"]["current_amount_yen"] == 0

        for b in r["buckets"]:
            assert b["target_amount_yen"] is None
            assert b["achievement_pct"] is None


class TestSetBucketGoal:
    def test_sets_and_updates_goal(self, db):
        import_portfolio_paste(db, "u1", SAMPLE_PASTE)
        set_bucket_goal(db, "u1", "長期", 1_000_000)
        r = get_bucket_summary(db, "u1")
        long_term = next(b for b in r["buckets"] if b["bucket_value"] == "長期")
        assert long_term["target_amount_yen"] == 1_000_000
        assert long_term["achievement_pct"] == 60.0  # 600,000/1,000,000

        set_bucket_goal(db, "u1", "長期", 500_000)
        r2 = get_bucket_summary(db, "u1")
        long_term2 = next(b for b in r2["buckets"] if b["bucket_value"] == "長期")
        assert long_term2["target_amount_yen"] == 500_000
        assert long_term2["achievement_pct"] == 120.0  # 600,000/500,000

        # DBに重複行が作られていないこと
        assert db.query(WealthBucketGoal).filter(
            WealthBucketGoal.user_id == "u1", WealthBucketGoal.bucket_value == "長期"
        ).count() == 1

    def test_invalid_bucket_value_raises(self, db):
        with pytest.raises(ValueError):
            set_bucket_goal(db, "u1", "超長期", 1_000_000)

    def test_goal_over_100_percent_achievement(self, db):
        """目標を上回っていても達成率は100%を超えて表示される（キャップしない）"""
        import_portfolio_paste(db, "u1", SAMPLE_PASTE)
        set_bucket_goal(db, "u1", "長期", 100_000)
        r = get_bucket_summary(db, "u1")
        long_term = next(b for b in r["buckets"] if b["bucket_value"] == "長期")
        assert long_term["achievement_pct"] == 600.0


class TestManualOverrideReflectedInBuckets:
    def test_reclassify_holding_moves_between_buckets(self, db):
        """住宅頭金用など、既定値と違う資金使途に手動で付け替えられる"""
        import_portfolio_paste(db, "u1", SAMPLE_PASTE)
        axes = ensure_builtin_axes(db, "u1")
        time_horizon_axis = axes["time_horizon"]

        tag = db.query(SecurityTag).filter(
            SecurityTag.security_key == "株式:8306", SecurityTag.axis_id == time_horizon_axis.id
        ).first()
        tag.value = "中期"
        tag.is_auto = 0
        db.commit()

        r = get_bucket_summary(db, "u1")
        by_value = {b["bucket_value"]: b for b in r["buckets"]}
        assert by_value["中期"]["current_amount_yen"] == 300_000
        assert by_value["長期"]["current_amount_yen"] == 300_000  # 600,000 - 300,000(株式が中期へ移動)
