"""最新のポートフォリオスナップショットを、指定した分類軸で集計する"""

from sqlalchemy.orm import Session

from ..models.portfolio import PortfolioSnapshot, Holding, ClassificationAxis, SecurityTag, SecurityExclusion

UNCLASSIFIED = "未分類"


def get_latest_snapshot(db: Session, user_id: str) -> PortfolioSnapshot | None:
    return (
        db.query(PortfolioSnapshot)
        .filter(PortfolioSnapshot.user_id == user_id)
        .order_by(PortfolioSnapshot.created_at.desc())
        .first()
    )


def _excluded_security_keys(db: Session, user_id: str) -> set[str]:
    return {
        e.security_key for e in db.query(SecurityExclusion)
        .filter(SecurityExclusion.user_id == user_id).all()
    }


def compute_breakdown(db: Session, user_id: str, axis_key: str) -> dict | None:
    """
    最新スナップショットの保有銘柄を、指定軸のタグ値で集計する。
    「計算対象外」に設定された銘柄(SecurityExclusion)は集計から除く。
    戻り値: {"snapshot_created_at", "total_value_yen", "groups": [{"value", "amount_yen", "pct"}, ...]}
    スナップショットが無い、または軸が存在しない場合はNone。
    """
    snapshot = get_latest_snapshot(db, user_id)
    if snapshot is None:
        return None

    axis = (
        db.query(ClassificationAxis)
        .filter(ClassificationAxis.user_id == user_id, ClassificationAxis.key == axis_key)
        .first()
    )
    if axis is None:
        return None

    excluded = _excluded_security_keys(db, user_id)
    holdings = [
        h for h in db.query(Holding).filter(Holding.snapshot_id == snapshot.id).all()
        if h.security_key not in excluded
    ]
    if not holdings:
        return None

    tags = {
        t.security_key: t.value for t in db.query(SecurityTag)
        .filter(SecurityTag.user_id == user_id, SecurityTag.axis_id == axis.id).all()
    }

    totals: dict[str, int] = {}
    for h in holdings:
        value = tags.get(h.security_key, UNCLASSIFIED)
        totals[value] = totals.get(value, 0) + h.market_value_yen

    total_value = sum(totals.values())
    groups = [
        {
            "value": k,
            "amount_yen": v,
            "pct": round(v / total_value * 100, 1) if total_value else 0.0,
        }
        for k, v in totals.items()
    ]
    groups.sort(key=lambda g: -g["amount_yen"])

    return {
        "snapshot_created_at": snapshot.created_at,
        "total_value_yen": total_value,
        "groups": groups,
    }


def list_securities_with_tags(db: Session, user_id: str) -> list[dict]:
    """
    最新スナップショットに含まれる銘柄一覧を、全軸のタグ・除外フラグ付きで返す（タグ編集UI用）。
    同一security_keyが複数の保有(異なる口座等)にまたがる場合は評価額を合算する。
    """
    snapshot = get_latest_snapshot(db, user_id)
    if snapshot is None:
        return []

    holdings = db.query(Holding).filter(Holding.snapshot_id == snapshot.id).all()
    merged: dict[str, dict] = {}
    for h in holdings:
        if h.security_key not in merged:
            merged[h.security_key] = {
                "security_key": h.security_key,
                "category": h.category,
                "name": h.name,
                "symbol_code": h.symbol_code,
                "market_value_yen": 0,
            }
        merged[h.security_key]["market_value_yen"] += h.market_value_yen

    axes = db.query(ClassificationAxis).filter(ClassificationAxis.user_id == user_id).all()
    axis_by_id = {a.id: a.key for a in axes}
    tags_by_security: dict[str, dict[str, str]] = {}
    for t in db.query(SecurityTag).filter(SecurityTag.user_id == user_id).all():
        axis_key = axis_by_id.get(t.axis_id)
        if axis_key is None:
            continue
        tags_by_security.setdefault(t.security_key, {})[axis_key] = t.value

    excluded = _excluded_security_keys(db, user_id)

    result = list(merged.values())
    for item in result:
        item["tags"] = tags_by_security.get(item["security_key"], {})
        item["excluded"] = item["security_key"] in excluded
    result.sort(key=lambda x: -x["market_value_yen"])
    return result


def set_security_excluded(db: Session, user_id: str, security_key: str, excluded: bool) -> None:
    """1銘柄の計算対象外フラグを設定・解除する"""
    existing = (
        db.query(SecurityExclusion)
        .filter(SecurityExclusion.user_id == user_id, SecurityExclusion.security_key == security_key)
        .first()
    )
    if excluded and existing is None:
        db.add(SecurityExclusion(user_id=user_id, security_key=security_key))
    elif not excluded and existing is not None:
        db.delete(existing)
    db.commit()


def set_category_excluded(db: Session, user_id: str, category: str, excluded: bool) -> int:
    """
    最新スナップショットのうち指定カテゴリに属する銘柄すべての計算対象外フラグを
    一括設定・解除する。対象になった銘柄数を返す。
    """
    snapshot = get_latest_snapshot(db, user_id)
    if snapshot is None:
        return 0

    security_keys = {
        h.security_key for h in db.query(Holding)
        .filter(Holding.snapshot_id == snapshot.id, Holding.category == category).all()
    }
    for key in security_keys:
        set_security_excluded(db, user_id, key, excluded)
    return len(security_keys)


def bulk_set_category_tag(db: Session, user_id: str, category: str, axis_key: str, value: str) -> int:
    """
    最新スナップショットのうち指定カテゴリに属する銘柄すべてに、指定軸のタグを
    一括設定する（例:「株式」の資金の時間軸を全部「中期」にする）。
    手動での一括操作なのでis_auto=0にし、以後の自動分類・再インポートで
    上書きされないようにする。軸が存在しない場合はNoneを返す。
    """
    snapshot = get_latest_snapshot(db, user_id)
    if snapshot is None:
        return None

    axis = (
        db.query(ClassificationAxis)
        .filter(ClassificationAxis.user_id == user_id, ClassificationAxis.key == axis_key)
        .first()
    )
    if axis is None:
        return None

    security_keys = {
        h.security_key for h in db.query(Holding)
        .filter(Holding.snapshot_id == snapshot.id, Holding.category == category).all()
    }

    existing_tags = {
        t.security_key: t for t in db.query(SecurityTag)
        .filter(SecurityTag.user_id == user_id, SecurityTag.axis_id == axis.id,
                SecurityTag.security_key.in_(security_keys)).all()
    }
    for key in security_keys:
        tag = existing_tags.get(key)
        if tag:
            tag.value = value
            tag.is_auto = 0
        else:
            db.add(SecurityTag(user_id=user_id, security_key=key, axis_id=axis.id, value=value, is_auto=0))
    db.commit()
    return len(security_keys)
