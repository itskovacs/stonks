"""
config.py
=========
Application settings via pydantic-settings.

SECRET_KEY intentional design
------------------------------
If SECRET_KEY is not provided via environment or config file, a new random
key is generated on every process start.  This invalidates all existing JWTs
on restart, which is acceptable for local development but wrong for any
deployed instance.

We emit a loud WARNING so the operator knows to set a stable key.
The app is NOT halted because the local-dev use case is valid.
In production, set the key via storage/config.env or a SECRET_KEY env var.
"""

import logging
import os
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

CONFIG_FILE = Path("storage/config.env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=CONFIG_FILE)

    FRONTEND_FOLDER: str = "frontend"

    SQLITE_FILE: str = "storage/db.sqlite"
    YF_CACHE_PATH: str = "storage/yf_cache.sqlite"

    # Generates a fresh random key if not set — see module docstring.
    SECRET_KEY: str = secrets.token_hex(32)
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 h


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if not CONFIG_FILE.exists() and "SECRET_KEY" not in os.environ:
        logger.warning(
            "SECRET_KEY is using an ephemeral random value — all JWTs will be "
            "invalidated on every restart.  Set a stable SECRET_KEY in "
            "%s or as a SECRET_KEY environment variable for any non-local deployment.",
            CONFIG_FILE,
        )
    return s


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
