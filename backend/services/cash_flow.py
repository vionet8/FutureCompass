"""入出金イベント（手動入力）のCRUD"""

from datetime import date
from typing import Optional
from sqlalchemy.orm import Session

from ..models.performance import CashFlowEvent


def add_cash_flow(
    db: Session, user_id: str, flow_date: date, amount_yen: int, flow_type: str,
    memo: Optional[str] = None,
) -> CashFlowEvent:
    event = CashFlowEvent(
        user_id=user_id, flow_date=flow_date, amount_yen=amount_yen,
        flow_type=flow_type, memo=memo, source="manual",
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def list_cash_flows(db: Session, user_id: str) -> list[CashFlowEvent]:
    return (
        db.query(CashFlowEvent)
        .filter(CashFlowEvent.user_id == user_id)
        .order_by(CashFlowEvent.flow_date.desc())
        .all()
    )


def delete_cash_flow(db: Session, user_id: str, flow_id: str) -> bool:
    event = (
        db.query(CashFlowEvent)
        .filter(CashFlowEvent.id == flow_id, CashFlowEvent.user_id == user_id)
        .first()
    )
    if not event:
        return False
    db.delete(event)
    db.commit()
    return True
