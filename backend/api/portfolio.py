from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..models.user import User
from ..models.portfolio import ClassificationAxis, SecurityTag
from ..services.portfolio_import import import_portfolio_paste, PortfolioParseError
from ..services.portfolio_analysis import compute_breakdown, list_securities_with_tags
from ..services.classification import ensure_builtin_axes, TIME_HORIZON_VALUES
from ..services.wealth_bucket import get_bucket_summary, set_bucket_goal
from .auth import get_current_user

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


class PastePortfolioInput(BaseModel):
    text: str


class CreateAxisInput(BaseModel):
    key: str
    label: str


class SetTagInput(BaseModel):
    axis_key: str
    value: str


class SetBucketGoalInput(BaseModel):
    target_amount_man: int


@router.post("/snapshot")
def post_portfolio_snapshot(
    body: PastePortfolioInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """マネフォ保有資産ページのコピペテキストを取り込み、新規スナップショットとして保存する"""
    try:
        result = import_portfolio_paste(db, current_user.id, body.text)
    except PortfolioParseError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return result


@router.get("/axes")
def get_axes(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """分類軸一覧（標準3軸＋カスタム軸）を返す"""
    axes = ensure_builtin_axes(db, current_user.id)  # 標準3軸を保証
    all_axes = (
        db.query(ClassificationAxis)
        .filter(ClassificationAxis.user_id == current_user.id)
        .order_by(ClassificationAxis.display_order, ClassificationAxis.created_at)
        .all()
    )
    return [
        {"key": a.key, "label": a.label, "is_builtin": bool(a.is_builtin)}
        for a in all_axes
    ]


@router.post("/axes")
def post_axis(
    body: CreateAxisInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """カスタム分類軸を追加する"""
    existing = (
        db.query(ClassificationAxis)
        .filter(ClassificationAxis.user_id == current_user.id, ClassificationAxis.key == body.key)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="同じキーの軸が既に存在します")

    max_order = db.query(ClassificationAxis).filter(
        ClassificationAxis.user_id == current_user.id
    ).count()
    axis = ClassificationAxis(
        user_id=current_user.id, key=body.key, label=body.label,
        is_builtin=0, display_order=max_order,
    )
    db.add(axis)
    db.commit()
    return {"key": axis.key, "label": axis.label, "is_builtin": False}


@router.get("/securities")
def get_securities(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """最新スナップショットの銘柄一覧（全軸のタグ付き）を返す。タグ編集UI用"""
    ensure_builtin_axes(db, current_user.id)
    return list_securities_with_tags(db, current_user.id)


@router.put("/securities/{security_key}/tags")
def put_security_tag(
    security_key: str,
    body: SetTagInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """銘柄に指定軸のタグを設定する（手動修正はis_auto=0になり、以後の自動分類で上書きされない）"""
    axis = (
        db.query(ClassificationAxis)
        .filter(ClassificationAxis.user_id == current_user.id, ClassificationAxis.key == body.axis_key)
        .first()
    )
    if axis is None:
        raise HTTPException(status_code=404, detail="指定された軸が見つかりません")

    tag = (
        db.query(SecurityTag)
        .filter(
            SecurityTag.user_id == current_user.id,
            SecurityTag.security_key == security_key,
            SecurityTag.axis_id == axis.id,
        )
        .first()
    )
    if tag:
        tag.value = body.value
        tag.is_auto = 0
    else:
        tag = SecurityTag(
            user_id=current_user.id, security_key=security_key, axis_id=axis.id,
            value=body.value, is_auto=0,
        )
        db.add(tag)
    db.commit()
    return {"security_key": security_key, "axis_key": body.axis_key, "value": body.value}


@router.get("/breakdown")
def get_breakdown(
    axis: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """指定軸での内訳集計（円グラフ用）を返す"""
    result = compute_breakdown(db, current_user.id, axis)
    if result is None:
        return {
            "has_data": False,
            "message": "保有資産のデータがありません。マネフォの保有資産ページを貼り付けてください。",
        }
    return {
        "has_data": True,
        "snapshot_created_at": result["snapshot_created_at"].isoformat(),
        "total_value_yen": result["total_value_yen"],
        "groups": result["groups"],
    }


@router.get("/buckets")
def get_buckets(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """「3つの財布」（長期・中期・短期）の現在額・目標額・達成率を返す"""
    ensure_builtin_axes(db, current_user.id)
    result = get_bucket_summary(db, current_user.id)
    if result is None:
        return {
            "has_data": False,
            "message": "保有資産のデータがありません。マネフォの保有資産ページを貼り付けてください。",
        }
    return {
        "has_data": True,
        "snapshot_created_at": result["snapshot_created_at"].isoformat(),
        "total_value_yen": result["total_value_yen"],
        "buckets": result["buckets"],
        "unclassified_yen": result["unclassified_yen"],
        "bucket_values": TIME_HORIZON_VALUES,
    }


@router.put("/buckets/{bucket_value}/goal")
def put_bucket_goal(
    bucket_value: str,
    body: SetBucketGoalInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """指定バケット（長期/中期/短期）の目標金額を設定する"""
    try:
        set_bucket_goal(db, current_user.id, bucket_value, body.target_amount_man * 10000)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"bucket_value": bucket_value, "target_amount_man": body.target_amount_man}
