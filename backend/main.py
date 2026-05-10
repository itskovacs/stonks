"""
Stock Research API — FastAPI backend
All data sourced from free/open libraries (yfinance, feedparser)
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import yfinance as yf
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from config import get_settings
from db.core import init_and_migrate_db
from routers import alerts, auth, news, profile, stock
from services.scheduler import check_prices_and_notify

VERSION = "1.1.0"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def _silence_http_logging():
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(get_settings().FRONTEND_FOLDER).mkdir(parents=True, exist_ok=True)
    log.info("Initializing database...")
    yf.set_tz_cache_location(str(Path(get_settings().YF_CACHE_PATH).parent))
    await init_and_migrate_db()
    _silence_http_logging()
    log.info("STONKS ready")
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_prices_and_notify, 'cron', day_of_week='mon-fri', hour='7-22', minute='*/15')
    scheduler.start()
    log.info("Scheduler (alerts) ready")
    yield
    scheduler.shutdown()
    log.info("Shutting down")

app = FastAPI(title="Stonks", version=VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(GZipMiddleware, minimum_size=1000)

app.include_router(auth.router, prefix="/api")
app.include_router(stock.router, prefix="/api")
app.include_router(news.router, prefix="/api")
app.include_router(profile.router, prefix="/api")
app.include_router(alerts.router, prefix="/api")


@app.get("/api/")
def info():
    return {"version": VERSION}


@app.middleware("http")
async def not_found_to_spa(request: Request, call_next):
    response = await call_next(request)
    if response.status_code == 404 and not request.url.path.startswith(("/api", "/assets")):
        return FileResponse(Path(get_settings().FRONTEND_FOLDER) / "index.html")
    return response


app.mount("/", StaticFiles(directory=get_settings().FRONTEND_FOLDER, html=True), name="frontend")
