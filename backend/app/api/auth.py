"""
app.api.auth
============
Broker authentication status and token renewal endpoints.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional

from app.data.database import SessionLocal
from app.data.models import DhanAccount
from app.broker.dhan_client import get_dhan_data_client
from app.broker.dhan_auth import DhanAuthManager
from app.api.auth_app import require_admin, CurrentUser

router = APIRouter(tags=["Broker Authentication"])


class RenewRequest(BaseModel):
    totp: Optional[str] = None
    dhan_account_id: Optional[str] = None


@router.get("/auth/status")
def get_auth_status():
    """
    Returns the authentication status for the data feed client and all configured Dhan accounts.
    Masks all sensitive credential tokens.
    """
    try:
        data_client = get_dhan_data_client()
        is_token_active = False
        client_id_masked = ""

        if data_client and getattr(data_client, "config", None):
            raw_cid = getattr(data_client.config, "data_client_id", "") or getattr(data_client.config, "client_id", "")
            if raw_cid:
                client_id_masked = f"***{raw_cid[-4:]}" if len(raw_cid) >= 4 else "***"
            is_token_active = bool(getattr(data_client.config, "data_access_token", "") or getattr(data_client.config, "access_token", ""))

        db = SessionLocal()
        accounts = db.query(DhanAccount).all()
        db.close()

        accounts_status = []
        for acc in accounts:
            cid = acc.client_id or ""
            accounts_status.append({
                "dhan_account_id": acc.id,
                "client_id": f"***{cid[-4:]}" if len(cid) >= 4 else "***",
                "status": acc.account_status,
                "token_active": bool(acc.access_token),
                "totp_configured": bool(acc.totp_secret),
                "is_data_account": getattr(acc, "is_data_account", False),
            })

        return {
            "status": "connected" if is_token_active else "disconnected",
            "client_id": client_id_masked,
            "token_active": is_token_active,
            "totp_configured": True,
            "mode": "PROD",
            "accounts": accounts_status
        }
    except Exception as exc:
        return {
            "status": "disconnected",
            "client_id": "",
            "token_active": False,
            "totp_configured": False,
            "mode": "ERROR",
            "error": str(exc),
            "accounts": []
        }


@router.post("/auth/renew")
def renew_token(req: RenewRequest = RenewRequest(), _: CurrentUser = Depends(require_admin)):
    """ADMIN only. Renews Dhan access token using TOTP or /RenewToken endpoint."""
    try:
        db = SessionLocal()
        if req.dhan_account_id:
            account = db.query(DhanAccount).filter(DhanAccount.id == req.dhan_account_id).first()
        else:
            account = db.query(DhanAccount).filter(DhanAccount.account_status == "ACTIVE").first()
        db.close()

        if not account:
            raise HTTPException(status_code=404, detail="No active Dhan account found to renew.")

        auth_mgr = DhanAuthManager(dhan_account_id=account.id)
        success = auth_mgr.refresh_token(totp_override=req.totp)
        if success:
            return {"status": "success", "message": f"Token renewed successfully for account ***{account.client_id[-4:]}"}
        else:
            raise HTTPException(status_code=500, detail="Token renewal failed. Check credentials or TOTP.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to renew token: {exc}")
