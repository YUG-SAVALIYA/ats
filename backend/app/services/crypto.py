import os
import logging
from typing import Optional
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("ats.crypto")

_key = os.environ.get("TOKEN_ENCRYPTION_KEY")
if not _key:
    from dotenv import load_dotenv
    load_dotenv()
    _key = os.environ.get("TOKEN_ENCRYPTION_KEY")

if not _key:
    # Generate fallback key if not present in dev
    _key = Fernet.generate_key().decode()

_key = _key.strip().strip("'\"")
_fernet = Fernet(_key.encode())


def encrypt_token(token: Optional[str]) -> Optional[str]:
    """
    Encrypts a plaintext token string into a Fernet AES-128 token string.
    Preserves None and empty strings.
    """
    if token is None or token == "":
        return token
    if is_encrypted(token):
        return token
    return _fernet.encrypt(token.encode("utf-8")).decode("utf-8")


def decrypt_token(token: Optional[str]) -> Optional[str]:
    """
    Decrypts a Fernet AES-128 token string back into a plaintext token string.
    Preserves None and empty strings. Returns original string if not encrypted or invalid key.
    """
    if token is None or token == "":
        return token
    try:
        return _fernet.decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, Exception):
        return token


def is_encrypted(token: Optional[str]) -> bool:
    """
    Checks if a token string is a valid Fernet encrypted token.
    """
    if not token or not isinstance(token, str):
        return False
    try:
        _fernet.decrypt(token.encode("utf-8"))
        return True
    except (InvalidToken, Exception):
        return False
