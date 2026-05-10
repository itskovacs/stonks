"""
Profile Router
==============
User watchlist, envelopes, transactions, dashboard, and portfolio overview.

Transaction accounting
----------------------
Type       Cash effect              Shares
DEPOSIT    +total                   none
WITHDRAW   -total                   none
BUY        -total (price×shares+fees) +shares
SELL       +total (price×shares-fees) -shares
DIVIDEND   +total                   none

cash_available on Envelope is the authoritative running balance.
It is updated atomically in the same session.commit() as the Transaction insert.
The dashboard reads it directly — no ledger replay needed.

envelope_name is NOT a column on Transaction.
It is resolved via the loaded envelope relationship and injected into response dicts.
Renaming an envelope requires only a single-row Envelope UPDATE — no transaction cascade.

allocation_pct is expressed as % of TOTAL portfolio (equity + cash),
not equity alone. This reflects realistic capital deployment and is
more informative when a portfolio is heavily cash-weighted.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import selectinload
from sqlmodel import select

from deps import SessionDep, get_current_username
from models.models import Envelope, Transaction, User, WatchlistItem
from models.schemas import (
    DashboardResponse,
    EnvelopeOverviewResponse,
    EnvelopeRequest,
    TickerRequest,
    TickerSearchResult,
    TransactionOut,
    TransactionRequest,
    UserSettingsOut,
    UserSettingsRequest,
    WatchlistRow,
)
from services.fetcher import YFinanceFetcher
from services.http import get_trending
from services.portfolio_engine import compute_all_positions, compute_envelope_overview, compute_sell_pnl
from services.search_service import search_tickers

router = APIRouter(prefix="/profile", tags=["Profile"])

_DEPOSIT_PERIODS: dict[str, int] = {"30d": 30, "90d": 90, "180d": 180, "1y": 365, "5y": 1825}
_SEMAPHORE = asyncio.Semaphore(5)

# ─────────────────────────────────────────────────────────────────────────────
# User settings
# ─────────────────────────────────────────────────────────────────────────────

async def _semaphore_threadpool(t: str):
    async with _SEMAPHORE:
        return await run_in_threadpool(_fetch_ticker_snapshot, t)

@router.get("/settings", response_model=UserSettingsOut, summary="Get current user settings")
def get_settings(
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
) -> User:
    user = session.get(User, current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return user


@router.put("/settings", response_model=UserSettingsOut, summary="Update currency and/or Apprise notification URL")
def update_settings(
    req: UserSettingsRequest,
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
) -> User:
    user = session.get(User, current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    if req.currency is not None:
        user.currency = req.currency
    if req.apprise_url is not None:
        user.apprise_url = req.apprise_url or None  # empty string → None
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _fmt(n: float) -> str:
    """Serialize a number without trailing zeros, up to 6 decimal places."""
    return f"{n:.6f}".rstrip("0").rstrip(".")


def _tx_out(tx: Transaction, sell_pnl_map: dict[int, dict] | None = None) -> TransactionOut:
    """Builds a TransactionOut from a loaded ORM Transaction (envelope eager-loaded)."""
    pnl = (sell_pnl_map or {}).get(tx.id, {})
    return TransactionOut(
        id=tx.id,
        user=tx.user,
        date=tx.date,
        type=tx.type,
        ticker=tx.ticker,
        shares=tx.shares,
        price=tx.price,
        fees=tx.fees,
        total=tx.total,
        note=tx.note,
        envelope_name=tx.envelope.name if tx.envelope else "",
        realized_pnl=pnl.get("realized_pnl"),
        realized_pnl_pct=pnl.get("realized_pnl_pct"),
    )


def _tx_dict(tx: Transaction) -> dict:
    """Flat dict of a transaction used by portfolio_engine (includes envelope_name)."""
    return {
        "id":            tx.id,
        "user":          tx.user,
        "date":          tx.date.isoformat(),
        "type":          str(tx.type),
        "ticker":        tx.ticker,
        "shares":        tx.shares,
        "price":         tx.price,
        "fees":          tx.fees,
        "total":         tx.total,
        "note":          tx.note,
        "envelope_name": tx.envelope.name if tx.envelope else "",
    }


def _fetch_ticker_snapshot(ticker: str) -> dict:
    """Lightweight price fetch for dashboard rows. Runs in a threadpool."""
    f = YFinanceFetcher(ticker)
    info = f.info()
    current = f.get_float("currentPrice") or f.get_float("regularMarketPrice") or 0.0
    prev    = f.get_float("previousClose") or f.get_float("regularMarketPreviousClose") or 0.0
    change_1d     = round(current - prev, 4) if current and prev else 0.0
    change_1d_pct = round(change_1d / prev * 100, 2) if prev else 0.0

    history_7d = []
    hist = f.history(period="8d", interval="1d")
    if not hist.empty and "Close" in hist.columns:
        for dt, row in hist.tail(7).iterrows():
            history_7d.append({"date": str(dt.date()), "close": round(float(row["Close"]), 4)})

    pre_price = info.get("preMarketPrice")
    pre_price = float(pre_price) if pre_price is not None else None
    pre_chg   = round(pre_price - prev, 4) if (pre_price and prev) else None
    pre_pct   = round(pre_chg / prev * 100, 2) if (pre_chg is not None and prev) else None

    return {
        "ticker":                ticker,
        "name":                  info.get("shortName") or info.get("longName") or ticker,
        "current_price":         round(current, 4),
        "prev_close":            round(prev, 4),
        "change_1d":             change_1d,
        "change_1d_pct":         change_1d_pct,
        "sector":                info.get("sector"),
        "currency":              info.get("currency", "USD"),
        "history_7d":            history_7d,
        "pre_market_price":      pre_price,
        "pre_market_change":     pre_chg,
        "pre_market_change_pct": pre_pct,
    }


def _fallback_snapshot(ticker: str) -> dict:
    return {
        "ticker": ticker, "name": ticker, "current_price": 0.0,
        "prev_close": 0.0, "change_1d": 0.0, "change_1d_pct": 0.0,
        "sector": None, "currency": "USD", "history_7d": [],
        "pre_market_price": None, "pre_market_change": None, "pre_market_change_pct": None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/dashboard",
    summary="Live dashboard: watchlist + positions + envelope totals",
    response_model=DashboardResponse,
)
async def get_dashboard(
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
):
    # ── 1. Load from DB ───────────────────────────────────────────────────────
    watchlist_items = session.exec(
        select(WatchlistItem)
        .where(WatchlistItem.user == current_user)
        .order_by(WatchlistItem.added_at)
    ).all()
    watchlist_tickers = [item.ticker for item in watchlist_items]

    envelopes = session.exec(
        select(Envelope).where(Envelope.user == current_user)
    ).all()

    txs_orm = session.exec(
        select(Transaction)
        .where(Transaction.user == current_user)
        .order_by(Transaction.date.desc())
        .options(selectinload(Transaction.envelope))
    ).all()

    txs_dict = [_tx_dict(tx) for tx in txs_orm]

    # ── 2. Compute WAC positions + realized PnL in a single pass ─────────────
    envelopes_meta = [{"name": e.name, "color": e.color} for e in envelopes]
    raw_positions  = compute_all_positions(txs_dict, envelopes_meta)
    sell_pnl_map   = compute_sell_pnl(txs_dict)
    txs_out        = [_tx_out(tx, sell_pnl_map) for tx in txs_orm]

    # ── 3. Fetch live prices concurrently ─────────────────────────────────────
    unique_tickers = list(dict.fromkeys(
        watchlist_tickers + [p["ticker"] for p in raw_positions]
    ))
    snapshots = await asyncio.gather(
        *[_semaphore_threadpool(t) for t in unique_tickers],
        return_exceptions=True,
    )
    price_map: dict[str, dict] = {
        ticker: (snap if not isinstance(snap, Exception) else _fallback_snapshot(ticker))
        for ticker, snap in zip(unique_tickers, snapshots, strict=True)
    }

    # ── 4. Enrich positions ───────────────────────────────────────────────────
    total_cash = sum(e.cash_available for e in envelopes)
    total_equity = sum(
        pos["shares"] * price_map.get(pos["ticker"], _fallback_snapshot(pos["ticker"]))["current_price"]
        for pos in raw_positions
    )
    total_portfolio = total_equity + total_cash
    # allocation_pct = % of total portfolio (equity + cash)
    alloc_base = total_portfolio if total_portfolio > 0 else 1.0

    env_market_values: dict[str, float] = {e.name: 0.0 for e in envelopes}
    enriched_positions = []

    for pos in raw_positions:
        snap = price_map.get(pos["ticker"], _fallback_snapshot(pos["ticker"]))
        cp   = snap["current_price"]
        prev = snap["prev_close"]
        shares     = pos["shares"]
        cost_basis = pos["cost_basis"]
        cur_value  = round(shares * cp, 2)
        pnl        = round(cur_value - cost_basis, 2)
        env_market_values[pos["envelope_name"]] = env_market_values.get(pos["envelope_name"], 0.0) + cur_value

        enriched_positions.append({
            "ticker":             pos["ticker"],
            "name":               snap.get("name") or None,
            "envelope_name":      pos["envelope_name"],
            "currency":           snap.get("currency", "USD"),
            "shares":             shares,
            "avg_cost":           pos["avg_cost"],
            "cost_basis":         cost_basis,
            "current_price":      cp,
            "current_value":      cur_value,
            "unrealized_pnl":     pnl,
            "unrealized_pnl_pct": round(pnl / cost_basis * 100, 2) if cost_basis else 0.0,
            "allocation_pct":     round(cur_value / alloc_base * 100, 2),
            "change_1d":          round(shares * (cp - prev), 2) if prev else 0.0,
            "change_1d_pct":      snap["change_1d_pct"],
        })

    # ── 5. Envelope summaries ─────────────────────────────────────────────────
    env_capital_in: dict[str, float] = {e.name: 0.0 for e in envelopes}
    for tx in txs_dict:
        if tx["type"] in ("DEPOSIT", "DIVIDEND"):
            env_capital_in[tx["envelope_name"]] = env_capital_in.get(tx["envelope_name"], 0.0) + tx["total"]
        elif tx["type"] == "WITHDRAW":
            env_capital_in[tx["envelope_name"]] = env_capital_in.get(tx["envelope_name"], 0.0) - tx["total"]

    enriched_envelopes = [
        {
            "id":             e.id,
            "name":           e.name,
            "color":          e.color,
            "cash_available": round(e.cash_available, 2),
            "total_value":    round(e.cash_available + env_market_values.get(e.name, 0.0), 2),
            "capital_in":     round(env_capital_in.get(e.name, 0.0), 2),
        }
        for e in envelopes
    ]

    # ── 6. Totals ─────────────────────────────────────────────────────────────
    total_cost_basis      = sum(p["cost_basis"] for p in enriched_positions)
    all_time_net_deposits = sum(env_capital_in.values())
    total_pnl             = round(total_portfolio - all_time_net_deposits, 2)
    total_change_1d       = round(sum(p["change_1d"] for p in enriched_positions), 2)
    yesterday_portfolio   = total_portfolio - total_change_1d

    cash_txs = [
        (tx["date"][:10], tx["total"] if tx["type"] == "DEPOSIT" else -tx["total"])
        for tx in txs_dict
        if tx["type"] in ("DEPOSIT", "WITHDRAW")
    ]
    if not cash_txs:
        net_deposits: dict[str, float] = {k: 0.0 for k in _DEPOSIT_PERIODS}
    else:
        today = datetime.now(timezone.utc).date()
        cutoffs = {k: str(today - timedelta(days=d)) for k, d in _DEPOSIT_PERIODS.items()}
        net_deposits = {
            k: round(sum(amt for date, amt in cash_txs if date >= cutoff), 2)
            for k, cutoff in cutoffs.items()
        }

    cutoff_90d = str(datetime.now(timezone.utc).date() - timedelta(days=90))
    dividend_income_90d = round(
        sum(tx["total"] for tx in txs_dict if tx["type"] == "DIVIDEND" and tx["date"][:10] >= cutoff_90d),
        2,
    )

    user_obj = session.get(User, current_user)
    user_currency = (user_obj.currency if user_obj else None) or "€"

    return {
        "watchlist":     [price_map[t] for t in watchlist_tickers if t in price_map],
        "positions":     enriched_positions,
        "envelopes":     enriched_envelopes,
        "transactions":  txs_out,
        "user_currency": user_currency,
        "totals": {
            "total_value":         round(total_portfolio, 2),
            "total_cash":          round(total_cash, 2),
            "total_cost_basis":    round(total_cost_basis, 2),
            "total_pnl":           total_pnl,
            "total_pnl_pct":       round(total_pnl / all_time_net_deposits * 100, 2) if all_time_net_deposits > 0 else 0.0,
            "total_change_1d":     total_change_1d,
            "total_change_1d_pct": round(total_change_1d / yesterday_portfolio * 100, 2) if yesterday_portfolio else 0.0,
            "net_deposits":        net_deposits,
            "dividend_income_90d": dividend_income_90d,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Transaction export
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/transactions/export",
    summary="Export transactions in bulk-paste format",
    response_model=list[str],
)
def export_transactions(
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
    envelope_name: str | None = Query(default=None),
):
    txs = session.exec(
        select(Transaction)
        .where(Transaction.user == current_user)
        .order_by(Transaction.date.asc())
        .options(selectinload(Transaction.envelope))
    ).all()

    lines: list[str] = []
    for tx in txs:
        env = tx.envelope.name if tx.envelope else ""
        if envelope_name and env != envelope_name:
            continue
        date_fmt = tx.date.strftime("%d/%m/%Y") if tx.date else "01/01/1970"
        env_q    = f'"{env}"'
        tx_type  = str(tx.type)

        if tx_type in ("BUY", "SELL"):
            lines.append(
                f"{tx_type} {env_q} {date_fmt} {tx.ticker} {_fmt(tx.shares)} {_fmt(tx.price)} {_fmt(tx.fees)}"
            )
        elif tx_type == "DIVIDEND" and tx.ticker and tx.shares > 0:
            lines.append(
                f"DIVIDEND {env_q} {date_fmt} {tx.ticker} {_fmt(tx.shares)} {_fmt(tx.price)}"
            )
        else:
            lines.append(f"{tx_type} {env_q} {date_fmt} {_fmt(tx.price)}")

    return lines


# ─────────────────────────────────────────────────────────────────────────────
# Portfolio overview
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/envelope/overview",
    summary="Portfolio history chart + stats",
    response_model=EnvelopeOverviewResponse,
)
async def get_portfolio_overview(
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
    period: str = Query("1y", pattern=r"^(1w|1mo|3mo|6mo|ytd|1y|3y)$"),
):
    txs_orm = session.exec(
        select(Transaction)
        .where(Transaction.user == current_user)
        .order_by(Transaction.date.asc())
        .options(selectinload(Transaction.envelope))
    ).all()
    envelopes = session.exec(
        select(Envelope).where(Envelope.user == current_user)
    ).all()

    txs_dict = [_tx_dict(tx) for tx in txs_orm]
    envelopes_meta = [{"name": e.name, "color": e.color} for e in envelopes]

    return await run_in_threadpool(compute_envelope_overview, txs_dict, envelopes_meta, period)


# ─────────────────────────────────────────────────────────────────────────────
# Watchlist
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/watchlist/trending",
    summary="Return trending tickers using Yahoo trending HTTP endpoint",
    response_model=list[WatchlistRow],
)
async def get_trending_tickers(current_user: Annotated[str, Depends(get_current_username)]) -> list[dict]:
    """
    Retrieve trending tickers, return enriched tickers.

    Each result includes:
      - ticker    : the exchange symbol (e.g. AAPL, MSFT)
      - name      : company or fund name
      - value     : current market price
      - change_1d_pct : daily percentage change
      - currency  : price currency
      - exchange  : listing exchange (e.g. NMS, NYQ)
      - quote_type: instrument category (EQUITY, ETF, MUTUALFUND, INDEX)
    """

    trending = get_trending()
    snapshots = await asyncio.gather(
        *[_semaphore_threadpool(t) for t in trending],
        return_exceptions=True,
    )
    return [s for s in snapshots if not isinstance(s, Exception)]


@router.get(
    "/watchlist/search",
    summary="Search for tickers by name or symbol",
    response_model=list[TickerSearchResult],
)
async def search_watchlist_tickers(
    current_user: Annotated[str, Depends(get_current_username)],
    q: str = Query(..., min_length=1, max_length=100, description="Search query"),
    limit: int = Query(8, ge=1, le=20, description="Maximum results to return"),
) -> list[dict]:
    """
    Searches for tickers matching the query string and returns enriched results.

    Each result includes:
      - ticker    : the exchange symbol (e.g. AAPL, MSFT)
      - name      : company or fund name
      - value     : current market price
      - change_1d_pct : daily percentage change
      - currency  : price currency
      - exchange  : listing exchange (e.g. NMS, NYQ)
      - quote_type: instrument category (EQUITY, ETF, MUTUALFUND, INDEX)

    The search uses yfinance's Search API for candidate discovery, then
    enriches with live price data. Results with no usable price are excluded.
    Results respect yfinance's relevance ranking.
    """
    return await run_in_threadpool(search_tickers, q.strip(), limit)


@router.post("/watchlist/add", summary="Add a ticker to the watchlist")
async def add_ticker_to_watchlist(
    req: TickerRequest,
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
):
    ticker = req.ticker
    if not session.exec(
        select(WatchlistItem).where(
            WatchlistItem.user == current_user, WatchlistItem.ticker == ticker
        )
    ).first():
        session.add(WatchlistItem(user=current_user, ticker=ticker))
        session.commit()

    snapshot = await run_in_threadpool(_fetch_ticker_snapshot, ticker)
    return {"status": "success", "ticker": snapshot}

@router.post("/watchlist/remove", summary="Remove a ticker from the watchlist")
def remove_ticker_from_watchlist(
    req: TickerRequest,
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
):
    item = session.exec(
        select(WatchlistItem).where(
            WatchlistItem.user == current_user, WatchlistItem.ticker == req.ticker
        )
    ).first()
    if item:
        session.delete(item)
        session.commit()
    return {
        "status": "success",
        "watchlist": [
            i.ticker
            for i in session.exec(
                select(WatchlistItem)
                .where(WatchlistItem.user == current_user)
                .order_by(WatchlistItem.added_at)
            ).all()
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Envelopes
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/envelopes/add", summary="Create a new envelope")
def add_envelope(
    req: EnvelopeRequest,
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
):
    if session.exec(
        select(Envelope).where(Envelope.user == current_user, Envelope.name == req.name)
    ).first():
        raise HTTPException(status_code=409, detail="An envelope with this name already exists.")
    session.add(Envelope(
        user=current_user, name=req.name,
        color=req.color or "#fafafa", cash_available=0.0,
    ))
    session.commit()
    return {"status": "success", "message": "Envelope created"}


@router.put("/envelopes/{id}", summary="Rename or recolor an envelope")
def update_envelope(
    id: int,
    req: EnvelopeRequest,
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
):
    """
    Renames or recolors an envelope.
    Because Transaction stores only envelope_id (not a denormalized name),
    renaming the envelope is a single-row UPDATE — no cascade needed.
    All transactions automatically reflect the new name via the FK join.
    """
    envelope = session.exec(
        select(Envelope).where(Envelope.user == current_user, Envelope.id == id)
    ).first()
    if not envelope:
        raise HTTPException(status_code=404, detail="Envelope not found.")

    if req.name != envelope.name:
        if session.exec(
            select(Envelope).where(Envelope.user == current_user, Envelope.name == req.name)
        ).first():
            raise HTTPException(status_code=409, detail="An envelope with this name already exists.")
        envelope.name = req.name

    if req.color:
        envelope.color = req.color

    session.add(envelope)
    session.commit()
    return {"status": "success", "message": "Envelope updated"}


@router.delete("/envelopes/{id}", summary="Delete an envelope and its transactions")
def remove_envelope(
    id: int,
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
):
    envelope = session.exec(
        select(Envelope).where(Envelope.user == current_user, Envelope.id == id)
    ).first()
    if not envelope:
        raise HTTPException(status_code=404, detail="Envelope not found.")
    session.delete(envelope)
    session.commit()
    return {"status": "success", "message": "Envelope deleted"}


# ─────────────────────────────────────────────────────────────────────────────
# Transactions
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/transactions", summary="Log a transaction")
def add_transaction(
    req: TransactionRequest,
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
):
    """
    Total computation:
      DEPOSIT / WITHDRAW : total = abs(price)
      DIVIDEND (shares=0): total = abs(price)    — flat cash amount
      DIVIDEND (shares>0): total = shares × price — dividend-per-share × position size
      BUY                : total = (shares × price) + fees
      SELL               : total = (shares × price) - fees
                           Validated: fees must not exceed gross proceeds.
    """
    envelope = session.exec(
        select(Envelope).where(
            Envelope.user == current_user, Envelope.name == req.envelope_name
        )
    ).first()
    if not envelope:
        raise HTTPException(status_code=400, detail="Envelope does not exist.")

    tx_type = req.type
    shares  = req.shares or 0.0
    price   = req.price

    if tx_type in ("DEPOSIT", "WITHDRAW"):
        total = abs(price)
    elif tx_type == "DIVIDEND":
        total = (shares * price) if shares > 0 else abs(price)
    elif tx_type == "BUY":
        total = (shares * price) + req.fees
    else:  # SELL
        gross = shares * price
        if req.fees > gross:
            raise HTTPException(
                status_code=422,
                detail=f"Fees ({req.fees}) exceed gross proceeds ({gross:.2f}).",
            )
        total = gross - req.fees

    tx = Transaction(
        user=current_user,
        date=req.date,
        type=tx_type,
        ticker=req.ticker,
        shares=shares,
        price=price,
        fees=req.fees,
        total=total,
        envelope_id=envelope.id,
        note=req.note,
    )
    session.add(tx)

    # Update running cash balance atomically
    if tx_type in ("SELL", "DEPOSIT", "DIVIDEND"):
        envelope.cash_available = round(envelope.cash_available + total, 10)
    else:
        envelope.cash_available = round(envelope.cash_available - total, 10)

    session.add(envelope)
    session.commit()
    session.refresh(tx)

    # Load the envelope relationship so _tx_out can read it
    session.refresh(envelope)
    tx.envelope = envelope

    return {
        "status":      "success",
        "message":     f"{tx_type} logged to '{req.envelope_name}'",
        "transaction": _tx_out(tx).model_dump(mode="json"),
    }


@router.delete("/transactions/{id}", summary="Remove a transaction and reverse its cash effect")
def delete_transaction(
    id: int,
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
):
    db_tx = session.get(Transaction, id)
    if not db_tx or db_tx.user != current_user:
        raise HTTPException(status_code=404, detail="Transaction not found.")

    envelope = session.get(Envelope, db_tx.envelope_id)
    if envelope:
        if db_tx.type in ("SELL", "DEPOSIT", "DIVIDEND"):
            envelope.cash_available = round(envelope.cash_available - db_tx.total, 10)
        else:
            envelope.cash_available = round(envelope.cash_available + db_tx.total, 10)
        session.add(envelope)

    session.delete(db_tx)
    session.commit()
    return {}
