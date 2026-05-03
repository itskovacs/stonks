"""
deps.py
=======
FastAPI dependency injectors.

get_current_username validates:
  1. JWT signature and expiry
  2. "typ" claim must be "access" — refresh tokens are rejected here
  3. The subject user must exist in the database
"""

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session

from config import get_settings
from db.core import get_engine
from models.models import User

oauth_password_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_session():
    with Session(get_engine()) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def get_current_username(
    token: Annotated[str, Depends(oauth_password_scheme)],
    session: SessionDep,
) -> str:
    try:
        payload = jwt.decode(
            token, get_settings().SECRET_KEY, algorithms=[get_settings().ALGORITHM]
        )
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    # Enforce token type — a refresh token must never grant API access
    if payload.get("typ") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")

    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = session.get(User, username)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return user.username
