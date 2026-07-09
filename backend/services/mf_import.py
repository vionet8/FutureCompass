"""
マネーフォワードCSVの取込サービス

手動アップロード（API）とフォルダ監視（自動取込）の両方から使われる共通ロジック。
"""

import hashlib
import logging
import os
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from ..models.mf_transaction import MFTransaction
from ..models.auto_import import AutoImportConfig, ImportedFile
from .mf_analyzer import parse_mf_csv
from .csv_parser import _sniff_moneyforward_assets, _sniff_rakuten_cashflow, CSVParseError
from .asset_history_import import import_asset_history
from .rakuten_cashflow_import import import_rakuten_cashflow

logger = logging.getLogger("mf_import")

# MFの入出金明細CSVに必ず含まれるヘッダー列（内容スニッフィング用）
MF_HEADER_MARKERS = ("計算対象", "金額（円）", "大項目")

# 書き込み途中のファイルを避けるため、最終更新からこの秒数は待つ
WRITE_SETTLE_SECONDS = 5


def sniff_mf_csv(content: bytes) -> bool:
    """先頭行を見てマネーフォワードの入出金明細CSVかどうか判定する"""
    head = content[:2048]
    for enc in ("utf-8-sig", "cp932", "utf-8"):
        try:
            first_line = head.decode(enc, errors="strict").splitlines()[0]
        except (UnicodeDecodeError, IndexError):
            continue
        if all(marker in first_line for marker in MF_HEADER_MARKERS):
            return True
    return False


def file_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def save_transactions(db: Session, user_id: str, transactions: list[dict]) -> tuple[int, int]:
    """
    トランザクションをDBに保存する。mf_idで重複排除。
    戻り値: (新規件数, スキップ件数)
    """
    existing_ids = set(
        row[0] for row in db.query(MFTransaction.mf_id)
        .filter(
            MFTransaction.user_id == user_id,
            MFTransaction.mf_id.isnot(None),
        ).all()
    )

    new_count = 0
    skip_count = 0
    for t in transactions:
        if t["mf_id"] and t["mf_id"] in existing_ids:
            skip_count += 1
            continue
        db.add(MFTransaction(
            user_id=user_id,
            mf_id=t["mf_id"],
            transaction_date=t["date"],
            description=t["description"],
            amount_yen=t["amount_yen"],
            institution=t["institution"],
            category_major=t["category_major"],
            category_minor=t["category_minor"],
            memo=t["memo"],
            is_transfer=t["is_transfer"],
            is_target=t["is_target"],
        ))
        if t["mf_id"]:
            existing_ids.add(t["mf_id"])
        new_count += 1

    db.commit()
    return new_count, skip_count


def import_file_content(
    db: Session, user_id: str, file_name: str, content: bytes, source: str = "manual",
) -> dict:
    """
    CSVバイト列を内容スニッフィングで種別判定して取り込む。ファイルハッシュで既取込チェック。
    対応: 家計明細（MF入出金明細）／資産推移（MF）／楽天証券入出金履歴
    戻り値: {status, imported, skipped, file_name, file_type}
      status: "imported" | "already_imported" | "not_mf_csv" | "no_transactions"
      file_type: "household" | "asset_history" | "rakuten_cashflow" | None
    """
    fhash = file_sha256(content)
    already = (
        db.query(ImportedFile)
        .filter(ImportedFile.user_id == user_id, ImportedFile.file_hash == fhash)
        .first()
    )
    if already:
        return {"status": "already_imported", "imported": 0, "skipped": 0,
                "file_name": file_name, "file_type": None}

    if sniff_mf_csv(content):
        result = _import_household(db, user_id, content)
        file_type = "household"
    elif _sniff_moneyforward_assets(content):
        result = _import_typed(import_asset_history, db, user_id, content)
        file_type = "asset_history"
    elif _sniff_rakuten_cashflow(content):
        result = _import_typed(import_rakuten_cashflow, db, user_id, content)
        file_type = "rakuten_cashflow"
    else:
        return {"status": "not_mf_csv", "imported": 0, "skipped": 0,
                "file_name": file_name, "file_type": None}

    if result["status"] != "imported":
        return {**result, "file_name": file_name, "file_type": file_type}

    db.add(ImportedFile(
        user_id=user_id,
        file_name=file_name,
        file_hash=fhash,
        imported_count=result["imported"],
        skipped_count=result["skipped"],
        source=source,
    ))
    db.commit()

    return {"status": "imported", "imported": result["imported"], "skipped": result["skipped"],
            "file_name": file_name, "file_type": file_type}


def _import_household(db: Session, user_id: str, content: bytes) -> dict:
    """家計明細CSV（MF入出金明細）の取込。import_file_content内部用"""
    try:
        transactions = parse_mf_csv(content)
    except ValueError:
        return {"status": "not_mf_csv", "imported": 0, "skipped": 0}
    if not transactions:
        return {"status": "no_transactions", "imported": 0, "skipped": 0}
    new_count, skip_count = save_transactions(db, user_id, transactions)
    return {"status": "imported", "imported": new_count, "skipped": skip_count}


def _import_typed(importer, db: Session, user_id: str, content: bytes) -> dict:
    """
    資産推移／楽天入出金の取込をimport_file_contentの戻り値形式に揃える。
    各インポーターは冪等（日付・内容ベースのdedup）なので再実行しても安全。
    """
    try:
        r = importer(db, user_id, content)
    except CSVParseError:
        return {"status": "not_mf_csv", "imported": 0, "skipped": 0}
    skipped = r.get("skipped", r.get("skipped_duplicate", 0))
    return {"status": "imported", "imported": r["imported"], "skipped": skipped}


def scan_directory_for_user(db: Session, config: AutoImportConfig) -> list[dict]:
    """
    設定された監視フォルダをスキャンし、新しいMF明細CSVを取り込む。
    取り込んだ（または判定した）ファイルの結果リストを返す。
    """
    results = []
    directory = Path(config.directory)
    if not directory.is_dir():
        logger.warning("自動取込: フォルダが存在しません: %s", config.directory)
        return results

    now = time.time()
    for path in sorted(directory.glob("*.csv")):
        try:
            stat = path.stat()
            # 書き込み途中のファイルはスキップ（次回スキャンで拾う）
            if now - stat.st_mtime < WRITE_SETTLE_SECONDS:
                continue
            # 100MB超は対象外（誤配置ファイル対策）
            if stat.st_size > 100 * 1024 * 1024:
                continue
            content = path.read_bytes()
        except OSError as e:
            logger.warning("自動取込: 読み込み失敗 %s: %s", path.name, e)
            continue

        result = import_file_content(db, config.user_id, path.name, content, source="auto")
        if result["status"] == "imported":
            logger.info(
                "自動取込: %s (%s) → 新規%d件・重複%d件",
                path.name, result.get("file_type"), result["imported"], result["skipped"],
            )
            results.append(result)
        # not_mf_csv / already_imported は毎回スニッフされるが軽量なので記録しない

    config.last_scanned_at = datetime.utcnow()
    db.commit()
    return results


def scan_all_users(session_factory) -> int:
    """全ユーザーの有効な自動取込設定をスキャンする。取り込んだファイル数を返す"""
    db = session_factory()
    imported_total = 0
    try:
        configs = (
            db.query(AutoImportConfig)
            .filter(AutoImportConfig.enabled == True)  # noqa: E712
            .all()
        )
        for config in configs:
            results = scan_directory_for_user(db, config)
            imported_total += len(results)
    except Exception:
        logger.exception("自動取込スキャンでエラー")
    finally:
        db.close()
    return imported_total


def default_watch_directory() -> str:
    """デフォルトの監視フォルダ（ユーザーのダウンロードフォルダ）"""
    return str(Path(os.path.expanduser("~")) / "Downloads")
