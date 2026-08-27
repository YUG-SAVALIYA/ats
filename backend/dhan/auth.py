"""
dhan/auth.py — Dhan Authentication & Credential Encryption Manager
==================================================================
Manages Dhan API access tokens with Fernet AES encryption in PostgreSQL `creds` table.
Handles TOTP generation, token caching, 18-hour validity tracking, and token renewals.
"""

from __future__ import annotations

import os
import time
import logging
import threading
import requests
from datetime import datetime, timezone
from typing import Optional
from cryptography.fernet import Fernet, InvalidToken

try:
    import pyotp
except ImportError:
    pyotp = None

from dhan.endpoints import AUTH_GENERATE_TOKEN_URL, AUTH_RENEW_TOKEN_URL

logger = logging.getLogger("ats.dhan.auth")

_AUTH_URL = AUTH_GENERATE_TOKEN_URL
_RENEW_URL = AUTH_RENEW_TOKEN_URL
_TOKEN_VALIDITY_MS = 18 * 60 * 60 * 1000  # 18 hours
_MIN_REFRESH_INTERVAL_SEC = 125           # Dhan rate limit: once per 2 minutes (120s + 5s buffer)

# ═══════════════════════════════════════════════════════════════════════════════
# FERNET AES CRYPTOGRAPHY UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

from config import load_config

_cfg = load_config()
_key = _cfg.token_encryption_key or Fernet.generate_key().decode()
_key = _key.strip().strip("'\"")
_fernet = Fernet(_key.encode())


def encrypt_token(token: Optional[str]) -> Optional[str]:
    """Encrypts a plaintext token string into a Fernet AES-128 token string."""
    if token is None or token == "":
        return token
    if is_encrypted(token):
        return token
    return _fernet.encrypt(token.encode("utf-8")).decode("utf-8")


def decrypt_token(token: Optional[str]) -> Optional[str]:
    """Decrypts a Fernet AES-128 token string back into a plaintext token string."""
    if token is None or token == "":
        return token
    try:
        return _fernet.decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, Exception):
        return token


def is_encrypted(token: Optional[str]) -> bool:
    """Checks if a token string is a valid Fernet encrypted token."""
    if not token or not isinstance(token, str):
        return False
    try:
        _fernet.decrypt(token.encode("utf-8"))
        return True
    except (InvalidToken, Exception):
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# DHAN AUTH MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class DhanAuthManager:
    """Manages Dhan API access tokens with Fernet AES encryption in PostgreSQL `creds` table."""

    def __init__(self, client_id: str, pin: str, totp_secret: str, initial_token: str = ""):
        self.client_id = client_id
        self.pin = pin
        self.totp_secret = totp_secret.replace(" ", "").replace("-", "").strip("'\"").upper() if totp_secret else ""
        self._cached_token = initial_token.strip().strip("'\"") if initial_token else ""
        self._token_expiry_ms = int(time.time() * 1000) + _TOKEN_VALIDITY_MS if self._cached_token else 0
        self._last_refresh_time = 0.0
        self._lock = threading.Lock()

        # Load existing encrypted credentials from `creds` DB table if available
        self._load_from_db()
        # Save/Sync initial credentials to `creds` DB table in encrypted Fernet AES format
        self._save_credentials_to_db()

    def _load_from_db(self):
        """Loads and decrypts all broker credentials from PostgreSQL `creds` table."""
        from database.database import SessionLocal
        from database.models import BrokerCredential

        db = SessionLocal()
        try:
            query = db.query(BrokerCredential)
            cred = query.filter(BrokerCredential.client_id == self.client_id).first() if self.client_id else query.first()
            if cred:
                if not self.client_id:
                    self.client_id = cred.client_id or ""
                if cred.access_token:
                    decrypted_tok = decrypt_token(cred.access_token)
                    if decrypted_tok and len(decrypted_tok) >= 20:
                        self._cached_token = decrypted_tok
                        if cred.updated_at:
                            now_utc = datetime.now(timezone.utc)
                            upd_utc = cred.updated_at if cred.updated_at.tzinfo else cred.updated_at.replace(tzinfo=timezone.utc)
                            token_age_ms = (now_utc - upd_utc).total_seconds() * 1000
                            self._token_expiry_ms = int(time.time() * 1000) + max(0, _TOKEN_VALIDITY_MS - token_age_ms)
                        else:
                            self._token_expiry_ms = int(time.time() * 1000) + _TOKEN_VALIDITY_MS
                if cred.pin:
                    self.pin = decrypt_token(cred.pin) or ""
                if cred.totp_secret:
                    self.totp_secret = decrypt_token(cred.totp_secret) or ""
                logger.info(f"[DHAN AUTH] Credentials loaded & decrypted from DB for client {self.client_id}")
        except Exception as exc:
            logger.warning(f"[DHAN AUTH] Error reading `creds` DB table: {exc}")
        finally:
            db.close()

    def _save_credentials_to_db(self):
        """Persists client_id, PIN, TOTP secret, and access_token in encrypted format into `creds` DB table."""
        if not self.client_id:
            return
        from database.database import SessionLocal
        from database.models import BrokerCredential

        db = SessionLocal()
        try:
            encrypted_acc_token = encrypt_token(self._cached_token) if self._cached_token else None
            encrypted_pin = encrypt_token(self.pin) if self.pin else None
            encrypted_totp = encrypt_token(self.totp_secret) if self.totp_secret else None

            cred = db.query(BrokerCredential).filter(BrokerCredential.client_id == self.client_id).first()
            if not cred:
                cred = BrokerCredential(
                    client_id=self.client_id,
                    access_token=encrypted_acc_token,
                    pin=encrypted_pin,
                    totp_secret=encrypted_totp,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
                db.add(cred)
            else:
                if encrypted_acc_token:
                    cred.access_token = encrypted_acc_token
                if encrypted_pin:
                    cred.pin = encrypted_pin
                if encrypted_totp:
                    cred.totp_secret = encrypted_totp

            db.commit()
            logger.info(f"[DHAN AUTH] Credentials encrypted & stored in `creds` DB for client {self.client_id}")
        except Exception as exc:
            db.rollback()
            logger.error(f"[DHAN AUTH] Error saving encrypted credentials to `creds` DB: {exc}")
        finally:
            db.close()

    def get_valid_token(self) -> str:
        """Returns a valid plaintext access token in memory. Generates fresh token via TOTP if expired, or reloads latest from DB."""
        now_ms = int(time.time() * 1000)
        if self._cached_token and now_ms < (self._token_expiry_ms - 5 * 60 * 1000) and len(self._cached_token) >= 20:
            return self._cached_token

        # Check DB for newer token saved by another process or scheduled job
        self._load_from_db()
        if self._cached_token and now_ms < (self._token_expiry_ms - 5 * 60 * 1000) and len(self._cached_token) >= 20:
            return self._cached_token

        # Attempt renewal or TOTP generation
        if self.refresh_token():
            return self._cached_token

        return self._cached_token

    def refresh_token(self, manual_totp: Optional[str] = None, force: bool = False) -> bool:
        """Force refresh access token using TOTP or /RenewToken endpoint with rate limit protection."""
        with self._lock:
            now = time.time()
            if not force and not manual_totp and (now - self._last_refresh_time) < _MIN_REFRESH_INTERVAL_SEC and self._cached_token and len(self._cached_token) > 20:
                logger.info(f"[DHAN AUTH] Token refreshed recently (<125s ago) for client {self.client_id}. Reusing valid token.")
                return True

            logger.info(f"[DHAN AUTH] Attempting Dhan token refresh for client {self.client_id}...")

            totp_code = manual_totp
            if not totp_code and self.totp_secret and pyotp:
                try:
                    totp_code = pyotp.TOTP(self.totp_secret).now()
                except Exception as e:
                    logger.error(f"[DHAN AUTH] TOTP generation error: {e}")

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
                            logger.info(f"[DHAN AUTH] Successfully generated fresh token via TOTP for client {self.client_id} (Suffix: {token[-4:]})")
                            return True
                        else:
                            logger.warning(f"[DHAN AUTH] Auth endpoint response missing accessToken: {data}")
                    else:
                        logger.warning(f"[DHAN AUTH] Auth endpoint returned HTTP {resp.status_code}: {resp.text[:200]}")
                except Exception as exc:
                    logger.error(f"[DHAN AUTH] Exception calling auth endpoint: {exc}")

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
                            logger.info(f"[DHAN AUTH] Successfully renewed access token via /RenewToken for client {self.client_id} (Suffix: {token[-4:]})")
                            return True
                except Exception as exc:
                    logger.error(f"[DHAN AUTH] Exception calling RenewToken: {exc}")

            if self._cached_token and len(self._cached_token) > 20:
                logger.warning(f"[DHAN AUTH] Rate-limited or TOTP refresh pending; reusing active cached token for client {self.client_id}")
                return True

            logger.error(f"[DHAN AUTH] Failed to refresh Dhan access token for client {self.client_id}")
            return False

    def _save_token(self, raw_token: str):
        """Encrypts token, saves to PostgreSQL `creds` table, and resets singletons so whole system switches to new token immediately."""
        clean_token = raw_token.strip().strip("'\"")
        self._cached_token = clean_token
        self._token_expiry_ms = int(time.time() * 1000) + _TOKEN_VALIDITY_MS

        from database.database import SessionLocal
        from database.models import BrokerCredential

        db = SessionLocal()
        try:
            encrypted_acc_token = encrypt_token(clean_token)
            encrypted_pin = encrypt_token(self.pin)
            encrypted_totp = encrypt_token(self.totp_secret)

            cred = db.query(BrokerCredential).filter(BrokerCredential.client_id == self.client_id).first()
            if not cred:
                cred = BrokerCredential(
                    client_id=self.client_id,
                    access_token=encrypted_acc_token,
                    pin=encrypted_pin,
                    totp_secret=encrypted_totp,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
                db.add(cred)
            else:
                cred.access_token = encrypted_acc_token
                cred.pin = encrypted_pin
                cred.totp_secret = encrypted_totp
                cred.updated_at = datetime.utcnow()

            db.commit()
            logger.info(f"[DHAN AUTH] Encrypted broker token saved to `creds` DB for client {self.client_id}")
        except Exception as exc:
            db.rollback()
            logger.error(f"[DHAN AUTH] Error saving encrypted token to `creds` DB: {exc}")
        finally:
            db.close()

        # Immediately reset client singletons so the entire running system switches to the new token
        try:
            from dhan.client import reset_dhan_clients
            reset_dhan_clients()
        except Exception:
            pass


def refresh_all_broker_tokens() -> Dict[str, bool]:
    """Iterates through all configured broker accounts in `creds` DB table and generates fresh tokens."""
    from database.database import SessionLocal
    from database.models import BrokerCredential

    results = {}
    db = SessionLocal()
    try:
        creds = db.query(BrokerCredential).all()
        for cred in creds:
            cid = cred.client_id
            if not cid:
                continue
            pin = decrypt_token(cred.pin) or ""
            totp = decrypt_token(cred.totp_secret) or ""
            auth = DhanAuthManager(client_id=cid, pin=pin, totp_secret=totp)
            success = auth.refresh_token()
            results[cid] = success
            logger.info(f"[DHAN AUTH] 9:00 AM Token Refresh for {cid}: {'SUCCESS' if success else 'FAILED'}")
    finally:
        db.close()
    return results
