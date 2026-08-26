import os
import logging
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger("ats.config")

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
if not _ENV_PATH.exists():
    _ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(_ENV_PATH, override=True)


class Config:
    def __init__(
        self,
        # Trade / Order Execution Account
        client_id: str = "",
        access_token: str = "",
        pin: str = "",
        totp_secret: str = "",
        # Premium Market Data Account
        data_client_id: str = "",
        data_access_token: str = "",
        data_pin: str = "",
        data_totp_secret: str = "",
        # Database & Limits
        database_url: str = "",
        max_orders_per_day: int = 25,
        max_daily_loss: float = 10000.0,
        kill_switch: bool = False
    ):
        self.client_id = client_id
        self.access_token = access_token
        self.pin = pin
        self.totp_secret = totp_secret

        self.data_client_id = data_client_id
        self.data_access_token = data_access_token
        self.data_pin = data_pin
        self.data_totp_secret = data_totp_secret

        self.database_url = database_url
        self.max_orders_per_day = max_orders_per_day
        self.max_daily_loss = max_daily_loss
        self.kill_switch = kill_switch

    def validate(self) -> tuple[bool, str]:
        """Validate live credentials and database configuration."""
        if not self.database_url:
            return False, "DATABASE_URL is missing in configuration."
        return True, "Configuration is valid."


def load_config() -> Config:
    database_url = os.getenv("DATABASE_URL", "").strip()
    data_client_env = os.getenv("DATA_CLIENT_ID", "").strip()

    client_id = ""
    access_token = ""
    pin = ""
    totp_secret = ""

    data_client_id = ""
    data_access_token = ""
    data_pin = ""
    data_totp_secret = ""

    if database_url:
        try:
            from sqlalchemy import create_engine, text
            from app.services.crypto import decrypt_token

            temp_engine = create_engine(database_url, pool_pre_ping=True)
            with temp_engine.connect() as conn:
                # 1. Try loading from primary `dhan_accounts` table
                try:
                    acc_rows = conn.execute(text(
                        'SELECT client_id, access_token, pin, totp_secret, is_data_account '
                        'FROM dhan_accounts WHERE account_status = :status ORDER BY created_at ASC'
                    ), {"status": "ACTIVE"}).fetchall()
                    
                    for r in acc_rows:
                        cid, db_tok, db_pin, db_totp, is_data = r
                        cid_str = str(cid or "").strip()
                        
                        # Designated data account condition
                        if is_data or (data_client_env and cid_str == data_client_env) or (not data_client_id and cid_str == "1111482994"):
                            data_client_id = cid_str
                            data_access_token = decrypt_token(str(db_tok)) if db_tok else ""
                            data_pin = decrypt_token(str(db_pin)) if db_pin else ""
                            data_totp_secret = decrypt_token(str(db_totp)) if db_totp else ""
                        elif cid_str:
                            client_id = cid_str
                            access_token = decrypt_token(str(db_tok)) if db_tok else ""
                            pin = decrypt_token(str(db_pin)) if db_pin else ""
                            totp_secret = decrypt_token(str(db_totp)) if db_totp else ""
                except Exception as acc_exc:
                    logger.debug("[CONFIG] dhan_accounts query notice: %s", acc_exc)

                # 2. Fallback to legacy `creds` table if data_client_id or client_id still missing
                if not data_client_id or not client_id:
                    try:
                        rows = conn.execute(text('SELECT client_id, access_token, pin, totp_secret FROM creds ORDER BY created_at ASC')).fetchall()
                        for r in rows:
                            cid, db_tok, db_pin, db_totp = r
                            cid_str = str(cid or "").strip()
                            if (data_client_env and cid_str == data_client_env) or cid_str == "1111482994":
                                if not data_client_id:
                                    data_client_id = cid_str
                                    data_access_token = decrypt_token(str(db_tok)) if db_tok else ""
                                    data_pin = decrypt_token(str(db_pin)) if db_pin else ""
                                    data_totp_secret = decrypt_token(str(db_totp)) if db_totp else ""
                            elif cid_str and not client_id:
                                client_id = cid_str
                                access_token = decrypt_token(str(db_tok)) if db_tok else ""
                                pin = decrypt_token(str(db_pin)) if db_pin else ""
                                totp_secret = decrypt_token(str(db_totp)) if db_totp else ""
                    except Exception:
                        pass

            temp_engine.dispose()
        except Exception as exc:
            logger.warning("[CONFIG] Could not load DB credentials: %s", exc)

    cfg = Config(
        client_id=client_id,
        access_token=access_token,
        pin=pin,
        totp_secret=totp_secret,
        data_client_id=data_client_id,
        data_access_token=data_access_token,
        data_pin=data_pin,
        data_totp_secret=data_totp_secret,
        database_url=database_url,
        max_orders_per_day=int(os.getenv("MAX_ORDERS_PER_DAY", "25")),
        max_daily_loss=float(os.getenv("MAX_DAILY_LOSS", "10000.0")),
        kill_switch=os.getenv("KILL_SWITCH", "false").strip().lower() in ("true", "1", "yes"),
    )
    return cfg
