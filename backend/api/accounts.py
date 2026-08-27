"""
api/accounts.py — Broker Authentication & Account Status Endpoints
==================================================================
Provides Dhan broker authentication status and token renewal endpoints:
- GET  /api/auth/status
- POST /api/auth/renew
"""

from __future__ import annotations

import logging
from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException

from api.auth import get_current_user
from dhan.client import get_dhan_client
from dhan.portfolio import PortfolioService

logger = logging.getLogger("ats.api.accounts")

router = APIRouter(prefix="/api/auth", tags=["Broker Accounts"])
_portfolio_service = PortfolioService()


class RenewTokenRequest(BaseModel):
    totp: Optional[str] = None


@router.get("/status")
def get_broker_auth_status(current_user: str = Depends(get_current_user)):
    """Check connection status with Dhan broker API."""
    client = get_dhan_client()
    cfg = client.config
    token = client.auth_manager.get_valid_token()
    token_suffix = token[-4:] if token and len(token) >= 4 else "NONE"
    
    try:
        limits = _portfolio_service.get_fund_limits()
        has_funds = bool(limits and not limits.get("status") == "failure")
        return {
            "status": "connected" if has_funds else "disconnected",
            "client_id": cfg.client_id or "UNSET",
            "token_valid": has_funds,
            "token_suffix": token_suffix,
            "funds_preview": limits if has_funds else None
        }
    except Exception as exc:
        return {
            "status": "error",
            "client_id": cfg.client_id or "UNSET",
            "token_valid": False,
            "token_suffix": token_suffix,
            "error": str(exc)
        }


@router.post("/renew")
def renew_broker_token(req: RenewTokenRequest = None, current_user: str = Depends(get_current_user)):
    """Force renew Dhan broker access token via TOTP / RenewToken endpoint."""
    client = get_dhan_client()
    totp = req.totp if req else None
    success = client.auth_manager.refresh_token(manual_totp=totp)
    
    if success:
        new_token = client.auth_manager.get_valid_token()
        token_suffix = new_token[-4:] if new_token and len(new_token) >= 4 else "NONE"
        logger.info("[ACCOUNTS] Dhan broker access token renewed successfully.")
        return {
            "success": True,
            "message": "Token renewed successfully",
            "token_suffix": token_suffix
        }
    else:
        logger.error("[ACCOUNTS] Failed to renew Dhan broker token.")
        raise HTTPException(
            status_code=400,
            detail="Failed to renew token. Please ensure client_id, pin, and totp_secret are valid in `creds` DB."
        )
