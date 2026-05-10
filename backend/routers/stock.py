"""
Stock router
============
GET /api/stock/report/{ticker}           → full ticker page payload
GET /api/stock/chart/{ticker}?period=1y  → price chart only (period-switchable)
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import selectinload
from sqlmodel import select
from starlette.concurrency import run_in_threadpool

from deps import SessionDep, get_current_username
from models.models import Transaction, WatchlistItem
from models.schemas import PriceChartResponse, StockReport
from services.fetcher import clear_ticker_cache
from services.portfolio_engine import compute_ticker_wac
from services.report_builder import build_price_chart_for_period, build_report

router = APIRouter(prefix="/stock", tags=["Stock"])

_CHART_PERIODS: dict[str, tuple[str, str]] = {
    "1d":  ("1d",   "5m"),
    "1w":  ("5d",   "15m"),
    "1m":  ("1mo",  "1d"),
    "3m":  ("3mo",  "1d"),
    "6m":  ("6mo",  "1d"),
    "ytd": ("ytd",  "1d"),
    "1y":  ("1y",   "1d"),
    "5y":  ("5y",   "1wk"),
}
_VALID_PERIODS = "|".join(_CHART_PERIODS.keys())


@router.get(
    "/report/{ticker}",
    summary="Full ticker analysis report",
    response_model=StockReport,
)
async def get_stock_report(
    ticker: str,
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
) -> dict:
    t = ticker.strip().upper()
    if not t:
        raise HTTPException(status_code=422, detail="Ticker cannot be empty.")

    txs_orm = session.exec(
        select(Transaction)
        .where(Transaction.user == current_user, Transaction.ticker == t)
        .order_by(Transaction.date.desc())
        .options(selectinload(Transaction.envelope))
    ).all()

    user_positions = [
        {
            "id":             tx.id,
            "date":           tx.date.isoformat(),
            "type":           str(tx.type),
            "shares":         tx.shares,
            "price":          tx.price,
            "fees":           tx.fees,
            "total":          tx.total,
            "envelope_name":  tx.envelope.name  if tx.envelope else "",
            "envelope_color": tx.envelope.color if tx.envelope else "#e2e8f0",
        }
        for tx in txs_orm
    ]

    wac_by_envelope = compute_ticker_wac(user_positions)

    in_watchlist = session.exec(
        select(WatchlistItem).where(
            WatchlistItem.user == current_user,
            WatchlistItem.ticker == t,
        )
    ).first() is not None

    report = await run_in_threadpool(build_report, t, user_positions)
    report["in_watchlist"]    = in_watchlist
    report["wac_by_envelope"] = wac_by_envelope
    return report


@router.delete(
    "/cache/{ticker}",
    summary="Invalidate dynamic cached data for a ticker",
    status_code=204,
)
async def invalidate_ticker_cache(
    ticker: str,
    current_user: Annotated[str, Depends(get_current_username)],
) -> None:
    t = ticker.strip().upper()
    if not t:
        raise HTTPException(status_code=422, detail="Ticker cannot be empty.")
    await run_in_threadpool(clear_ticker_cache, t)


@router.get(
    "/chart/{ticker}",
    summary="Price chart for a given period",
    response_model=PriceChartResponse,
)
async def get_price_chart(
    ticker: str,
    current_user: Annotated[str, Depends(get_current_username)],
    period: str = Query("1y", pattern=rf"^({_VALID_PERIODS})$"),
) -> dict:
    t = ticker.strip().upper()
    if not t:
        raise HTTPException(status_code=422, detail="Ticker cannot be empty.")
    yf_period, yf_interval = _CHART_PERIODS[period]

    chart = await run_in_threadpool(build_price_chart_for_period, t, yf_period, yf_interval)
    return {
        "ticker": ticker,
        "period": period,
        "interval": yf_interval,
        "prices": chart.get('prices', []),
        "annotations": chart.get('annotations', []),
    }
