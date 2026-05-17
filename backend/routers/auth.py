"""
Auth router
===========
POST /api/auth/login    → Token pair (access + refresh)
POST /api/auth/register → Token pair on success
POST /api/auth/refresh  → New access token (requires valid refresh token)

Token type enforcement
----------------------
/refresh validates that the submitted token has typ == "refresh".
An access token is intentionally rejected here so that a leaked access
token cannot be used to silently extend a session.
"""

import jwt
from fastapi import APIRouter, Body, HTTPException

from config import get_settings
from deps import SessionDep
from models.models import User
from models.schemas import AuthParams, LoginRegisterModel, Token
from security import create_access_token, create_tokens, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/params", response_model=AuthParams)
async def auth_params() -> AuthParams:
    return {"register_enabled": get_settings().REGISTER_ENABLE}


@router.post("/login", response_model=Token)
def login(req: LoginRegisterModel, session: SessionDep) -> Token:
    db_user = session.get(User, req.username)
    if not db_user or not verify_password(req.password, db_user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return create_tokens(data={"sub": db_user.username})


@router.post("/register", response_model=Token)
def register(req: LoginRegisterModel, session: SessionDep) -> Token:
    if not get_settings().REGISTER_ENABLE:
        raise HTTPException(status_code=403, detail="Registration is disabled")
    if session.get(User, req.username):
        raise HTTPException(status_code=409, detail="Username already taken")
    session.add(User(username=req.username, hashed_password=hash_password(req.password)))
    session.commit()
    return create_tokens(data={"sub": req.username})


@router.post("/refresh", response_model=Token)
def refresh_token(
    session: SessionDep,
    token: str = Body(..., embed=True, alias="refresh_token"),
) -> Token:
    """
    Validates the refresh token and issues a new token pair.
    Rejects access tokens — typ must be exactly "refresh".
    Also verifies the user still exists in case the account was deleted.
    """
    try:
        payload = jwt.decode(
            token,
            get_settings().SECRET_KEY,
            algorithms=[get_settings().ALGORITHM],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Refresh token expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    if payload.get("typ") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid token type")

    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=401, detail="Invalid token")

    if not session.get(User, username):
        raise HTTPException(status_code=401, detail="User not found")

    return Token(
        access_token=create_access_token(data={"sub": username}),
        refresh_token=token,  # reuse existing refresh token until it expires
    )
