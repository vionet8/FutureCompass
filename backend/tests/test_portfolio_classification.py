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
        assert r["product_type"] == "銀行"

    def test_us_etf_known_ticker(self):
        r = auto_classify("株式", "バンガード 米国増配株式ETF", "VIG")
        assert r["currency"] == "USD"
        assert r["asset_class"] == "米国株式"

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
