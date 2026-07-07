"""
自動取込（フォルダ監視）サービスのテスト
"""
import pytest
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.database import Base
from backend.models.user import User
from backend.models.mf_transaction import MFTransaction
from backend.models.auto_import import AutoImportConfig, ImportedFile
from backend.services.mf_import import (
    sniff_mf_csv,
    import_file_content,
    save_transactions,
    scan_directory_for_user,
)

MF_HEADER = "計算対象\t日付\t内容\t金額（円）\t保有金融機関\t大項目\t中項目\tメモ\t振替\tID"


def mf_csv(rows: list[str]) -> bytes:
    return ("\n".join([MF_HEADER] + rows)).encode("utf-8")


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    user = User(id="u1", email="t@example.com", hashed_password="x")
    session.add(user)
    session.commit()
    yield session
    session.close()


# ──────────────────────────────────────────────
# スニッフィング
# ──────────────────────────────────────────────

class TestSniff:
    def test_mf_csv_detected(self):
        content = mf_csv(["1\t2026/05/10\tスーパー\t-3500\t楽天\t食費\t食料品\t\t0\tid1"])
        assert sniff_mf_csv(content)

    def test_mf_csv_cp932_detected(self):
        content = ("\n".join([MF_HEADER, "1\t2026/05/10\tテスト\t-100\t現金\t食費\t\t\t0\tid"])).encode("cp932")
        assert sniff_mf_csv(content)

    def test_random_csv_rejected(self):
        assert not sniff_mf_csv(b"name,age,city\nAlice,30,Tokyo\n")

    def test_binary_rejected(self):
        assert not sniff_mf_csv(bytes(range(256)) * 4)

    def test_empty_rejected(self):
        assert not sniff_mf_csv(b"")


# ──────────────────────────────────────────────
# 取込・重複排除
# ──────────────────────────────────────────────

class TestImportFileContent:
    def test_basic_import(self, db):
        content = mf_csv([
            "1\t2026/05/10\tスーパー\t-3500\t楽天\t食費\t食料品\t\t0\tid1",
            "1\t2026/05/11\tドラッグ\t-800\t現金\t日用品\t\t\t0\tid2",
        ])
        r = import_file_content(db, "u1", "test.csv", content)
        assert r["status"] == "imported"
        assert r["imported"] == 2
        assert db.query(MFTransaction).count() == 2
        assert db.query(ImportedFile).count() == 1

    def test_same_file_not_reimported(self, db):
        """同一ファイル（ハッシュ一致）は再取込されない"""
        content = mf_csv(["1\t2026/05/10\tスーパー\t-3500\t楽天\t食費\t\t\t0\tid1"])
        r1 = import_file_content(db, "u1", "a.csv", content)
        r2 = import_file_content(db, "u1", "renamed.csv", content)  # 名前が違っても中身同一
        assert r1["status"] == "imported"
        assert r2["status"] == "already_imported"
        assert db.query(MFTransaction).count() == 1

    def test_overlapping_transactions_deduped_by_mf_id(self, db):
        """別ファイルでも同一mf_idのトランザクションはスキップ"""
        c1 = mf_csv([
            "1\t2026/05/10\tスーパー\t-3500\t楽天\t食費\t\t\t0\tid1",
            "1\t2026/05/11\tドラッグ\t-800\t現金\t日用品\t\t\t0\tid2",
        ])
        # 期間が重複するダウンロード（id2が両方に含まれる）
        c2 = mf_csv([
            "1\t2026/05/11\tドラッグ\t-800\t現金\t日用品\t\t\t0\tid2",
            "1\t2026/06/01\tコンビニ\t-500\t現金\t食費\t\t\t0\tid3",
        ])
        import_file_content(db, "u1", "may.csv", c1)
        r = import_file_content(db, "u1", "june.csv", c2)
        assert r["imported"] == 1
        assert r["skipped"] == 1
        assert db.query(MFTransaction).count() == 3

    def test_non_mf_csv_rejected(self, db):
        r = import_file_content(db, "u1", "other.csv", b"name,age\nAlice,30\n")
        assert r["status"] == "not_mf_csv"
        assert db.query(MFTransaction).count() == 0
        # 記録も残さない（後でMF形式に置き換わったら取り込めるように）
        assert db.query(ImportedFile).count() == 0

    def test_duplicate_mf_id_within_single_file(self, db):
        """1ファイル内に同一mf_idが2行あっても1件だけ保存"""
        content = mf_csv([
            "1\t2026/05/10\tスーパー\t-3500\t楽天\t食費\t\t\t0\tdup",
            "1\t2026/05/10\tスーパー\t-3500\t楽天\t食費\t\t\t0\tdup",
        ])
        r = import_file_content(db, "u1", "dup.csv", content)
        assert r["imported"] == 1
        assert r["skipped"] == 1


# ──────────────────────────────────────────────
# フォルダスキャン
# ──────────────────────────────────────────────

class TestScanDirectory:
    def _make_config(self, db, directory: str) -> AutoImportConfig:
        config = AutoImportConfig(user_id="u1", directory=directory, enabled=True)
        db.add(config)
        db.commit()
        return config

    def test_scan_imports_mf_files_only(self, db, tmp_path):
        import os
        # MF明細CSVと無関係CSVを配置
        mf_file = tmp_path / "収入・支出詳細_2026-05-01_2026-05-31.csv"
        mf_file.write_bytes(mf_csv(["1\t2026/05/10\tスーパー\t-3500\t楽天\t食費\t\t\t0\tid1"]))
        (tmp_path / "other.csv").write_bytes(b"name,age\nAlice,30\n")
        # 書き込み直後のファイルはスキップされるのでmtimeを過去にする
        old = 1600000000
        os.utime(mf_file, (old, old))
        os.utime(tmp_path / "other.csv", (old, old))

        config = self._make_config(db, str(tmp_path))
        results = scan_directory_for_user(db, config)

        assert len(results) == 1
        assert results[0]["file_name"] == mf_file.name
        assert db.query(MFTransaction).count() == 1
        assert config.last_scanned_at is not None

    def test_scan_skips_freshly_written_files(self, db, tmp_path):
        """書き込み直後（5秒以内）のファイルは次回スキャンに回す"""
        f = tmp_path / "fresh.csv"
        f.write_bytes(mf_csv(["1\t2026/05/10\tテスト\t-100\t現金\t食費\t\t\t0\tid1"]))
        config = self._make_config(db, str(tmp_path))
        results = scan_directory_for_user(db, config)
        assert results == []
        assert db.query(MFTransaction).count() == 0

    def test_scan_nonexistent_directory_is_safe(self, db):
        config = self._make_config(db, r"C:\no\such\directory\xyz")
        results = scan_directory_for_user(db, config)
        assert results == []

    def test_rescan_does_not_duplicate(self, db, tmp_path):
        import os
        f = tmp_path / "明細.csv"
        f.write_bytes(mf_csv(["1\t2026/05/10\tテスト\t-100\t現金\t食費\t\t\t0\tid1"]))
        os.utime(f, (1600000000, 1600000000))
        config = self._make_config(db, str(tmp_path))
        r1 = scan_directory_for_user(db, config)
        r2 = scan_directory_for_user(db, config)
        assert len(r1) == 1
        assert r2 == []
        assert db.query(MFTransaction).count() == 1
