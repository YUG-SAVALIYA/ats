import os
import time
import logging
import threading
import requests
from datetime import datetime

from app.services.crypto import encrypt_token, decrypt_token
from app.database import SessionLocal
from app.models import BrokerCredential

try:
    import pyotp
except ImportError:
    pyotp = None

logger = logging.getLogger("ats.auth")

_AUTH_URL = "https://auth.dhan.co/app/generateAccessToken"
_RENEW_URL = "https://api.dhan.co/v2/RenewToken"
_TOKEN_VALIDITY_MS = 18 * 60 * 60 * 1000  # 18 hours
_MIN_REFRESH_INTERVAL_SEC = 125           # Dhan rate limit: once per 2 minutes (120s + 5s buffer)


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
        """Loads and decrypts all broker credentials (client_id, access_token, PIN, TOTP secret) from PostgreSQL `creds` table."""
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
                        # Calculate accurate expiry based on DB updated_at
                        if cred.updated_at:
                            from datetime import timezone
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
                logger.info("Broker credentials loaded & decrypted from DB `creds` table for client %s", self.client_id)
        except Exception as exc:
            logger.warning("[AUTH] Error reading `creds` DB table: %s", exc)
        finally:
            db.close()

    def _save_credentials_to_db(self):
        """Persists client_id, PIN, TOTP secret, and access_token in encrypted Fernet AES format into `creds` DB table."""
        if not self.client_id:
            return
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
            logger.info("All broker credentials (PIN, TOTP secret, token) encrypted & stored in `creds` DB table for client %s", self.client_id)
        except Exception as exc:
            db.rollback()
            logger.error("[AUTH] Error saving encrypted credentials to `creds` DB: %s", exc)
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

            logger.error("Failed to refresh Dhan access token for client %s", self.client_id)
            return False

    def _save_token(self, raw_token: str):
        """Encrypts token and saves ONLY to PostgreSQL `creds` table."""
        clean_token = raw_token.strip().strip("'\"")
        self._cached_token = clean_token
        self._token_expiry_ms = int(time.time() * 1000) + _TOKEN_VALIDITY_MS

        # 1. Encrypt and persist into `creds` PostgreSQL table
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
            logger.info("Encrypted broker token saved to PostgreSQL `creds` table for client %s", self.client_id)
        except Exception as exc:
            db.rollback()
            logger.error("[AUTH] Error saving encrypted token to `creds` DB: %s", exc)
        finally:
            db.close()
