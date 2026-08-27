"""
api/auth.py — Application Authentication & Master Password Security
===================================================================
Provides JWT authentication routes for frontend lock-screen & admin protection:
- GET  /api/app-auth/status
- POST /api/app-auth/setup
- POST /api/app-auth/login
Includes `get_current_user` FastAPI security dependency.
"""

from __future__ import annotations

import os
import uuid
import logging
from datetime import datetime, timedelta
from typing import Optional
import jwt
import bcrypt
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from database.database import get_db
from database.models import AppConfig

logger = logging.getLogger("ats.api.auth")

from config import load_config

_cfg = load_config()
SECRET_KEY = _cfg.jwt_secret_key
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30
router = APIRouter(prefix="/api/app-auth", tags=["App Auth"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/app-auth/login", auto_error=False)

CONFIG_KEY_HASH = "master_password_hash"


class PasswordSetupRequest(BaseModel):
    password: str


class PasswordLoginRequest(BaseModel):
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AuthStatusResponse(BaseModel):
    is_setup: bool


def _get_password_hash(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def _verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False


def _create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(token: Optional[str] = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    """FastAPI dependency to protect endpoints requiring valid login JWT."""
    config_entry = db.query(AppConfig).filter(AppConfig.config_key == CONFIG_KEY_HASH).first()
    if not config_entry or not config_entry.config_value:
        return "admin"  # If password hasn't been set up yet, allow setup

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user: str = payload.get("sub")
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials / Token expired",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.get("/status", response_model=AuthStatusResponse)
def get_auth_status(db: Session = Depends(get_db)):
    """Checks whether the system has a master password set up."""
    entry = db.query(AppConfig).filter(AppConfig.config_key == CONFIG_KEY_HASH).first()
    return {"is_setup": bool(entry and entry.config_value)}


@router.post("/setup", response_model=TokenResponse)
def setup_password(req: PasswordSetupRequest, db: Session = Depends(get_db)):
    """Set the master password initially."""
    entry = db.query(AppConfig).filter(AppConfig.config_key == CONFIG_KEY_HASH).first()
    if entry and entry.config_value:
        raise HTTPException(status_code=400, detail="Master password already set up. Use login instead.")

    if len(req.password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters long.")

    hashed = _get_password_hash(req.password)
    if not entry:
        entry = AppConfig(
            id=str(uuid.uuid4()),
            config_key=CONFIG_KEY_HASH,
            config_value=hashed,
            updated_at=datetime.utcnow()
        )
        db.add(entry)
    else:
        entry.config_value = hashed
        entry.updated_at = datetime.utcnow()

    db.commit()
    logger.info("[AUTH] Master password successfully set up.")
    token = _create_access_token({"sub": "admin"})
    return {"access_token": token, "token_type": "bearer"}


@router.post("/login", response_model=TokenResponse)
def login_password(req: PasswordLoginRequest, db: Session = Depends(get_db)):
    """Authenticates against the master password and returns a JWT access token."""
    entry = db.query(AppConfig).filter(AppConfig.config_key == CONFIG_KEY_HASH).first()
    if not entry or not entry.config_value:
        raise HTTPException(status_code=400, detail="Master password is not set up. Please setup first.")

    if not _verify_password(req.password, entry.config_value):
        logger.warning("[AUTH] Failed login attempt: invalid master password.")
        raise HTTPException(status_code=401, detail="Invalid password.")

    logger.info("[AUTH] Successful login. Issuing JWT.")
    token = _create_access_token({"sub": "admin"})
    return {"access_token": token, "token_type": "bearer"}
