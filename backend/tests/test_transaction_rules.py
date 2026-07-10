"""取引の個別上書き・パターンルール適用のテスト"""
import pytest
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.database import Base
from backend.models.user import User
from backend.models.mf_transaction import MFTransaction, TransactionRule
from backend.services.transaction_rules import (
    apply_effective_classification,
    apply_rules_and_overrides,
    create_rule,
    list_rules,
    delete_rule,
    set_transaction_override,
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


def make_txn(**overrides) -> dict:
    base = {
        "id": "t1",
        "date": date(2026, 6, 15),
        "description": "SBI証券 投信積立",
        "amount_yen": -50000,
        "institution": "楽天銀行",
        "category_major": "水道・光熱費",
        "category_minor": "電気",
        "is_transfer": False,
        "category_major_override": None,
        "category_minor_override": None,
        "is_transfer_override": None,
    }
    base.update(overrides)
    return base


class TestApplyEffectiveClassification:
    def test_no_rule_no_override_keeps_imported_values(self):
        txn = make_txn()
        result = apply_effective_classification(txn, rules=[])
        assert result["category_major"] == "水道・光熱費"
        assert result["is_transfer"] is False
        assert result["classification_source"] == "imported"

    def test_manual_override_takes_precedence(self):
        txn = make_txn(is_transfer_override=True)
        result = apply_effective_classification(txn, rules=[])
        assert result["is_transfer"] is True
        assert result["classification_source"] == "manual"

    def test_raw_values_preserved_for_audit(self):
        """実効値を書き換えても、元のインポート値はraw系フィールドに残る"""
        txn = make_txn(is_transfer_override=True)
        result = apply_effective_classification(txn, rules=[])
        assert result["is_transfer_raw"] is False
        assert result["is_transfer"] is True


class TestApplyRulesAndOverrides:
    def test_matching_rule_sets_is_transfer(self):
        rule = TransactionRule(
            id="r1", user_id="u1", match_field="description", match_value="SBI証券",
            set_is_transfer=True,
        )
        results = apply_rules_and_overrides([make_txn()], [rule])
        assert results[0]["is_transfer"] is True
        assert results[0]["classification_source"] == "rule"

    def test_non_matching_rule_has_no_effect(self):
        rule = TransactionRule(
            id="r1", user_id="u1", match_field="description", match_value="無関係な文字列",
            set_is_transfer=True,
        )
        results = apply_rules_and_overrides([make_txn()], [rule])
        assert results[0]["is_transfer"] is False

    def test_manual_override_wins_over_rule(self):
        """ルールが振替=Trueにしても、個別上書きでFalseにしていればFalseが優先される"""
        rule = TransactionRule(
            id="r1", user_id="u1", match_field="description", match_value="SBI証券",
            set_is_transfer=True,
        )
        txn = make_txn(is_transfer_override=False)
        results = apply_rules_and_overrides([txn], [rule])
        assert results[0]["is_transfer"] is False
        assert results[0]["classification_source"] == "manual"

    def test_institution_field_matching(self):
        rule = TransactionRule(
            id="r1", user_id="u1", match_field="institution", match_value="楽天銀行",
            set_category_major="振替",
        )
        results = apply_rules_and_overrides([make_txn()], [rule])
        assert results[0]["category_major"] == "振替"

    def test_case_insensitive_match(self):
        rule = TransactionRule(
            id="r1", user_id="u1", match_field="description", match_value="sbi証券",
            set_is_transfer=True,
        )
        results = apply_rules_and_overrides([make_txn()], [rule])
        assert results[0]["is_transfer"] is True

    def test_first_matching_rule_wins_when_multiple_match(self):
        rule1 = TransactionRule(
            id="r1", user_id="u1", match_field="description", match_value="SBI証券",
            set_category_major="投資",
        )
        rule2 = TransactionRule(
            id="r2", user_id="u1", match_field="description", match_value="投信",
            set_category_major="振替",
        )
        results = apply_rules_and_overrides([make_txn()], [rule1, rule2])
        assert results[0]["category_major"] == "投資"  # rule1が先勝ち


class TestCreateRule:
    def test_creates_and_persists(self, db):
        rule = create_rule(db, "u1", "description", "SBI証券", set_is_transfer=True)
        assert rule.id is not None
        assert db.query(TransactionRule).filter(TransactionRule.user_id == "u1").count() == 1

    def test_invalid_match_field_raises(self, db):
        with pytest.raises(ValueError):
            create_rule(db, "u1", "amount", "1000", set_is_transfer=True)

    def test_no_action_specified_raises(self, db):
        with pytest.raises(ValueError):
            create_rule(db, "u1", "description", "SBI証券")

    def test_list_rules_ordered_by_creation(self, db):
        create_rule(db, "u1", "description", "A", set_is_transfer=True)
        create_rule(db, "u1", "description", "B", set_is_transfer=True)
        rules = list_rules(db, "u1")
        assert [r.match_value for r in rules] == ["A", "B"]

    def test_delete_rule(self, db):
        rule = create_rule(db, "u1", "description", "A", set_is_transfer=True)
        assert delete_rule(db, "u1", rule.id) is True
        assert list_rules(db, "u1") == []

    def test_delete_nonexistent_rule_returns_false(self, db):
        assert delete_rule(db, "u1", "nonexistent") is False

    def test_delete_other_users_rule_fails(self, db):
        db.add(User(id="u2", email="t2@example.com", hashed_password="x"))
        db.commit()
        rule = create_rule(db, "u1", "description", "A", set_is_transfer=True)
        assert delete_rule(db, "u2", rule.id) is False


class TestSetTransactionOverride:
    def test_sets_override_fields(self, db):
        txn = MFTransaction(
            id="t1", user_id="u1", transaction_date=date(2026, 6, 15),
            description="test", amount_yen=-1000, category_major="食費",
        )
        db.add(txn)
        db.commit()

        result = set_transaction_override(db, "u1", "t1", is_transfer=True, source="manual")
        assert result is not None
        assert result.is_transfer_override is True
        assert result.override_source == "manual"
        assert result.overridden_at is not None

    def test_nonexistent_transaction_returns_none(self, db):
        assert set_transaction_override(db, "u1", "nonexistent", is_transfer=True) is None

    def test_partial_update_does_not_clear_other_fields(self, db):
        """category_majorだけ更新してもis_transfer_overrideは変わらない"""
        txn = MFTransaction(
            id="t1", user_id="u1", transaction_date=date(2026, 6, 15),
            description="test", amount_yen=-1000, category_major="食費",
        )
        db.add(txn)
        db.commit()
        set_transaction_override(db, "u1", "t1", is_transfer=True)
        set_transaction_override(db, "u1", "t1", category_major="振替")

        updated = db.query(MFTransaction).filter(MFTransaction.id == "t1").first()
        assert updated.is_transfer_override is True
        assert updated.category_major_override == "振替"
