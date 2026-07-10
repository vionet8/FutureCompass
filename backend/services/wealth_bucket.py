"""
「3つの財布」（長期・中期・短期）の現在額・目標額・達成率を管理する。

現在額はtime_horizon軸のタグ集計（portfolio_analysis.compute_breakdown）を再利用する。
目標額はWealthBucketGoalに保存し、未設定のバケットは達成率をNoneで返す。
"""

from sqlalchemy.orm import Session

from ..models.portfolio import WealthBucketGoal
from .classification import TIME_HORIZON_AXIS_KEY, TIME_HORIZON_VALUES
from .portfolio_analysis import compute_breakdown


def get_bucket_summary(db: Session, user_id: str) -> dict | None:
    """
    3つの財布（長期・中期・短期）それぞれの現在額・目標額・達成率を返す。
    保有資産データが無ければNone。未タグの保有分は「未分類」として別枠で返す。
    """
    breakdown = compute_breakdown(db, user_id, TIME_HORIZON_AXIS_KEY)
    if breakdown is None:
        return None

    amounts_by_value = {g["value"]: g["amount_yen"] for g in breakdown["groups"]}
    goals = {
        g.bucket_value: g.target_amount_yen
        for g in db.query(WealthBucketGoal).filter(WealthBucketGoal.user_id == user_id).all()
    }

    buckets = []
    for bucket_value in TIME_HORIZON_VALUES:
        current = amounts_by_value.pop(bucket_value, 0)
        target = goals.get(bucket_value)
        achievement_pct = round(current / target * 100, 1) if target else None
        buckets.append({
            "bucket_value": bucket_value,
            "current_amount_yen": current,
            "target_amount_yen": target,
            "achievement_pct": achievement_pct,
        })

    # 3バケット以外（"未分類"や旧カスタム値）に残った分はまとめて別枠で提示
    unclassified_yen = sum(amounts_by_value.values())

    return {
        "snapshot_created_at": breakdown["snapshot_created_at"],
        "total_value_yen": breakdown["total_value_yen"],
        "buckets": buckets,
        "unclassified_yen": unclassified_yen,
    }


def set_bucket_goal(db: Session, user_id: str, bucket_value: str, target_amount_yen: int) -> None:
    """指定バケットの目標金額を設定・更新する"""
    if bucket_value not in TIME_HORIZON_VALUES:
        raise ValueError(f"不正なバケット値です: {bucket_value}")

    goal = (
        db.query(WealthBucketGoal)
        .filter(WealthBucketGoal.user_id == user_id, WealthBucketGoal.bucket_value == bucket_value)
        .first()
    )
    if goal:
        goal.target_amount_yen = target_amount_yen
    else:
        db.add(WealthBucketGoal(user_id=user_id, bucket_value=bucket_value, target_amount_yen=target_amount_yen))
    db.commit()
