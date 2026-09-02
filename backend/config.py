"""
config.py — Central Application Configuration
=============================================
Single source of truth for all environment variables, database configuration,
application parameters, and decrypted broker credentials.
Plain, simple Python with zero decorators.
"""

import os
from dotenv import load_dotenv

# Load .env file once from backend directory or project root
env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if not os.path.exists(env_file):
    env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
load_dotenv(env_file)


class Config:
    def __init__(self):
        # Database Components
        self.db_host = (os.getenv("DB_HOST"))
        self.db_port = int(os.getenv("DB_PORT"))
        self.db_user = (os.getenv("POSTGRES_USER"))
        self.db_password = (os.getenv("POSTGRES_PASSWORD"))
        self.db_name = (os.getenv("POSTGRES_DB"))

        # Primary Database URL
        raw_db_url = (os.getenv("DATABASE_URL"))
        if raw_db_url:
            self.database_url = raw_db_url
        else:
            self.database_url = f"postgresql://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"

        # Security & Cryptography Keys
        self.token_encryption_key = (os.getenv("TOKEN_ENCRYPTION_KEY") or "").strip(' "\'')
        self.jwt_secret_key = (os.getenv("JWT_SECRET_KEY"))

        # Server & Networking
        self.port = int(os.getenv("PORT"))
        self.host = (os.getenv("HOST"))
        self.frontend_url = (os.getenv("FRONTEND_URL"))

        # Trade / Order Execution Account
        self.client_id = ""
        self.access_token = ""
        self.pin = ""
        self.totp_secret = ""

        # Market Data Account (Charts & Feeds)
        self.data_client_id = ""
        self.data_access_token = ""
        self.data_pin = ""
        self.data_totp_secret = ""

        # Risk Limits & Safety Controls
        self.max_orders_per_day = int(os.getenv("MAX_ORDERS_PER_DAY", "25"))
        self.max_daily_loss = float(os.getenv("MAX_DAILY_LOSS", "10000.0"))
        self.kill_switch = os.getenv("KILL_SWITCH", "false").lower() in ("true", "1", "yes")


def load_config():
    """Creates a Config object and loads broker credentials from the database."""
    cfg = Config()

    # Load Dhan account credentials from PostgreSQL creds table if database is configured
    if cfg.database_url:
        _load_db_credentials(cfg)

    return cfg


def _load_db_credentials(cfg):
    """Reads broker credentials from the `creds` database table."""
    try:
        from database.database import SessionLocal
        from database.models import BrokerCredential
        from dhan.auth import decrypt_token

        db = SessionLocal()
        try:
            creds = db.query(BrokerCredential).all()
            for cred in creds:
                cid = (cred.client_id or "").strip()
                if cid == "1111482994":  # Data Account
                    cfg.data_client_id = cid
                    cfg.data_access_token = decrypt_token(cred.access_token) or ""
                    cfg.data_pin = decrypt_token(cred.pin) or ""
                    cfg.data_totp_secret = decrypt_token(cred.totp_secret) or ""
                elif cid:  # Trade Account
                    cfg.client_id = cid
                    cfg.access_token = decrypt_token(cred.access_token) or ""
                    cfg.pin = decrypt_token(cred.pin) or ""
                    cfg.totp_secret = decrypt_token(cred.totp_secret) or ""
        finally:
            db.close()
    except Exception:
        pass
