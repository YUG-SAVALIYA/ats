"""
app.api.dhan_connection
=======================
Dhan Account Connection Gate.

Provides endpoints for connecting, disconnecting, and checking
a user's Dhan trading account.  The Dashboard is blocked until
this gate returns DHAN_CONNECTED.

Partner flow:
  1. Frontend calls GET /api/dhan/connect-url
     → Backend returns Dhan Partner consent URL
  2. User is redirected to Dhan login page
  3. Dhan redirects to GET /api/dhan/callback?tokenId=<id>
     → Backend exchanges tokenId for access_token via Dhan API
     → Saves encrypted access_token in dhan_accounts table
     → Redirects browser to frontend /?dhan_connected=1
  4. Frontend picks up the query param, transitions to DHAN_CONNECTED

Self-service fallback (if partner_id not configured):
  POST /api/dhan/connect with { client_id, access_token }
"""

import os
import logging
import requests
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.data.database import get_db
from app.data.models import DhanAccount, AccountStatus
from app.services.crypto import encrypt_token, decrypt_token
from app.api.auth_app import get_current_user, get_tenant_scope, CurrentUser, TenantScope

logger = logging.getLogger("ats.dhan_connection")

router = APIRouter(prefix="/dhan", tags=["Dhan Connection"])


# Dhan Partner / Token API
_DHAN_PARTNER_ID = os.getenv("DHAN_PARTNER_ID", "").strip()
_DHAN_REDIRECT_URI = os.getenv("DHAN_REDIRECT_URI", "").strip()
_DHAN_TOKEN_EXCHANGE_URL = "https://api.dhan.co/v2/token"
_DHAN_CONSENT_BASE = "https://auth.dhan.co/login"
_DHAN_SELF_AUTH_URL = "https://auth.dhan.co/app/generateAccessToken"
_DHAN_RENEW_URL = "https://api.dhan.co/v2/RenewToken"
_DHAN_VALIDATE_URL = "https://api.dhan.co/v2/fundlimit"

# Token validity: 18 hours (Dhan standard)
_TOKEN_VALIDITY_SECONDS = 18 * 60 * 60


# ─── Pydantic Schemas ─────────────────────────────────────────────────────────

class ConnectRequest(BaseModel):
    client_id: str
    access_token: str  # Raw plaintext from Dhan portal / Partner callback


class DisconnectRequest(BaseModel):
    confirm: bool = True


# ─── Helper: resolve user's Dhan account ──────────────────────────────────────

def _get_user_account(
    current_user: CurrentUser,
    db: Session,
) -> Optional[DhanAccount]:
    """
    Return the primary DhanAccount for the authenticated user.
    Admin sees the first non-data account.  Regular user is scoped by user_id.
    """
    if current_user.is_admin:
        return (
            db.query(DhanAccount)
            .filter(DhanAccount.is_data_account == False)
            .order_by(DhanAccount.created_at.asc())
            .first()
        )
    return (
        db.query(DhanAccount)
        .filter(DhanAccount.user_id == current_user.user_id)
        .order_by(DhanAccount.created_at.asc())
        .first()
    )


def _dhan_status_for_account(acc: Optional[DhanAccount]) -> str:
    """Map DhanAccount state to frontend DhanConnectionState string."""
    if acc is None:
        return "DHAN_NOT_CONNECTED"
    if acc.account_status == AccountStatus.TOKEN_ERROR:
        return "DHAN_AUTH_REQUIRED"
    if acc.account_status == AccountStatus.DISABLED:
        return "DHAN_NOT_CONNECTED"
    if acc.account_status in (AccountStatus.ACTIVE, AccountStatus.TRADING_HALTED, AccountStatus.API_ERROR):
        if acc.access_token:
            return "DHAN_CONNECTED"
    return "DHAN_NOT_CONNECTED"


def _validate_token_with_dhan(client_id: str, access_token: str) -> bool:
    """Call Dhan /fundlimit to verify the token is currently valid."""
    try:
        r = requests.get(
            _DHAN_VALIDATE_URL,
            headers={
                "Accept": "application/json",
                "dhanClientId": client_id,
                "access-token": access_token,
            },
            timeout=8,
        )
        return r.status_code == 200
    except Exception as exc:
        logger.warning("[DHAN_CONN] Token validation failed: %s", exc)
        return False


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/connection")
def get_dhan_connection(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Return the current user's Dhan connection status.
    Frontend polls this after ATS login to decide whether to show
    DhanConnectScreen or Dashboard.
    """
    acc = _get_user_account(current_user, db)
    status = _dhan_status_for_account(acc)

    if acc:
        cid = acc.client_id or ""
        return {
            "status": status,
            "client_id_masked": f"***{cid[-4:]}" if len(cid) >= 4 else "***",
            "account_id": acc.id,
            "account_status": acc.account_status,
            "token_present": bool(acc.access_token),
            "connected_at": acc.updated_at.isoformat() if acc.updated_at else None,
            "partner_flow_available": bool(_DHAN_PARTNER_ID),
        }

    return {
        "status": "DHAN_NOT_CONNECTED",
        "client_id_masked": None,
        "account_id": None,
        "account_status": None,
        "token_present": False,
        "connected_at": None,
        "partner_flow_available": bool(_DHAN_PARTNER_ID),
    }


@router.get("/connect-url")
def get_dhan_connect_url(
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Returns the Dhan Partner consent URL.
    Frontend opens this URL (in same tab or popup) to start the OAuth-style flow.
    """
    if not _DHAN_PARTNER_ID:
        raise HTTPException(
            status_code=400,
            detail=(
                "Dhan Partner ID (DHAN_PARTNER_ID) is not configured. "
                "Use POST /api/dhan/connect to manually provide credentials."
            ),
        )

    redirect_uri = _DHAN_REDIRECT_URI or "http://localhost:8005/api/dhan/callback"
    consent_url = (
        f"{_DHAN_CONSENT_BASE}"
        f"?client_id={_DHAN_PARTNER_ID}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
    )
    return {
        "consent_url": consent_url,
        "partner_id": _DHAN_PARTNER_ID,
        "redirect_uri": redirect_uri,
    }


@router.get("/callback")
def dhan_oauth_callback(
    tokenId: Optional[str] = Query(None),
    code: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Dhan Partner consent callback.
    Dhan redirects here after the user authenticates on dhan.co.
    Exchanges the tokenId / code for an access_token, saves it encrypted,
    then redirects the browser to the frontend with ?dhan_connected=1.

    NOTE: This endpoint is accessed directly by the browser redirect,
    so it cannot use the JWT dependency.  State/CSRF protection relies
    on the tokenId being single-use and Dhan-signed.
    """
    token_value = tokenId or code

    if error:
        logger.warning("[DHAN_CALLBACK] Dhan consent error: %s", error)
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
        if not frontend_url.startswith("http"):
            frontend_url = f"https://{frontend_url}"
        return RedirectResponse(url=f"{frontend_url}/?dhan_error={error}", status_code=302)

    if not token_value:
        raise HTTPException(status_code=400, detail="Missing tokenId or code from Dhan callback.")

    # ── Exchange tokenId for access_token (Partner flow) ──────────────────
    access_token = None
    client_id = None

    if _DHAN_PARTNER_ID:
        try:
            resp = requests.post(
                _DHAN_TOKEN_EXCHANGE_URL,
                json={
                    "partnerId": _DHAN_PARTNER_ID,
                    "tokenId": token_value,
                },
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                access_token = data.get("accessToken") or (data.get("data") or {}).get("accessToken")
                client_id = data.get("dhanClientId") or (data.get("data") or {}).get("dhanClientId")
            else:
                logger.error("[DHAN_CALLBACK] Token exchange failed HTTP %s: %s", resp.status_code, resp.text[:300])
        except Exception as exc:
            logger.error("[DHAN_CALLBACK] Token exchange error: %s", exc)

    # ── Fallback: treat tokenId itself as the access_token (self-service flow) ──
    if not access_token:
        # Try to decode client_id from JWT payload if it's a Dhan JWT
        access_token = token_value
        try:
            import base64, json as _json
            parts = token_value.split(".")
            if len(parts) == 3:
                padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
                payload = _json.loads(base64.b64decode(padded))
                client_id = str(payload.get("dhanClientId", ""))
        except Exception:
            pass

    if not access_token:
        raise HTTPException(status_code=502, detail="Could not obtain access token from Dhan.")

    # ── Validate token against Dhan API ───────────────────────────────────────
    if client_id:
        valid = _validate_token_with_dhan(client_id, access_token)
        logger.info("[DHAN_CALLBACK] Token validation for client %s: %s", client_id, valid)

    # ── Save to DB (create or update) ────────────────────────────────────────
    encrypted_token = encrypt_token(access_token)

    # For the callback we don't have a JWT, so we use client_id to find/create the account.
    # The account is scoped under the admin user (or the first user) for now.
    # In a full multi-tenant setup, state param would carry the user_id.
    existing = None
    if client_id:
        existing = db.query(DhanAccount).filter(DhanAccount.client_id == client_id).first()

    if existing:
        existing.access_token = encrypted_token
        existing.account_status = AccountStatus.ACTIVE
        existing.updated_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("[DHAN_CALLBACK] Updated access token for existing account %s", existing.id)
    elif client_id:
        # Need a user to attach to; default to finding the first user or creating
        from app.data.models import User
        admin_user = db.query(User).filter(User.email == "admin").first()
        if not admin_user:
            admin_user = db.query(User).order_by(User.created_at.asc()).first()

        if not admin_user:
            # Create a placeholder admin user
            from app.data.models import UserRole
            admin_user = User(email="admin", role=UserRole.ADMIN, is_active=True)
            db.add(admin_user)
            db.flush()

        new_acc = DhanAccount(
            user_id=admin_user.id,
            client_id=client_id,
            access_token=encrypted_token,
            account_status=AccountStatus.ACTIVE,
            is_data_account=False,
        )
        db.add(new_acc)
        db.commit()
        logger.info("[DHAN_CALLBACK] Created new DhanAccount for client %s", client_id)

    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
    if not frontend_url.startswith("http"):
        frontend_url = f"https://{frontend_url}"

    return RedirectResponse(url=f"{frontend_url}/?dhan_connected=1", status_code=302)


@router.post("/connect")
def connect_dhan_manual(
    payload: ConnectRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Self-service: Accept Client ID + Access Token directly.
    Used when DHAN_PARTNER_ID is not configured, or as fallback.
    Token is validated against Dhan API before saving.
    """
    client_id = payload.client_id.strip()
    raw_token = payload.access_token.strip().strip("'\"")

    if not client_id or not raw_token:
        raise HTTPException(status_code=400, detail="client_id and access_token are required.")

    # Validate token against Dhan before saving
    valid = _validate_token_with_dhan(client_id, raw_token)
    if not valid:
        raise HTTPException(
            status_code=401,
            detail=(
                "Dhan rejected this access token. "
                "Please generate a fresh token from the Dhan portal and try again."
            ),
        )

    encrypted_token = encrypt_token(raw_token)

    # Resolve or create user_id
    user_id = current_user.user_id
    if not user_id:
        # Admin JWT — attach to first user or create one
        from app.data.models import User
        admin_user = db.query(User).filter(User.email == "admin").first()
        if not admin_user:
            admin_user = db.query(User).order_by(User.created_at.asc()).first()
        if not admin_user:
            from app.data.models import UserRole
            admin_user = User(email="admin", role=UserRole.ADMIN, is_active=True)
            db.add(admin_user)
            db.flush()
        user_id = admin_user.id

    # Security: ensure client_id doesn't already belong to another user
    existing_other = (
        db.query(DhanAccount)
        .filter(
            DhanAccount.client_id == client_id,
            DhanAccount.user_id != user_id,
        )
        .first()
    )
    if existing_other:
        raise HTTPException(
            status_code=409,
            detail="This Dhan client ID is already connected to a different user account.",
        )

    # Create or update
    existing = (
        db.query(DhanAccount)
        .filter(DhanAccount.client_id == client_id, DhanAccount.user_id == user_id)
        .first()
    )

    if existing:
        existing.access_token = encrypted_token
        existing.account_status = AccountStatus.ACTIVE
        existing.updated_at = datetime.now(timezone.utc)
        db.commit()
        acc = existing
    else:
        acc = DhanAccount(
            user_id=user_id,
            client_id=client_id,
            access_token=encrypted_token,
            account_status=AccountStatus.ACTIVE,
            is_data_account=False,
        )
        db.add(acc)
        db.commit()
        db.refresh(acc)

    cid = acc.client_id or ""
    return {
        "status": "DHAN_CONNECTED",
        "client_id_masked": f"***{cid[-4:]}" if len(cid) >= 4 else "***",
        "account_id": acc.id,
        "message": "Dhan account connected successfully.",
    }


@router.post("/disconnect")
def disconnect_dhan(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Disconnect the current user's Dhan account.
    Clears the access token and marks the account DISABLED.
    Does NOT delete the row (preserves trade history).
    """
    acc = _get_user_account(current_user, db)
    if not acc:
        return {"status": "DHAN_NOT_CONNECTED", "message": "No Dhan account was connected."}

    acc.access_token = None
    acc.account_status = AccountStatus.DISABLED
    acc.updated_at = datetime.now(timezone.utc)
    db.commit()

    logger.info("[DHAN_CONN] Account %s disconnected by user %s", acc.id, current_user.sub)
    return {"status": "DHAN_NOT_CONNECTED", "message": "Dhan account disconnected."}


@router.post("/verify")
def verify_dhan_token(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Validate the current Dhan access token against Dhan API.
    Returns DHAN_CONNECTED or DHAN_AUTH_REQUIRED.
    Used on Dashboard refresh to detect expired tokens without user action.
    """
    acc = _get_user_account(current_user, db)
    if not acc or not acc.access_token:
        return {"status": "DHAN_NOT_CONNECTED"}

    raw_token = decrypt_token(acc.access_token) if acc.access_token else None
    if not raw_token:
        return {"status": "DHAN_AUTH_REQUIRED"}

    valid = _validate_token_with_dhan(acc.client_id or "", raw_token)

    if valid:
        if acc.account_status == AccountStatus.TOKEN_ERROR:
            acc.account_status = AccountStatus.ACTIVE
            db.commit()
        return {"status": "DHAN_CONNECTED"}
    else:
        acc.account_status = AccountStatus.TOKEN_ERROR
        db.commit()
        return {"status": "DHAN_AUTH_REQUIRED"}
