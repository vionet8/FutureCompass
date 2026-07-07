"""
楽天証券入出金履歴CSVのパース・カテゴリ分類・取込テスト
"""
import pytest
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.database import Base
from backend.models.user import User
from backend.models.performance import CashFlowEvent
from backend.services.csv_parser import parse_rakuten_cashflow, CSVParseError
from backend.services.rakuten_cashflow_import import import_rakuten_cashflow

HEADER_BLOCK = (
    "口座開設以来の入出金合計額\n"
    '入金額合計[円],"1000000"\n'
    '出金額合計[円],"500000"\n'
    "\n"
    "入出金日,入金額[円],出金額[円],内容,出金先"
)


def rakuten_csv(rows: list[str]) -> bytes:
    return ("\n".join([HEADER_BLOCK] + rows)).encode("shift_jis")


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


class TestParseRakutenCashflow:
    def test_inflow_category_classified(self):
        content = rakuten_csv([
            '"2026/06/12","113315","","IPO・PO(自動入金)","楽天銀行"',
        ])
        rows = parse_rakuten_cashflow(content)
        assert len(rows) == 1
        assert rows[0]["classification"] == "inflow"
        assert rows[0]["amount_yen"] == 113315

    def test_outflow_category_classified(self):
        content = rakuten_csv([
            '"2026/06/30","","17727","配当金振込","楽天銀行 ラテン支店"',
        ])
        rows = parse_rakuten_cashflow(content)
        assert len(rows) == 1
        assert rows[0]["classification"] == "outflow"
        assert rows[0]["amount_yen"] == -17727

    def test_excluded_category_not_a_cashflow(self):
        """スイープ等の内部振替はamount_yen=Noneではなくclassification=excludeとして識別される"""
        content = rakuten_csv([
            '"2026/06/16","","366448","自動出金(スイープ)","楽天銀行"',
        ])
        rows = parse_rakuten_cashflow(content)
        assert len(rows) == 1
        assert rows[0]["classification"] == "exclude"
        assert rows[0]["amount_yen"] is None

    def test_unknown_category_marked_unclassified(self):
        content = rakuten_csv([
            '"2026/06/16","1000","","謎の新カテゴリ","楽天銀行"',
        ])
        rows = parse_rakuten_cashflow(content)
        assert rows[0]["classification"] == "unclassified"
        assert rows[0]["amount_yen"] is None

    def test_invalid_csv_raises(self):
        with pytest.raises(CSVParseError):
            parse_rakuten_cashflow(b"name,age\nAlice,30\n")

    def test_sorted_by_date_ascending(self):
        content = rakuten_csv([
            '"2026/06/12","113315","","IPO・PO(自動入金)","楽天銀行"',
            '"2026/01/01","5000","","投信積立(自動入金)","楽天銀行"',
        ])
        rows = parse_rakuten_cashflow(content)
        assert rows[0]["date"] == date(2026, 1, 1)
        assert rows[1]["date"] == date(2026, 6, 12)


class TestImportRakutenCashflow:
    def test_imports_only_classified_rows(self, db):
        content = rakuten_csv([
            '"2026/06/12","113315","","IPO・PO(自動入金)","楽天銀行"',      # inflow
            '"2026/06/30","","17727","配当金振込","楽天銀行 ラテン支店"',   # outflow
            '"2026/06/16","","366448","自動出金(スイープ)","楽天銀行"',     # exclude
        ])
        r = import_rakuten_cashflow(db, "u1", content)
        assert r["imported"] == 2
        assert db.query(CashFlowEvent).count() == 2
        events = db.query(CashFlowEvent).order_by(CashFlowEvent.flow_date).all()
        assert events[0].amount_yen == 113315
        assert events[0].flow_type == "deposit"
        assert events[1].amount_yen == -17727
        assert events[1].flow_type == "withdrawal"
        assert all(e.source == "rakuten_csv" for e in events)

    def test_reimport_is_idempotent(self, db):
        content = rakuten_csv([
            '"2026/06/12","113315","","IPO・PO(自動入金)","楽天銀行"',
        ])
        r1 = import_rakuten_cashflow(db, "u1", content)
        r2 = import_rakuten_cashflow(db, "u1", content)
        assert r1["imported"] == 1
        assert r2["imported"] == 0
        assert r2["skipped_duplicate"] == 1
        assert db.query(CashFlowEvent).count() == 1

    def test_reports_unclassified_count(self, db):
        content = rakuten_csv([
            '"2026/06/12","1000","","謎の新カテゴリ","楽天銀行"',
        ])
        r = import_rakuten_cashflow(db, "u1", content)
        assert r["unclassified"] == 1
        assert r["imported"] == 0
        assert db.query(CashFlowEvent).count() == 0

    def test_excluded_not_counted_as_unclassified(self, db):
        """
        exclude(対象外)とunclassified(未知カテゴリ)を混同しない回帰テスト。
        以前はamount_yen is Noneのみで判定していたため、両方が誤ってunclassifiedに
        カウントされていた。
        """
        content = rakuten_csv([
            '"2026/06/16","","366448","自動出金(スイープ)","楽天銀行"',      # exclude
            '"2026/06/12","1000","","謎の新カテゴリ","楽天銀行"',            # unclassified
        ])
        r = import_rakuten_cashflow(db, "u1", content)
        assert r["excluded"] == 1
        assert r["unclassified"] == 1
