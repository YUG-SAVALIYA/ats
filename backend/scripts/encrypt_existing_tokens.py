import os
import sys
import logging

# Add project backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.crypto import encrypt_token, is_encrypted
from database import SessionLocal
from models import BrokerCredential, Company

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ats.encrypt_existing_tokens")


def run_token_encryption_migration():
    """
    Scans PostgreSQL database for broker credentials / tokens.
    Encrypts any plaintext values using Fernet AES-128 key from environment.
    Idempotent: skips already encrypted Fernet strings.
    """
    logger.info("=== STARTING BROKER TOKEN ENCRYPTION MIGRATION ===")
    
    db = SessionLocal()
    scanned_count = 0
    encrypted_count = 0
    skipped_count = 0
    failed_count = 0

    try:
        credentials = db.query(BrokerCredential).all()
        for cred in credentials:
            scanned_count += 1
            fields_to_check = ["access_token", "refresh_token", "pin", "totp_secret"]
            row_modified = False

            for field_name in fields_to_check:
                val = getattr(cred, field_name)
                if not val:
                    continue

                if is_encrypted(val):
                    skipped_count += 1
                    logger.info("Client %s field '%s' is already encrypted — skipping.", cred.client_id, field_name)
                else:
                    try:
                        encrypted_val = encrypt_token(val)
                        setattr(cred, field_name, encrypted_val)
                        encrypted_count += 1
                        row_modified = True
                        logger.info("Client %s field '%s' encrypted successfully.", cred.client_id, field_name)
                    except Exception as field_exc:
                        failed_count += 1
                        logger.error("Failed to encrypt field '%s' for client %s: %s", field_name, cred.client_id, field_exc)

            if row_modified:
                db.commit()

    except Exception as exc:
        db.rollback()
        logger.error("Error during token encryption migration: %s", exc)
        failed_count += 1
    finally:
        db.close()

    print("\n" + "=" * 50)
    print("      MIGRATION SUMMARY")
    print("=" * 50)
    print(f"Scanned: {scanned_count}")
    print(f"Encrypted: {encrypted_count}")
    print(f"Skipped: {skipped_count}")
    print(f"Failed: {failed_count}")
    print("=" * 50 + "\n")


if __name__ == "__main__":
    run_token_encryption_migration()
