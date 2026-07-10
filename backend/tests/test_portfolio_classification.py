"""分類ロジック（自動分類・軸管理）のテスト"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.database import Base
from backend.models.user import User
from backend.models.portfolio import ClassificationAxis, SecurityTag
from backend.services.classification import (
    auto_classify,
    ensure_builtin_axes,
    apply_auto_classification,
    BUILTIN_AXES,
)
from backend.services.portfolio_analysis import (
    set_security_excluded,
    set_category_excluded,
    bulk_set_category_tag,
    compute_breakdown,
    list_securities_with_tags,
)
from backend.services.portfolio_import import import_portfolio_paste


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


class TestAutoClassify:
    def test_jp_stock_known_code(self):
        r = auto_classify("株式", "三菱UFJフィナンシャルG", "8306")
        assert r["currency"] == "JPY"
        assert r["asset_class"] == "日本株式"
        assert r["product_type"] == "金融"  # 東証33業種の「銀行」ではなくGICS類似の大分類

    def test_jp_stock_sectors_are_consolidated_not_duplicated(self):
        """
        以前は東証33業種そのまま(食品/情報通信が銘柄ごとに重複)だったが、
        GICS類似の大分類に統合されて重複しないことを確認する回帰テスト。
        """
        food1 = auto_classify("株式", "日東富士", "2003")
        food2 = auto_classify("株式", "伊藤ハム米久HD", "2296")
        assert food1["product_type"] == food2["product_type"] == "生活必需品"

    def test_jp_stock_cyclicality_derived_from_sector(self):
        """金融セクターは景気敏感、生活必需品はディフェンシブに自動分類される"""
        bank = auto_classify("株式", "三菱UFJフィナンシャルG", "8306")
        food = auto_classify("株式", "日東富士", "2003")
        assert bank["cyclicality"] == "景気敏感"
        assert food["cyclicality"] == "ディフェンシブ"

    def test_us_etf_known_ticker(self):
        r = auto_classify("株式", "バンガード 米国増配株式ETF", "VIG")
        assert r["currency"] == "USD"
        assert r["asset_class"] == "米国株式"

    def test_us_ticker_cyclicality_individually_specified(self):
        vdc = auto_classify("株式", "バンガード・米国生活必需品セクターETF", "VDC")
        msft = auto_classify("株式", "マイクロソフト", "MSFT")
        vig = auto_classify("株式", "バンガード 米国増配株式ETF", "VIG")
        assert vdc["cyclicality"] == "ディフェンシブ"
        assert msft["cyclicality"] == "景気敏感"
        assert vig["cyclicality"] == "未分類"  # 分散型ETFは特定セクターに偏らないため未分類

    def test_fund_cyclicality_is_unclassified(self):
        """分散型インデックスファンドは景気敏感/ディフェンシブが定まらないため未分類"""
        r = auto_classify("投資信託", "eMAXIS Slim 全世界株式(オール・カントリー)", None)
        assert r["cyclicality"] == "未分類"

    def test_unknown_stock_code_falls_back_to_unclassified(self):
        r = auto_classify("株式", "架空商事", "9999")
        assert r["asset_class"] == "未分類"

    def test_gold_fund_classified_as_gold(self):
        """ゴールドファンドは資産クラス「金・コモディティ」に分類される（ユーザー要望の具体例）"""
        r = auto_classify("投資信託", "SBI・iシェアーズ・ゴールドファンド(為替ヘッジなし)", None)
        assert r["asset_class"] == "金・コモディティ"
        assert r["product_type"] == "ゴールド"
        assert r["currency"] == "JPY"

    def test_sp500_fund_classified_as_us_equity(self):
        r = auto_classify("投資信託", "eMAXIS Slim 米国株式(S&P500)", None)
        assert r["asset_class"] == "米国株式"
        assert "S&P500" in r["product_type"]

    def test_all_country_fund_classified_separately_from_us(self):
        r = auto_classify("投資信託", "eMAXIS Slim 全世界株式(オール・カントリー)", None)
        assert r["asset_class"] == "全世界株式"

    def test_usd_mmf_classified_as_cash(self):
        r = auto_classify("投資信託", "ノーザン・トラスト・米ドル・リクイディティ・ファンド(楽天・米ドルMMF)", None)
        assert r["asset_class"] == "現金"

    def test_cash_account_default(self):
        r = auto_classify("現金", "タンス預金", None)
        assert r["currency"] == "JPY"
        assert r["asset_class"] == "現金"

    def test_usd_cash_account(self):
        r = auto_classify("現金", "米ドル 現金", None)
        assert r["currency"] == "USD"

    def test_point_category(self):
        r = auto_classify("ポイント", "永久不滅ポイント", None)
        assert r["asset_class"] == "ポイント"


class TestEnsureBuiltinAxes:
    def test_creates_builtin_axes(self, db):
        axes = ensure_builtin_axes(db, "u1")
        assert set(axes.keys()) == {k for k, _ in BUILTIN_AXES}
        assert db.query(ClassificationAxis).filter(ClassificationAxis.user_id == "u1").count() == len(BUILTIN_AXES)

    def test_idempotent(self, db):
        ensure_builtin_axes(db, "u1")
        ensure_builtin_axes(db, "u1")
        assert db.query(ClassificationAxis).filter(ClassificationAxis.user_id == "u1").count() == len(BUILTIN_AXES)


class TestApplyAutoClassification:
    def test_creates_tags_for_new_security(self, db):
        apply_auto_classification(db, "u1", "株式", "三菱UFJフィナンシャルG", "8306", "株式:8306")
        db.commit()
        tags = db.query(SecurityTag).filter(SecurityTag.security_key == "株式:8306").all()
        assert len(tags) == len(BUILTIN_AXES)
        assert all(t.is_auto == 1 for t in tags)

    def test_does_not_overwrite_user_edited_tag(self, db):
        """手動修正済み(is_auto=0)のタグは自動分類で上書きされない"""
        apply_auto_classification(db, "u1", "株式", "三菱UFJフィナンシャルG", "8306", "株式:8306")
        db.commit()
        axes = ensure_builtin_axes(db, "u1")
        tag = (
            db.query(SecurityTag)
            .filter(SecurityTag.security_key == "株式:8306", SecurityTag.axis_id == axes["asset_class"].id)
            .first()
        )
        tag.value = "ユーザー独自分類"
        tag.is_auto = 0
        db.commit()

        # 再度自動分類を実行（2回目の貼り付けを想定）
        apply_auto_classification(db, "u1", "株式", "三菱UFJフィナンシャルG", "8306", "株式:8306")
        db.commit()

        tag_after = (
            db.query(SecurityTag)
            .filter(SecurityTag.security_key == "株式:8306", SecurityTag.axis_id == axes["asset_class"].id)
            .first()
        )
        assert tag_after.value == "ユーザー独自分類"


SAMPLE_PASTE_FOR_EXCLUSION = """預金・現金
合計：100,000円
種類・名称	残高	保有金融機関	変更	削除
タンス預金	100,000円	タンス預金
株式(現物)
合計：300,000円
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
ポイント
合計：10,000円
名称	種類	ポイント・マイル数	換算レート	現在の価値	有効期限	保有金融機関	変更	削除
永久不滅ポイント	ポイント	200ポイント	5.00	1,000円		セゾンパールカード
少額ポイントB	ポイント	100ポイント	1.00	9,000円		テストカード
"""


class TestSecurityExclusion:
    def test_excluded_security_removed_from_breakdown(self, db):
        import_portfolio_paste(db, "u1", SAMPLE_PASTE_FOR_EXCLUSION)
        before = compute_breakdown(db, "u1", "asset_class")
        total_before = before["total_value_yen"]

        set_security_excluded(db, "u1", "株式:8306", True)
        after = compute_breakdown(db, "u1", "asset_class")
        assert after["total_value_yen"] == total_before - 300_000

    def test_unexclude_restores_in_breakdown(self, db):
        import_portfolio_paste(db, "u1", SAMPLE_PASTE_FOR_EXCLUSION)
        total_before = compute_breakdown(db, "u1", "asset_class")["total_value_yen"]

        set_security_excluded(db, "u1", "株式:8306", True)
        set_security_excluded(db, "u1", "株式:8306", False)
        after = compute_breakdown(db, "u1", "asset_class")
        assert after["total_value_yen"] == total_before

    def test_excluded_flag_reflected_in_securities_list(self, db):
        import_portfolio_paste(db, "u1", SAMPLE_PASTE_FOR_EXCLUSION)
        set_security_excluded(db, "u1", "株式:8306", True)

        securities = list_securities_with_tags(db, "u1")
        stock = next(s for s in securities if s["security_key"] == "株式:8306")
        cash = next(s for s in securities if s["security_key"] == "現金:タンス預金")
        assert stock["excluded"] is True
        assert cash["excluded"] is False

    def test_double_exclude_is_idempotent(self, db):
        """同じ銘柄を2回除外指定してもDBに重複レコードができない"""
        import_portfolio_paste(db, "u1", SAMPLE_PASTE_FOR_EXCLUSION)
        set_security_excluded(db, "u1", "株式:8306", True)
        set_security_excluded(db, "u1", "株式:8306", True)
        securities = list_securities_with_tags(db, "u1")
        stock = next(s for s in securities if s["security_key"] == "株式:8306")
        assert stock["excluded"] is True


class TestCategoryExclusion:
    def test_bulk_excludes_all_securities_in_category(self, db):
        """カテゴリ一括除外: ポイント2件が両方とも計算対象外になる"""
        import_portfolio_paste(db, "u1", SAMPLE_PASTE_FOR_EXCLUSION)
        count = set_category_excluded(db, "u1", "ポイント", True)
        assert count == 2

        securities = list_securities_with_tags(db, "u1")
        points = [s for s in securities if s["category"] == "ポイント"]
        assert all(s["excluded"] for s in points)
        # 他カテゴリには影響しない
        stock = next(s for s in securities if s["security_key"] == "株式:8306")
        assert stock["excluded"] is False

    def test_bulk_exclude_reflected_in_breakdown_total(self, db):
        import_portfolio_paste(db, "u1", SAMPLE_PASTE_FOR_EXCLUSION)
        total_before = compute_breakdown(db, "u1", "asset_class")["total_value_yen"]
        set_category_excluded(db, "u1", "ポイント", True)
        total_after = compute_breakdown(db, "u1", "asset_class")["total_value_yen"]
        assert total_after == total_before - 10_000

    def test_bulk_unexclude_restores_category(self, db):
        import_portfolio_paste(db, "u1", SAMPLE_PASTE_FOR_EXCLUSION)
        set_category_excluded(db, "u1", "ポイント", True)
        set_category_excluded(db, "u1", "ポイント", False)
        securities = list_securities_with_tags(db, "u1")
        points = [s for s in securities if s["category"] == "ポイント"]
        assert all(not s["excluded"] for s in points)


class TestBulkSetCategoryTag:
    def test_sets_tag_for_all_securities_in_category(self, db):
        """例: 株式の資金の時間軸を一括で「中期」にする"""
        import_portfolio_paste(db, "u1", SAMPLE_PASTE_FOR_EXCLUSION)
        ensure_builtin_axes(db, "u1")
        count = bulk_set_category_tag(db, "u1", "株式", "time_horizon", "中期")
        assert count == 1  # SAMPLE_PASTE_FOR_EXCLUSIONの株式は8306の1件のみ

        securities = list_securities_with_tags(db, "u1")
        stock = next(s for s in securities if s["security_key"] == "株式:8306")
        assert stock["tags"]["time_horizon"] == "中期"

    def test_bulk_set_marks_as_manual_not_auto(self, db):
        """一括設定はis_auto=0になり、以後の自動分類・再インポートで上書きされない"""
        import_portfolio_paste(db, "u1", SAMPLE_PASTE_FOR_EXCLUSION)
        axes = ensure_builtin_axes(db, "u1")
        bulk_set_category_tag(db, "u1", "株式", "time_horizon", "中期")

        tag = db.query(SecurityTag).filter(
            SecurityTag.security_key == "株式:8306", SecurityTag.axis_id == axes["time_horizon"].id
        ).first()
        assert tag.is_auto == 0

    def test_bulk_set_only_affects_specified_category(self, db):
        import_portfolio_paste(db, "u1", SAMPLE_PASTE_FOR_EXCLUSION)
        ensure_builtin_axes(db, "u1")
        bulk_set_category_tag(db, "u1", "ポイント", "cyclicality", "対象外")

        securities = list_securities_with_tags(db, "u1")
        stock = next(s for s in securities if s["security_key"] == "株式:8306")
        assert stock["tags"].get("cyclicality") != "対象外"

    def test_bulk_set_overwrites_existing_tag(self, db):
        """既にタグがある銘柄でも一括設定で値が上書きされる"""
        import_portfolio_paste(db, "u1", SAMPLE_PASTE_FOR_EXCLUSION)
        ensure_builtin_axes(db, "u1")
        bulk_set_category_tag(db, "u1", "株式", "time_horizon", "中期")
        bulk_set_category_tag(db, "u1", "株式", "time_horizon", "短期（1年以内）")

        securities = list_securities_with_tags(db, "u1")
        stock = next(s for s in securities if s["security_key"] == "株式:8306")
        assert stock["tags"]["time_horizon"] == "短期（1年以内）"

    def test_unknown_axis_returns_none(self, db):
        import_portfolio_paste(db, "u1", SAMPLE_PASTE_FOR_EXCLUSION)
        result = bulk_set_category_tag(db, "u1", "株式", "nonexistent_axis", "何か")
        assert result is None

    def test_no_snapshot_returns_none(self, db):
        result = bulk_set_category_tag(db, "u1", "株式", "time_horizon", "中期")
        assert result is None
