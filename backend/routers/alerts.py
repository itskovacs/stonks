"""
Alerts Router
=============
User price alerts: create, list, update, delete.

Alerts are evaluated by the background scheduler (services/scheduler.py)
every 15 minutes. A triggered alert is disarmed until the price retreats
past the threshold, at which point it re-arms automatically.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select

from deps import SessionDep, get_current_username
from models.models import Alert
from models.schemas import AlertOut, AlertRequest, AlertUpdateRequest

router = APIRouter(prefix="/profile/alerts", tags=["Alerts"])


@router.get(
    "",
    response_model=list[AlertOut],
    summary="List all price alerts for the current user",
)
def list_alerts(
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
) -> list[Alert]:
    return session.exec(
        select(Alert)
        .where(Alert.user == current_user)
        .order_by(Alert.id)
    ).all()


@router.post(
    "",
    response_model=AlertOut,
    status_code=201,
    summary="Create a price alert for a ticker",
)
def create_alert(
    req: AlertRequest,
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
) -> Alert:
    alert = Alert(
        user=current_user,
        ticker=req.ticker,
        target_price=req.target_price,
        trigger_above=req.trigger_above,
        notes=req.notes,
        actionable=req.actionable,
    )
    session.add(alert)
    session.commit()
    session.refresh(alert)
    return alert


@router.put(
    "/{alert_id}",
    response_model=AlertOut,
    summary="Update target price or trigger direction",
)
def update_alert(
    alert_id: int,
    req: AlertUpdateRequest,
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
) -> Alert:
    alert = session.exec(
        select(Alert).where(Alert.id == alert_id, Alert.user == current_user)
    ).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found.")

    if req.target_price is not None:
        alert.target_price = req.target_price
    if req.trigger_above is not None:
        alert.trigger_above = req.trigger_above
    if req.notes is not None:
        alert.notes = req.notes.strip() or None
    if req.actionable is not None:
        alert.actionable = req.actionable

    # Only reset armed state when the trigger condition itself changes.
    if req.target_price is not None or req.trigger_above is not None:
        alert.is_armed = True
        alert.last_triggered = None

    session.add(alert)
    session.commit()
    session.refresh(alert)
    return alert


@router.delete("/{alert_id}", summary="Delete a price alert")
def delete_alert(
    alert_id: int,
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
):
    alert = session.exec(
        select(Alert).where(Alert.id == alert_id, Alert.user == current_user)
    ).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found.")
    session.delete(alert)
    session.commit()
    return {"status": "success", "message": "Alert deleted"}
