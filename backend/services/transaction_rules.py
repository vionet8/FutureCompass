"""
取引の分類修正: 個別上書き（MFTransaction.*_override）とパターンルール（TransactionRule）を
適用して「実効値」を計算する。優先順位: 個別上書き > ルール（登録順で最初にマッチしたもの）> 元のインポート値。

再インポートしても上書き・ルールは消えない（MFTransactionはmf_idで重複スキップされ、
ルールは読み込み時点で動的適用されるため）。
"""

from dataclasses import dataclass
from sqlalchemy.orm import Session

from ..models.mf_transaction import MFTransaction, TransactionRule


def _rule_matches(rule: TransactionRule, txn: dict) -> bool:
    field_value = txn.get(rule.match_field) or ""
    return rule.match_value.lower() in field_value.lower()


def apply_effective_classification(txn: dict, rules: list[TransactionRule]) -> dict:
    """
    1件の取引dictに対し、個別上書き→ルールの順で実効値を計算し、
    category_major/category_minor/is_transferを実効値に差し替えたdictを返す。
    元の値はcategory_major_raw等に退避し、何が変わったか追跡できるようにする。
    """
    result = dict(txn)
    result["category_major_raw"] = txn["category_major"]
    result["category_minor_raw"] = txn["category_minor"]
    result["is_transfer_raw"] = txn["is_transfer"]
    result["classification_source"] = "imported"

    # ルールを先に適用し、個別上書きで最終的に上書きする（個別上書きが最優先のため）
    for rule in rules:
        if _rule_matches(rule, txn):
            if rule.set_is_transfer is not None:
                result["is_transfer"] = rule.set_is_transfer
            if rule.set_category_major is not None:
                result["category_major"] = rule.set_category_major
            if rule.set_category_minor is not None:
                result["category_minor"] = rule.set_category_minor
            result["classification_source"] = "rule"
            break  # 最初にマッチしたルールのみ適用（複数ルールの競合を避ける）

    if txn.get("category_major_override") is not None:
        result["category_major"] = txn["category_major_override"]
        result["classification_source"] = "manual"
    if txn.get("category_minor_override") is not None:
        result["category_minor"] = txn["category_minor_override"]
        result["classification_source"] = "manual"
    if txn.get("is_transfer_override") is not None:
        result["is_transfer"] = txn["is_transfer_override"]
        result["classification_source"] = "manual"

    return result


def apply_rules_and_overrides(transactions: list[dict], rules: list[TransactionRule]) -> list[dict]:
    """複数の取引dictに対してapply_effective_classificationを一括適用する"""
    return [apply_effective_classification(t, rules) for t in transactions]


def create_rule(
    db: Session, user_id: str, match_field: str, match_value: str,
    set_is_transfer: bool | None = None,
    set_category_major: str | None = None,
    set_category_minor: str | None = None,
) -> TransactionRule:
    if match_field not in ("description", "institution"):
        raise ValueError(f"不正なmatch_fieldです: {match_field}")
    if set_is_transfer is None and set_category_major is None and set_category_minor is None:
        raise ValueError("少なくとも1つの修正内容（振替フラグ・大項目・中項目）を指定してください")

    rule = TransactionRule(
        user_id=user_id, match_field=match_field, match_value=match_value,
        set_is_transfer=set_is_transfer, set_category_major=set_category_major,
        set_category_minor=set_category_minor,
    )
    db.add(rule)
    db.commit()
    return rule


def list_rules(db: Session, user_id: str) -> list[TransactionRule]:
    return (
        db.query(TransactionRule)
        .filter(TransactionRule.user_id == user_id)
        .order_by(TransactionRule.created_at)
        .all()
    )


def delete_rule(db: Session, user_id: str, rule_id: str) -> bool:
    rule = (
        db.query(TransactionRule)
        .filter(TransactionRule.id == rule_id, TransactionRule.user_id == user_id)
        .first()
    )
    if not rule:
        return False
    db.delete(rule)
    db.commit()
    return True


def set_transaction_override(
    db: Session, user_id: str, transaction_id: str,
    category_major: str | None = None,
    category_minor: str | None = None,
    is_transfer: bool | None = None,
    source: str = "manual",
) -> MFTransaction | None:
    """1件の取引に個別上書きを設定する。指定しなかったフィールドは変更しない"""
    from datetime import datetime

    txn = (
        db.query(MFTransaction)
        .filter(MFTransaction.id == transaction_id, MFTransaction.user_id == user_id)
        .first()
    )
    if txn is None:
        return None

    if category_major is not None:
        txn.category_major_override = category_major
    if category_minor is not None:
        txn.category_minor_override = category_minor
    if is_transfer is not None:
        txn.is_transfer_override = is_transfer
    txn.override_source = source
    txn.overridden_at = datetime.utcnow()
    db.commit()
    return txn
