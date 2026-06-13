import logging
import time
from datetime import UTC, date, datetime, timedelta

import apprise
import yfinance as yf
from curl_cffi import requests as curl_requests
from sqlmodel import Session, select

from db.core import get_engine
from models.models import Alert, Transaction, User, WatchlistItem
from services.fetcher import YFinanceFetcher

log = logging.getLogger(__name__)

_NASDAQ_EARN_URL = "https://api.nasdaq.com/api/calendar/earnings"


def warm_dashboard_cache():
    """
    Pre-warm the yfinance info + 7-day history cache for every ticker tracked
    by any user (watchlist + transaction history). Runs Mon-Fri every 45 min
    during market + extended hours. Uses force=True so every run refreshes the
    cache regardless of remaining TTL — the smart _ttl() in fetcher.py writes
    a 72 h entry on Friday evening to bridge the weekend.
    """
    with Session(get_engine()) as session:
        watchlist = list(session.exec(select(WatchlistItem.ticker).distinct()).all())
        tx_tickers = list(session.exec(
            select(Transaction.ticker).where(Transaction.ticker.is_not(None)).distinct()
        ).all())

    tickers = list(dict.fromkeys(watchlist + tx_tickers))
    if not tickers:
        return

    log.info("Cache warmer: warming %d tickers", len(tickers))
    warmed = 0
    for ticker in tickers:
        try:
            f = YFinanceFetcher(ticker, force=True)
            f.info()
            f.history(period="8d", interval="1d")
            warmed += 1
        except Exception as exc:
            log.warning("Cache warmer [%s]: %s", ticker, exc)
        time.sleep(0.7)  # polite inter-ticker delay

    log.info("Cache warmer: done — %d/%d tickers warmed", warmed, len(tickers))


def check_prices_and_notify():
    """
    Run every scheduler tick: fetch live prices for all alerted tickers,
    evaluate each alert, notify + disarm on trigger, re-arm when price retreats.

    Intentionally synchronous — APScheduler's AsyncIOScheduler runs plain
    callables in the default thread-pool executor, keeping the event loop free.
    """
    with Session(get_engine()) as session:
        symbols: list[str] = list(session.exec(select(Alert.ticker).distinct()).all())
        if not symbols:
            return

        prices: dict[str, float] = {}
        currencies: dict[str, str] = {}
        for sym in symbols:
            try:
                ticker = yf.Ticker(sym).fast_info
                price = ticker.get("lastPrice", 0)
                if price is not None:
                    prices[sym] = float(price)
                    currencies[sym] = ticker.get("currency") or ""
            except Exception:
                log.warning("Failed to fetch live price for %s", sym)

        if not prices:
            return

        alerts = session.exec(
            select(Alert, User).join(User, Alert.user == User.username)
        ).all()
        today = datetime.now(UTC).date()

        for alert, user in alerts:
            price = prices.get(alert.ticker)
            if price is None:
                continue

            triggered = (
                price >= alert.target_price if alert.trigger_above
                else price <= alert.target_price
            )

            if triggered:
                if alert.is_armed and alert.last_triggered != today:
                    try:
                        alert.is_armed = False
                        alert.last_triggered = today
                        session.add(alert)
                        session.commit()

                        if user.apprise_url:
                            ap_obj = apprise.Apprise()
                            for url in user.apprise_url.split(","):
                                url = url.strip()
                                if url:
                                    ap_obj.add(url)
                            currency = currencies.get(alert.ticker, "")
                            direction = 'above' if alert.trigger_above else 'below'
                            body_parts = [
                                f"{alert.ticker} crossed {direction} target price of {alert.target_price:.2f}, currently at {price:.2f} {currency}",
                            ]
                            if alert.actionable:
                                body_parts.append("Moment to SELL" if alert.trigger_above else "Moment to BUY")
                            if alert.notes:
                                body_parts.append(alert.notes)
                            ap_obj.notify(
                                title=f"{alert.ticker} {direction} {alert.target_price:.2f}",
                                body="\n".join(body_parts),
                            )
                    except Exception as e:
                        session.rollback()
                        log.error("Failed to process alert %s: %s", alert.id, e)
            else:
                if not alert.is_armed:
                    try:
                        alert.is_armed = True
                        session.add(alert)
                        session.commit()
                    except Exception as e:
                        session.rollback()
                        log.error("Failed to re-arm alert %s: %s", alert.id, e)


def notify_earnings_summary():
    """
    Daily 8AM cron (mon–fri): notify all users of the prior trading day's earnings
    reporters with price change % and EPS forecast vs actual when available.

    Monday always reports Friday's earnings (skips weekend).
    Uses retry + inter-ticker delay to avoid Yahoo Finance rate limiting.
    """
    today_weekday = datetime.now(UTC).weekday()  # 0=Mon … 6=Sun
    # Guard: should never fire on weekends given cron config, but be safe
    if today_weekday >= 5:
        return

    with Session(get_engine()) as session:
        has_recipients = session.exec(
            select(User).where(User.apprise_url.is_not(None), User.apprise_url != "", User.earnings_notify.is_not(False))
        ).first()
    if not has_recipients:
        return
    # Monday → Friday; all other weekdays → previous calendar day
    offset = 3 if today_weekday == 0 else 1
    report_date = (date.today() - timedelta(days=offset)).strftime("%Y-%m-%d")

    # --- Step 1: fetch ticker list from Nasdaq with retry + backoff ---
    tickers: dict[str, str | None] = {}
    for attempt in range(4):
        try:
            resp = curl_requests.get(
                _NASDAQ_EARN_URL,
                params={"date": report_date},
                impersonate="chrome",
                timeout=15,
            )
            if resp.status_code == 200:
                for row in (resp.json().get("data") or {}).get("rows") or []:
                    sym = str(row.get("symbol", "")).strip().upper()
                    if sym:
                        tickers[sym] = row.get("name") or None
                break
            log.warning("Nasdaq API HTTP %s (attempt %d) for %s", resp.status_code, attempt + 1, report_date)
        except Exception as exc:
            log.warning("Nasdaq API attempt %d failed for %s: %s", attempt + 1, report_date, exc)
        time.sleep(2 ** attempt)  # 1 s, 2 s, 4 s, 8 s

    if not tickers:
        log.info("Earnings notifier: no reporters for %s", report_date)
        return

    log.info("Earnings notifier: enriching %d tickers for %s", len(tickers), report_date)

    # --- Step 2: enrich each ticker sequentially with retry per ticker ---
    lines: list[str] = []
    for ticker, company in tickers.items():
        enriched = None
        for attempt in range(3):
            try:
                f = YFinanceFetcher(ticker)
                info = f.info()
                change_pct = info.get("regularMarketChangePercent")

                change_5d: float | None = None
                hist = f.history(period="8d", interval="1d")
                if not hist.empty:
                    closes = hist["Close"].dropna()
                    if len(closes) >= 2:
                        window = closes.iloc[-6:] if len(closes) >= 6 else closes
                        change_5d = (window.iloc[-1] / window.iloc[0] - 1) * 100

                eps_actual = eps_est = None
                eh = f.earnings_history()
                if not eh.empty:
                    last = eh.sort_index(ascending=False).iloc[0]
                    raw_actual = last.get("reportedEPS") or last.get("Reported EPS")
                    raw_est    = last.get("epsEstimate")  or last.get("EPS Estimate")
                    try:
                        eps_actual = float(raw_actual) if raw_actual is not None else None
                    except (TypeError, ValueError):
                        eps_actual = None
                    try:
                        eps_est = float(raw_est) if raw_est is not None else None
                    except (TypeError, ValueError):
                        eps_est = None

                enriched = {"change_pct": change_pct, "change_5d": change_5d, "eps_actual": eps_actual, "eps_est": eps_est}
                break
            except Exception as exc:
                log.warning("Notifier enrich attempt %d [%s]: %s", attempt + 1, ticker, exc)
                if attempt < 2:
                    time.sleep(2)

        # Inter-ticker delay regardless of outcome — avoids Yahoo rate limiting
        time.sleep(0.4)

        if enriched is None:
            continue

        change_1d_str = f"{enriched['change_pct']:+.1f}%" if enriched['change_pct'] is not None else "?"
        change_5d_str = f"{enriched['change_5d']:+.1f}%" if enriched['change_5d'] is not None else "?"
        eps_str = ""
        if enriched['eps_est'] is not None:
            eps_str = f" | EPS est {enriched['eps_est']:.2f}"
            if enriched['eps_actual'] is not None:
                eps_str += f" → actual {enriched['eps_actual']:.2f}"
        lines.append(f"{ticker} ({company or '?'}): 1d {change_1d_str} / 5d {change_5d_str}{eps_str}")

    if not lines:
        log.info("Earnings notifier: no enriched data to send for %s", report_date)
        return

    body = "\n".join(lines)
    log.info("Earnings notifier: sending recap (%d lines) for %s", len(lines), report_date)

    with Session(get_engine()) as session:
        users = session.exec(select(User).where(User.apprise_url.is_not(None), User.earnings_notify.is_not(False))).all()
        for user in users:
            try:
                ap = apprise.Apprise()
                for url in (user.apprise_url or "").split(","):
                    if url.strip():
                        ap.add(url.strip())
                ap.notify(title=f"Earnings recap — {report_date}", body=body)
            except Exception as exc:
                log.error("Apprise notify failed for %s: %s", user.username, exc)
