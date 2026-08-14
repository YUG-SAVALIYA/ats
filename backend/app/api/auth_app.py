import jwt
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import AppConfig

router = APIRouter(prefix="/api/app-auth")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

import os
import secrets

SECRET_KEY = os.getenv("JWT_SECRET_KEY") or secrets.token_hex(32)
ALGORITHM = "HS256"

class SetupRequest(BaseModel):
    password: str

class LoginRequest(BaseModel):
    password: str


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

    hashed_pw = pwd_context.hash(payload.password)
    new_config = AppConfig(config_key="admin_password", config_value=hashed_pw)
    db.add(new_config)
    db.commit()
    
    return {"message": "Password setup successfully."}


@router.post("/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    """Verify the password and return a JWT token."""
    config = db.query(AppConfig).filter(AppConfig.config_key == "admin_password").first()
    if not config:
        raise HTTPException(status_code=400, detail="Password not set up yet.")

    if not pwd_context.verify(payload.password, config.config_value):
        raise HTTPException(status_code=401, detail="Invalid password.")

    # Generate JWT
    expire = datetime.now(timezone.utc) + timedelta(hours=24)
    to_encode = {"sub": "admin", "exp": expire}
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    
    return {"access_token": encoded_jwt, "token_type": "bearer"}


# Security Dependency
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Validates the JWT token."""
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("sub") != "admin":
            raise HTTPException(status_code=401, detail="Invalid token subject.")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid authentication token.")
    return "admin"
