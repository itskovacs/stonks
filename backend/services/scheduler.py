import logging
from datetime import UTC, datetime

import apprise
import yfinance as yf
from sqlmodel import Session, select

from db.core import get_engine
from models.models import Alert, User

log = logging.getLogger(__name__)


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
            select(Alert, User).where(Alert.user == User.username)
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
                            ap_obj.notify(
                                title=f"{alert.ticker} {direction} {alert.target_price:.2f}",
                                body=f"{alert.ticker} crossed {direction} target price of {alert.target_price:.2f}, currently at {price:.2f} {currency}",
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
