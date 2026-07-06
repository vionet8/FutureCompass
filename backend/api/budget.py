from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from ..core.database import get_db
from ..models.budget import BudgetCategory, BudgetEntry, EntryStatus
from ..models.user import User
from ..services.budget_reconcile import reconcile_mf_csv, calc_budget_summary
from .auth import get_current_user

router = APIRouter(prefix="/budget", tags=["budget"])


# ── カテゴリ ──────────────────────────────────

class CategoryCreate(BaseModel):
    name: str
    icon: str = "💴"
    monthly_budget: int
    color: str = "#6c63ff"
    sort_order: int = 0


@router.get("/categories")
def list_categories(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cats = db.query(BudgetCategory).filter(
        BudgetCategory.user_id == current_user.id
    ).order_by(BudgetCategory.sort_order).all()
    return [
        {
            "id": c.id, "name": c.name, "icon": c.icon,
            "monthly_budget": c.monthly_budget, "color": c.color,
        }
        for c in cats
    ]


@router.post("/categories")
def create_category(
    req: CategoryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cat = BudgetCategory(user_id=current_user.id, **req.dict())
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return {"id": cat.id, "message": "カテゴリを作成しました"}


@router.delete("/categories/{category_id}")
def delete_category(
    category_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cat = db.query(BudgetCategory).filter(
        BudgetCategory.id == category_id,
        BudgetCategory.user_id == current_user.id,
    ).first()
    if not cat:
        raise HTTPException(status_code=404, detail="カテゴリが見つかりません")
    db.delete(cat)
    db.commit()
    return {"message": "削除しました"}


# ── エントリ（仮計上・確定） ─────────────────

class EntryCreate(BaseModel):
    category_id: str
    description: str
    amount: int
    entry_date: str           # "2026-06-22"
    status: str = "provisional"


@router.post("/entries")
def create_entry(
    req: EntryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cat = db.query(BudgetCategory).filter(
        BudgetCategory.id == req.category_id,
        BudgetCategory.user_id == current_user.id,
    ).first()
    if not cat:
        raise HTTPException(status_code=404, detail="カテゴリが見つかりません")

    entry = BudgetEntry(
        category_id=req.category_id,
        user_id=current_user.id,
        description=req.description,
        amount=req.amount,
        status=req.status,
        entry_date=datetime.strptime(req.entry_date, "%Y-%m-%d"),
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return {"id": entry.id, "message": "仮計上しました"}


@router.delete("/entries/{entry_id}")
def delete_entry(
    entry_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    entry = db.query(BudgetEntry).filter(
        BudgetEntry.id == entry_id,
        BudgetEntry.user_id == current_user.id,
    ).first()
    if not entry:
        raise HTTPException(status_code=404, detail="エントリが見つかりません")
    db.delete(entry)
    db.commit()
    return {"message": "削除しました"}


# ── 月次サマリー ──────────────────────────────

@router.get("/summary")
def get_summary(
    year_month: str = Query(..., example="2026-06"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    summaries = calc_budget_summary(db, current_user.id, year_month)
    total_budget = sum(s["monthly_budget"] for s in summaries)
    total_spent = sum(s["total_spent"] for s in summaries)
    total_confirmed = sum(s["confirmed_total"] for s in summaries)
    total_provisional = sum(s["provisional_total"] for s in summaries)
    return {
        "year_month": year_month,
        "total_budget": total_budget,
        "total_spent": total_spent,
        "total_confirmed": total_confirmed,
        "total_provisional": total_provisional,
        "total_remaining": total_budget - total_spent,
        "categories": summaries,
    }


# ── MF CSV 照合 ───────────────────────────────

@router.post("/reconcile/{category_id}")
async def reconcile(
    category_id: str,
    year_month: str = Query(..., example="2026-06"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cat = db.query(BudgetCategory).filter(
        BudgetCategory.id == category_id,
        BudgetCategory.user_id == current_user.id,
    ).first()
    if not cat:
        raise HTTPException(status_code=404, detail="カテゴリが見つかりません")

    content = await file.read()
    result = reconcile_mf_csv(db, current_user.id, category_id, content, year_month)
    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])
    return result
