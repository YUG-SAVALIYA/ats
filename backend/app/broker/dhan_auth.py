"""
app.broker.dhan_auth
====================
Manages Dhan API access tokens with Fernet AES encryption in PostgreSQL `dhan_accounts` table.
"""

import os
import time
import logging
import threading
import requests
from datetime import datetime, timezone

from app.services.crypto import encrypt_token, decrypt_token
from app.data.database import SessionLocal
from app.data.models import DhanAccount, AccountStatus

try:
    import pyotp
except ImportError:
    pyotp = None

logger = logging.getLogger("ats.dhan_auth")

_AUTH_URL = "https://auth.dhan.co/app/generateAccessToken"
_RENEW_URL = "https://api.dhan.co/v2/RenewToken"
_TOKEN_VALIDITY_MS = 18 * 60 * 60 * 1000  # 18 hours
_MIN_REFRESH_INTERVAL_SEC = 125           # Dhan rate limit: once per 2 minutes (120s + 5s buffer)


class DhanAuthManager:
    """Manages Dhan API access tokens with Fernet AES encryption in PostgreSQL `dhan_accounts` table."""

    def __init__(self, dhan_account_id: str):
        self.dhan_account_id = dhan_account_id
        self.client_id = ""
        self.pin = ""
        self.totp_secret = ""
        self._cached_token = ""
        self._token_expiry_ms = 0
        self._last_refresh_time = 0.0
        self._lock = threading.Lock()

        # Load existing encrypted credentials from DB
        self._load_from_db()

    def _load_from_db(self):
        """Loads and decrypts all broker credentials from PostgreSQL `dhan_accounts` table."""
        db = SessionLocal()
        try:
            acc = db.query(DhanAccount).filter(DhanAccount.id == self.dhan_account_id).first()
            if acc:
                self.client_id = acc.client_id or ""
                if acc.access_token:
                    decrypted_tok = decrypt_token(acc.access_token)
                    if decrypted_tok and len(decrypted_tok) >= 20:
                        self._cached_token = decrypted_tok
                        # Calculate accurate expiry based on DB updated_at
                        if acc.updated_at:
                            now_utc = datetime.now(timezone.utc)
                            upd_utc = acc.updated_at if acc.updated_at.tzinfo else acc.updated_at.replace(tzinfo=timezone.utc)
                            token_age_ms = (now_utc - upd_utc).total_seconds() * 1000
                            self._token_expiry_ms = int(time.time() * 1000) + max(0, _TOKEN_VALIDITY_MS - token_age_ms)
                        else:
                            self._token_expiry_ms = int(time.time() * 1000) + _TOKEN_VALIDITY_MS
                if acc.pin:
                    self.pin = decrypt_token(acc.pin) or ""
                if acc.totp_secret:
                    self.totp_secret = decrypt_token(acc.totp_secret).replace(" ", "").replace("-", "").strip("'\"").upper()
                logger.info("Broker credentials loaded & decrypted from DB for account %s (client %s)", self.dhan_account_id, self.client_id)
            else:
                logger.warning("No DhanAccount found for ID %s", self.dhan_account_id)
        except Exception as exc:
            logger.warning("[AUTH] Error reading DB: %s", exc)
        finally:
            db.close()

    def get_valid_token(self) -> str:
        """Returns a valid plaintext access token in memory. Generates fresh token via TOTP if expired."""
        now_ms = int(time.time() * 1000)
        if self._cached_token and now_ms < (self._token_expiry_ms - 5 * 60 * 1000):
            return self._cached_token

        # Attempt renewal or TOTP generation
        if self.refresh_token():
            return self._cached_token

        return self._cached_token

    def refresh_token(self, manual_totp: str = None) -> bool:
        """Force refresh access token using TOTP or /RenewToken endpoint with 2-min rate limit protection."""
        with self._lock:
            now = time.time()
            if not manual_totp and (now - self._last_refresh_time) < _MIN_REFRESH_INTERVAL_SEC and self._cached_token and len(self._cached_token) > 20:
                logger.info("Broker token refreshed recently (<125s ago) for client %s. Reusing valid token.", self.client_id)
                return True

            logger.info("Attempting Dhan token refresh for client %s...", self.client_id)

            totp_code = manual_totp
            if not totp_code and self.totp_secret and pyotp:
                try:
                    totp_code = pyotp.TOTP(self.totp_secret).now()
                except Exception as e:
                    logger.error("[AUTH] TOTP generation error: %s", e)

            if totp_code and self.client_id and self.pin:
                try:
                    params = {
                        "dhanClientId": self.client_id,
                        "pin": self.pin,
                        "totp": totp_code
                    }
                    resp = requests.post(_AUTH_URL, params=params, headers={"Accept": "application/json"}, timeout=15)
                    if resp.status_code == 200:
                        data = resp.json()
                        token = data.get("accessToken") or (data.get("data") or {}).get("accessToken")
                        if token and len(token) >= 20:
                            self._last_refresh_time = time.time()
                            self._save_token(token)
                            logger.info("Successfully generated fresh token via TOTP for client %s (Suffix: %s)", self.client_id, token[-4:])
                            return True
                        else:
                            logger.warning("[AUTH] Auth endpoint response missing accessToken: %s", data)
                    else:
                        logger.warning("[AUTH] Auth endpoint returned HTTP %s: %s", resp.status_code, resp.text[:200])
                except Exception as exc:
                    logger.error("[AUTH] Exception calling auth endpoint: %s", exc)

            if self._cached_token and self.client_id:
                try:
                    headers = {
                        "Accept": "application/json",
                        "dhanClientId": self.client_id,
                        "access-token": self._cached_token
                    }
                    resp = requests.post(_RENEW_URL, headers=headers, timeout=10)
                    if resp.status_code in (200, 201, 202):
                        data = resp.json()
                        token = data.get("accessToken") or (data.get("data") or {}).get("accessToken")
                        if token and len(token) >= 20:
                            self._save_token(token)
                            self._last_refresh_time = time.time()
                            logger.info("Successfully renewed access token via /RenewToken for client %s (Suffix: %s)", self.client_id, token[-4:])
                            return True
                except Exception as exc:
                    logger.error("[AUTH] Exception calling RenewToken: %s", exc)

            if self._cached_token and len(self._cached_token) > 20:
                logger.warning("[AUTH] Rate-limited or TOTP refresh pending; reusing active cached token for client %s", self.client_id)
                return True

            logger.error("Failed to refresh Dhan access token for account %s (client %s). Flagging as TOKEN_ERROR.", self.dhan_account_id, self.client_id)
            self._mark_account_token_error()
            return False

    def _mark_account_token_error(self):
        """Flags the DhanAccount as TOKEN_ERROR in DB so the system skips it until manual intervention."""
        db = SessionLocal()
        try:
            acc = db.query(DhanAccount).filter(DhanAccount.id == self.dhan_account_id).first()
            if acc:
                acc.account_status = AccountStatus.TOKEN_ERROR
                db.commit()
                logger.error("DhanAccount %s flagged as TOKEN_ERROR.", self.dhan_account_id)
        except Exception as exc:
            db.rollback()
            logger.error("[AUTH] Failed to mark account %s as TOKEN_ERROR: %s", self.dhan_account_id, exc)
        finally:
            db.close()

    def _save_token(self, raw_token: str):
        """Encrypts token and saves ONLY to PostgreSQL `dhan_accounts` table."""
        clean_token = raw_token.strip().strip("'\"")
        self._cached_token = clean_token
        self._token_expiry_ms = int(time.time() * 1000) + _TOKEN_VALIDITY_MS

        db = SessionLocal()
        try:
            encrypted_acc_token = encrypt_token(clean_token)
            
            acc = db.query(DhanAccount).filter(DhanAccount.id == self.dhan_account_id).first()
            if acc:
                acc.access_token = encrypted_acc_token
                # Clear TOKEN_ERROR if we successfully got a new token
                if acc.account_status == AccountStatus.TOKEN_ERROR:
                    acc.account_status = AccountStatus.ACTIVE
                acc.updated_at = datetime.utcnow()
                db.commit()
                logger.info("Encrypted broker token saved to DB for account %s (client %s)", self.dhan_account_id, self.client_id)
            else:
                logger.warning("Could not save token; account %s not found.", self.dhan_account_id)
        except Exception as exc:
            db.rollback()
            logger.error("[AUTH] Error saving encrypted token to DB: %s", exc)
        finally:
            db.close()
