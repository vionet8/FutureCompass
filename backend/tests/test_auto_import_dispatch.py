"""
自動取込のCSV種別自動判別（家計明細／資産推移／楽天入出金）のテスト
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.database import Base
from backend.models.user import User
from backend.models.mf_transaction import MFTransaction
from backend.models.performance import AssetSnapshot, CashFlowEvent
from backend.services.mf_import import import_file_content

HOUSEHOLD_HEADER = "計算対象\t日付\t内容\t金額（円）\t保有金融機関\t大項目\t中項目\tメモ\t振替\tID"
ASSET_HEADER = "日付,合計（円）,預金・現金（円）,株式(現物)（円）,株式(信用)（円）,投資信託（円）,年金（円）,ポイント（円）"
RAKUTEN_HEADER_BLOCK = (
    "口座開設以来の入出金合計額\n"
    '入金額合計[円],"1000000"\n'
    '出金額合計[円],"500000"\n'
    "\n"
    "入出金日,入金額[円],出金額[円],内容,出金先"
)


def household_csv() -> bytes:
    return ("\n".join([
        HOUSEHOLD_HEADER,
        "1\t2026/05/10\tスーパー\t-3500\t楽天\t食費\t食料品\t\t0\tid1",
    ])).encode("utf-8")


def asset_csv() -> bytes:
    return ("\n".join([
        ASSET_HEADER,
        "2026/07/01,1000000,400000,300000,0,200000,100000,0",
        "2026/06/01,900000,400000,250000,0,180000,70000,0",
    ])).encode("utf-8-sig")


def rakuten_csv() -> bytes:
    return ("\n".join([
        RAKUTEN_HEADER_BLOCK,
        '"2026/06/12","113315","","IPO・PO(自動入金)","楽天銀行"',
        '"2026/06/16","","366448","自動出金(スイープ)","楽天銀行"',
    ])).encode("shift_jis")


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


class TestDispatch:
    def test_household_csv_routed_to_transactions(self, db):
        r = import_file_content(db, "u1", "kakeibo.csv", household_csv())
        assert r["status"] == "imported"
        assert r["file_type"] == "household"
        assert db.query(MFTransaction).count() == 1

    def test_asset_history_csv_routed_to_snapshots(self, db):
        r = import_file_content(db, "u1", "assets.csv", asset_csv())
        assert r["status"] == "imported"
        assert r["file_type"] == "asset_history"
        assert r["imported"] == 2
        assert db.query(AssetSnapshot).count() == 2

    def test_rakuten_csv_routed_to_cashflows(self, db):
        r = import_file_content(db, "u1", "Withdrawallist.csv", rakuten_csv())
        assert r["status"] == "imported"
        assert r["file_type"] == "rakuten_cashflow"
        assert r["imported"] == 1  # スイープは対象外
        assert db.query(CashFlowEvent).count() == 1

    def test_unknown_csv_rejected(self, db):
        r = import_file_content(db, "u1", "random.csv", b"name,age\nAlice,30\n")
        assert r["status"] == "not_mf_csv"
        assert r["file_type"] is None

    def test_same_file_hash_skipped_regardless_of_type(self, db):
        """同一ファイル（ハッシュ一致）は種別を問わず2回目はスキップ"""
        r1 = import_file_content(db, "u1", "assets.csv", asset_csv())
        r2 = import_file_content(db, "u1", "assets_renamed.csv", asset_csv())
        assert r1["status"] == "imported"
        assert r2["status"] == "already_imported"
        assert db.query(AssetSnapshot).count() == 2

    def test_updated_asset_csv_adds_only_new_dates(self, db):
        """MFが再出力した資産推移CSV（既存日付+新規日付）は新規分のみ追加"""
        import_file_content(db, "u1", "assets.csv", asset_csv())
        updated = ("\n".join([
            ASSET_HEADER,
            "2026/08/01,1100000,400000,350000,0,230000,120000,0",  # 新規
            "2026/07/01,1000000,400000,300000,0,200000,100000,0",  # 既存
            "2026/06/01,900000,400000,250000,0,180000,70000,0",    # 既存
        ])).encode("utf-8-sig")
        r = import_file_content(db, "u1", "assets_v2.csv", updated)
        assert r["status"] == "imported"
        assert r["imported"] == 1
        assert r["skipped"] == 2
        assert db.query(AssetSnapshot).count() == 3
