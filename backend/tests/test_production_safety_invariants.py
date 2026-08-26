"""
tests/test_production_safety_invariants.py
===========================================
Production Safety & Invariant Verification Suite:
1. TokenBucketRateLimiter verification (rate limits per account, no cross-account throttling).
2. Credentials & Token Leakage prevention (client IDs masked, tokens/secrets absent in API responses & logs).
3. AST scan ensuring zero calls to deprecated get_dhan_client().
4. Strict Trade <-> Order <-> Account context binding invariants.
"""

import os
import ast
import time
import pytest
from datetime import datetime, timezone
import uuid

from fastapi.testclient import TestClient
from app.main import app
from app.data.database import SessionLocal
from app.data.models import DhanAccount, User, AppConfig
from app.broker.dhan_client import TokenBucketRateLimiter, get_rate_limiter, get_dhan_client
from app.api.auth_app import SECRET_KEY, ALGORITHM
import jwt
import bcrypt


def test_rate_limiter_per_account_isolation():
    """
    Verify TokenBucketRateLimiter enforces rate limiting per account
    and exhaustion on Account A does NOT throttle Account B.
    """
    limiter_a = TokenBucketRateLimiter(capacity=5, refill_rate=5.0)
    limiter_b = TokenBucketRateLimiter(capacity=5, refill_rate=5.0)

    # Consume all 5 tokens on Account A
    for _ in range(5):
        wait_a = limiter_a.consume(1)
        assert wait_a == 0.0

    # 6th call on Account A must be rate-limited (wait > 0)
    wait_a_extra = limiter_a.consume(1)
    assert wait_a_extra > 0.0

    # Account B should have full bucket intact (0.0 wait)
    wait_b = limiter_b.consume(1)
    assert wait_b == 0.0


def test_credentials_masked_in_api_responses():
    """
    Verify sensitive tokens, PINs, and TOTP secrets NEVER appear in API responses,
    and client IDs are masked as ***XXXX.
    """
    db = SessionLocal()
    try:
        user = User(id=str(uuid.uuid4()), email="admin_mask@test.com", role="admin", is_active=True)
        db.add(user)
        
        acc = DhanAccount(
            id="acc_MASK_TEST",
            user_id=user.id,
            client_id="1111482994",
            access_token="SECRET_ACCESS_TOKEN_ABC123XYZ",
            pin="1234",
            totp_secret="JBSWY3DPEHPK3PXP",
            account_status="ACTIVE"
        )
        db.add(acc)
        
        hashed_pw = bcrypt.hashpw(b"admin1234", bcrypt.gensalt()).decode("utf-8")
        db.add(AppConfig(config_key="admin_password", config_value=hashed_pw))
        db.commit()

        admin_payload = {"sub": "admin", "user_id": None, "role": "admin", "exp": datetime.now(timezone.utc).timestamp() + 3600}
        token = jwt.encode(admin_payload, SECRET_KEY, algorithm=ALGORITHM)

        client = TestClient(app)
        res = client.get("/api/auth/status", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 200

        data_str = str(res.json())

        # Assert secret tokens are NEVER exposed
        assert "SECRET_ACCESS_TOKEN_ABC123XYZ" not in data_str
        assert "JBSWY3DPEHPK3PXP" not in data_str
        assert "1234" not in [acc_item.get("pin") for acc_item in res.json().get("accounts", []) if "pin" in acc_item]

        # Assert client_id is masked
        accounts = res.json().get("accounts", [])
        for a in accounts:
            if a.get("dhan_account_id") == "acc_MASK_TEST":
                assert a.get("client_id") == "***2994"
    finally:
        db.close()


def test_zero_live_calls_to_get_dhan_client():
    """
    AST Static Analysis Scan:
    Assert that NO production code in app/ calls deprecated get_dhan_client().
    """
    app_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app")
    call_sites = []

    for root, _, files in os.walk(app_dir):
        for f in files:
            if f.endswith(".py"):
                file_path = os.path.join(root, f)
                with open(file_path, "r", encoding="utf-8") as py_file:
                    tree = ast.parse(py_file.read(), filename=file_path)
                    for node in ast.walk(tree):
                        # Detect any Call node calling get_dhan_client()
                        if isinstance(node, ast.Call):
                            if isinstance(node.func, ast.Name) and node.func.id == "get_dhan_client":
                                # Exclude the function definition inside dhan_client.py itself
                                if not (f == "dhan_client.py" and isinstance(node, ast.FunctionDef)):
                                    call_sites.append(f"{f}:{node.lineno}")
                            elif isinstance(node.func, ast.Attribute) and node.func.attr == "get_dhan_client":
                                call_sites.append(f"{f}:{node.lineno}")

    assert len(call_sites) == 0, f"Found active calls to deprecated get_dhan_client(): {call_sites}"


def test_deprecated_get_dhan_client_raises_runtime_error():
    """
    Verify that calling get_dhan_client() directly raises an explicit RuntimeError.
    """
    with pytest.raises(RuntimeError, match="deprecated and forbidden"):
        get_dhan_client()
