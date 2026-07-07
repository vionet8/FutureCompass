"""
資産推移CSV全履歴インポートのテスト（日付単位dedup）
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.database import Base
from backend.models.user import User
from backend.models.performance import AssetSnapshot
from backend.services.asset_history_import import import_asset_history
from backend.services.csv_parser import CSVParseError

HEADER = "日付,合計（円）,預金・現金（円）,株式(現物)（円）,株式(信用)（円）,投資信託（円）,年金（円）,ポイント（円）"


def asset_csv(rows: list[str]) -> bytes:
    return ("\n".join([HEADER] + rows)).encode("shift_jis")


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


class TestImportAssetHistory:
    def test_basic_import_creates_snapshots(self, db):
        content = asset_csv([
            "2026/07/05,4300000,700000,1500000,0,1800000,300000,0",
            "2026/07/06,4320000,705000,1510000,0,1805000,300000,0",
            "2026/07/07,4341389,729401,1498667,0,1794080,306225,13015",
        ])
        r = import_asset_history(db, "u1", content)
        assert r["status"] == "imported"
        assert r["imported"] == 3
        assert r["skipped"] == 0
        assert db.query(AssetSnapshot).count() == 3

    def test_reimport_identical_is_idempotent(self, db):
        content = asset_csv([
            "2026/07/05,4300000,700000,1500000,0,1800000,300000,0",
            "2026/07/06,4320000,705000,1510000,0,1805000,300000,0",
        ])
        r1 = import_asset_history(db, "u1", content)
        r2 = import_asset_history(db, "u1", content)
        assert r1["imported"] == 2
        assert r2["imported"] == 0
        assert r2["skipped"] == 2
        assert db.query(AssetSnapshot).count() == 2

    def test_incremental_reexport_adds_only_new_dates(self, db):
        first = asset_csv([
            "2026/07/05,4300000,700000,1500000,0,1800000,300000,0",
            "2026/07/06,4320000,705000,1510000,0,1805000,300000,0",
        ])
        # MFが翌日再出力：過去分＋新規1日分
        second = asset_csv([
            "2026/07/05,4300000,700000,1500000,0,1800000,300000,0",
            "2026/07/06,4320000,705000,1510000,0,1805000,300000,0",
            "2026/07/07,4341389,729401,1498667,0,1794080,306225,13015",
        ])
        import_asset_history(db, "u1", first)
        r2 = import_asset_history(db, "u1", second)
        assert r2["imported"] == 1
        assert r2["skipped"] == 2
        assert db.query(AssetSnapshot).count() == 3

    def test_per_user_isolation(self, db):
        db.add(User(id="u2", email="t2@example.com", hashed_password="x"))
        db.commit()
        content = asset_csv(["2026/07/05,4300000,700000,1500000,0,1800000,300000,0"])
        import_asset_history(db, "u1", content)
        import_asset_history(db, "u2", content)
        assert db.query(AssetSnapshot).filter_by(user_id="u1").count() == 1
        assert db.query(AssetSnapshot).filter_by(user_id="u2").count() == 1

    def test_invalid_csv_raises(self, db):
        with pytest.raises(CSVParseError):
            import_asset_history(db, "u1", b"name,age\nAlice,30\n")
