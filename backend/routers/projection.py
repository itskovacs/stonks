"""
Projection router
=================
POST /api/projection  →  25-year compound-interest projection (Chart.js-ready)

The initial balance is derived from the authenticated user's live portfolio:
  total_cash   = SUM(envelope.cash_available)
  total_equity = SUM(position.shares × current_price)
  initial_balance = total_cash + total_equity
"""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import selectinload
from sqlmodel import select

from deps import SessionDep, get_current_username
from models.models import Envelope, Transaction
from models.schemas import ProjectionRequest, ProjectionResponse
from services.fetcher import YFinanceFetcher
from services.portfolio_engine import compute_all_positions
from services.projection_engine import build_projection

router = APIRouter(prefix="/projection", tags=["Projection"])


def _current_price(ticker: str) -> float:
    f = YFinanceFetcher(ticker)
    return f.get_float("currentPrice") or f.get_float("regularMarketPrice") or 0.0


@router.post(
    "",
    response_model=ProjectionResponse,
    summary="25-year portfolio projection",
)
async def project_portfolio(
    body: ProjectionRequest,
    session: SessionDep,
    username: Annotated[str, Depends(get_current_username)],
) -> dict:
    # 1. Cash balance across all envelopes
    envelopes = session.exec(
        select(Envelope).where(Envelope.user == username)
    ).all()
    total_cash = sum(e.cash_available for e in envelopes)

    # 2. WAC positions from transaction ledger
    txs_orm = session.exec(
        select(Transaction)
        .where(Transaction.user == username)
        .options(selectinload(Transaction.envelope))
    ).all()
    txs_dict = [
        {
            "type":          str(tx.type),
            "ticker":        tx.ticker,
            "shares":        tx.shares,
            "total":         tx.total,
            "date":          tx.date.isoformat(),
            "envelope_name": tx.envelope.name if tx.envelope else "",
        }
        for tx in txs_orm
    ]
    envelopes_meta = [{"name": e.name, "color": e.color} for e in envelopes]
    positions = compute_all_positions(txs_dict, envelopes_meta)

    # 3. Fetch live prices concurrently (TTL-cached by YFinanceFetcher)
    unique_tickers = list(dict.fromkeys(p["ticker"] for p in positions))
    prices = await asyncio.gather(
        *[run_in_threadpool(_current_price, t) for t in unique_tickers]
    )
    price_map = dict(zip(unique_tickers, prices, strict=True))
    total_equity      = sum(p["shares"] * price_map.get(p["ticker"], 0.0) for p in positions)
    total_cost_basis  = sum(p["cost_basis"] for p in positions)

    initial_balance  = round(total_cash + total_equity, 2)
    initial_invested = round(total_cash + total_cost_basis, 2)

    if body.initial_balance is not None:
        initial_balance  = round(body.initial_balance, 2)
        initial_invested = round(body.initial_balance, 2)

    return build_projection(
        initial_balance  = initial_balance,
        initial_invested = initial_invested,
        deposit          = body.deposit,
        annual_rate_pct  = body.annual_rate_pct,
        frequency        = body.deposit_frequency,
    )
