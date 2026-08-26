"""
app.api.auth_app
================
Application Authentication & Multi-Tenant Scoping.
"""

import jwt
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.data.database import SessionLocal
from app.data.models import AppConfig, User, DhanAccount
import bcrypt
import os
import secrets

router = APIRouter(prefix="/api/app-auth")


def get_jwt_secret_key() -> str:
    env_key = os.getenv("JWT_SECRET_KEY")
    try:
        db = SessionLocal()
        config = db.query(AppConfig).filter(AppConfig.config_key == "jwt_secret_key").first()
        if config and config.config_value:
            secret = config.config_value
            db.close()
            return secret
        
        secret_to_save = env_key.strip() if (env_key and env_key.strip()) else secrets.token_hex(32)
        new_config = AppConfig(config_key="jwt_secret_key", config_value=secret_to_save)
        db.add(new_config)
        db.commit()
        db.close()
        return secret_to_save
    except Exception:
        if env_key and env_key.strip():
            return env_key.strip()
        return secrets.token_hex(32)


SECRET_KEY = get_jwt_secret_key()
ALGORITHM = "HS256"


class SetupRequest(BaseModel):
    password: str


class LoginRequest(BaseModel):
    password: str
    email: Optional[str] = None


@dataclass
class CurrentUser:
    sub: str                        # email or "admin"
    user_id: Optional[str]         # None for legacy admin JWT
    role: str                       # "admin" or "user"
    is_admin: bool

    def get(self, key, default=None):
        if key == "sub":
            return self.sub
        elif key == "user_id":
            return self.user_id
        elif key == "role":
            return self.role
        elif key == "is_admin":
            return self.is_admin
        elif key == "email":
            return self.sub
        return default

    def __getitem__(self, key):
        val = self.get(key)
        if val is None and key not in ("sub", "user_id", "role", "is_admin", "email"):
            raise KeyError(key)
        return val


@dataclass
class TenantScope:
    user_id: Optional[str]
    account_ids: Optional[List[str]]  # None = no filter (admin sees everything)
    is_admin: bool = False

    def get(self, key, default=None):
        if key == "user_id":
            return self.user_id
        elif key == "account_ids":
            return self.account_ids
        elif key == "is_admin":
            return self.is_admin
        return default

    def __getitem__(self, key):
        val = self.get(key)
        if val is None and key not in ("user_id", "account_ids", "is_admin"):
            raise KeyError(key)
        return val


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/status")
def get_auth_status(db: Session = Depends(get_db)):
    """Check if the master password has been set up."""
    config = db.query(AppConfig).filter(AppConfig.config_key == "admin_password").first()
    return {"is_setup": config is not None}


@router.post("/setup")
def setup_password(payload: SetupRequest, db: Session = Depends(get_db)):
    """Set the master password if it hasn't been set yet."""
    config = db.query(AppConfig).filter(AppConfig.config_key == "admin_password").first()
    if config:
        raise HTTPException(status_code=400, detail="Password already set.")
    
    if len(payload.password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters.")

    hashed_pw = bcrypt.hashpw(payload.password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    new_config = AppConfig(config_key="admin_password", config_value=hashed_pw)
    db.add(new_config)
    db.commit()
    
    return {"message": "Password setup successfully."}


@router.post("/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    """
    Verify the password and return a JWT token.
    - If `email` is omitted: issues legacy admin JWT (sub="admin"). All data visible.
    - If `email` is provided: issues per-user JWT scoped to that user's DhanAccounts.
    """
    config = db.query(AppConfig).filter(AppConfig.config_key == "admin_password").first()
    if not config:
        raise HTTPException(status_code=400, detail="Password not set up yet.")

    if not bcrypt.checkpw(payload.password.encode('utf-8'), config.config_value.encode('utf-8')):
        raise HTTPException(status_code=401, detail="Invalid password.")

    expire = datetime.now(timezone.utc) + timedelta(hours=24)

    if payload.email:
        user = db.query(User).filter(User.email == payload.email, User.is_active == True).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"No active user found for email: {payload.email}")

        to_encode = {
            "sub": user.email,
            "user_id": user.id,
            "role": user.role,
            "exp": expire,
        }
    else:
        to_encode = {"sub": "admin", "role": "admin", "exp": expire}

    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return {"access_token": encoded_jwt, "token_type": "bearer"}


from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer(auto_error=False)


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> CurrentUser:
    """Validates the JWT token and returns a CurrentUser."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        sub = payload.get("sub")
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid token subject.")
        
        user_id = payload.get("user_id")
        role = payload.get("role", "admin")
        is_admin = (sub == "admin" or role == "admin")
        
        return CurrentUser(sub=sub, user_id=user_id, role=role, is_admin=is_admin)

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid authentication token.")


def get_tenant_scope(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TenantScope:
    """Derives the tenant scope from the current user's JWT."""
    user_id = current_user.get("user_id") if isinstance(current_user, dict) else getattr(current_user, "user_id", None)
    is_admin = current_user.get("is_admin", False) if isinstance(current_user, dict) else getattr(current_user, "is_admin", False)
    
    if user_id is None or is_admin:
        return TenantScope(user_id=None, account_ids=None, is_admin=True)

    accounts = (
        db.query(DhanAccount.id)
        .filter(DhanAccount.user_id == user_id)
        .all()
    )
    account_ids = [row[0] for row in accounts]
    return TenantScope(user_id=user_id, account_ids=account_ids, is_admin=False)


def require_admin(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """FastAPI dependency that enforces ADMIN-only access."""
    if isinstance(current_user, dict):
        is_admin = current_user.get("is_admin", False) or current_user.get("role") == "admin" or current_user.get("sub") == "admin"
    else:
        is_admin = getattr(current_user, "is_admin", False)

    if not is_admin:
        raise HTTPException(
            status_code=403,
            detail="Administrator access required. This operation is not permitted for regular users.",
        )
    return current_user


@router.get("/me")
def get_current_user_profile(
    current_user: CurrentUser = Depends(get_current_user),
    scope: TenantScope = Depends(get_tenant_scope),
    db: Session = Depends(get_db),
):
    """
    Verify active JWT token and return current user identity, tenant scope,
    and Dhan connection status.  Frontend uses this single call on startup
    to determine whether to show Dashboard or DhanConnectScreen.
    """
    # Inline Dhan status check (avoids a second HTTP round-trip)
    from app.data.models import DhanAccount, AccountStatus
    try:
        if current_user.is_admin:
            acc = (
                db.query(DhanAccount)
                .filter(DhanAccount.is_data_account == False)
                .order_by(DhanAccount.created_at.asc())
                .first()
            )
        else:
            acc = (
                db.query(DhanAccount)
                .filter(DhanAccount.user_id == current_user.user_id)
                .order_by(DhanAccount.created_at.asc())
                .first()
            )

        if acc is None:
            dhan_status = "DHAN_NOT_CONNECTED"
        elif acc.account_status == AccountStatus.TOKEN_ERROR:
            dhan_status = "DHAN_AUTH_REQUIRED"
        elif acc.account_status == AccountStatus.DISABLED:
            dhan_status = "DHAN_NOT_CONNECTED"
        elif acc.access_token:
            dhan_status = "DHAN_CONNECTED"
        else:
            dhan_status = "DHAN_NOT_CONNECTED"
    except Exception:
        dhan_status = "DHAN_NOT_CONNECTED"

    return {
        "authenticated": True,
        "sub": current_user.sub,
        "user_id": current_user.user_id,
        "role": current_user.role,
        "is_admin": current_user.is_admin,
        "account_ids": scope.account_ids,
        "dhan_status": dhan_status,
    }



