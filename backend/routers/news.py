"""
News router
===========
  GET /api/news/{ticker}?limit=20  → latest headlines for a single ticker
"""



import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from deps import get_current_username
from services.news_service import fetch_news_async

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/news", tags=["News"])


@router.get("/{ticker}", summary="Latest news headlines for a ticker")
async def get_news(
    ticker: str,
    current_user: Annotated[str, Depends(get_current_username)],
    limit: int = Query(20, ge=1, le=50, description="N headlines to return"),
):
    """
    Returns up to `limit` headlines from free RSS feeds
    (Yahoo Finance, Google News, Seeking Alpha) with rule-based sentiment tagging.
    """
    try:
        return await fetch_news_async(ticker.strip().upper(), limit=limit)
    except Exception as exc:
        logger.error(f'Error retrieving news for {ticker}: {exc}')
        raise HTTPException(status_code=500, detail="Error while retrieving news")
