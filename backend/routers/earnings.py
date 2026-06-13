"""
Earnings Calendar router
========================
GET /api/earnings  →  EarningsCalendarResponse for ±1 week from today
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from deps import get_current_username
from models.schemas import EarningsCalendarResponse
from services.earnings_service import get_earnings_calendar

router = APIRouter(prefix="/earnings", tags=["Earnings"])


@router.get("", summary="Earnings calendar for ±1 week from today", response_model=EarningsCalendarResponse)
async def earnings_calendar(current_user: Annotated[str, Depends(get_current_username)]) -> dict:
    return await run_in_threadpool(get_earnings_calendar)
