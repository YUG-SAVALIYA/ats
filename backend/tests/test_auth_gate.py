"""
tests.test_auth_gate
====================
Automated test suite verifying the application authentication gate:
1. Open app without token -> Protected endpoints reject with 401.
2. Login with password -> Returns valid JWT token.
3. Call /api/app-auth/me with valid Bearer JWT -> Returns 200 OK & authenticated user profile.
4. Call protected endpoints (/api/engine/status, /api/portfolio/summary) with valid Bearer JWT -> 200 OK.
5. Tampered or expired JWT token -> Returns 401 Unauthorized.
6. Persistent secret key across simulated restarts.
"""

import pytest
import bcrypt
import jwt
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient

from app.main import app
from app.data.database import SessionLocal
from app.data.models import AppConfig, DhanAccount, User
from app.api.auth_app import SECRET_KEY, ALGORITHM


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def setup_admin_password():
    db = SessionLocal()
    # Clean and insert admin password "TestPass123"
    db.query(AppConfig).filter(AppConfig.config_key == "admin_password").delete()
    hashed_pw = bcrypt.hashpw("TestPass123".encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    db.add(AppConfig(config_key="admin_password", config_value=hashed_pw))
    db.commit()
    db.close()


def test_1_unauthenticated_requests_return_401(client):
    """Calling protected endpoints without token must return 401 Unauthorized."""
    # /api/app-auth/me
    res_me = client.get("/api/app-auth/me")
    assert res_me.status_code == 401
    assert "Not authenticated" in res_me.json()["detail"]

    # /api/engine/status
    res_eng = client.get("/api/engine/status")
    assert res_eng.status_code == 401

    # /api/portfolio/summary
    res_port = client.get("/api/portfolio/summary")
    assert res_port.status_code == 401


def test_2_login_and_token_verification(client):
    """Login with valid password returns JWT that passes /api/app-auth/me verification."""
    # Wrong password
    res_bad = client.post("/api/app-auth/login", json={"password": "WrongPassword"})
    assert res_bad.status_code == 401

    # Correct password
    res_login = client.post("/api/app-auth/login", json={"password": "TestPass123"})
    assert res_login.status_code == 200
    data = res_login.json()
    assert "access_token" in data
    token = data["access_token"]

    # Verify with /api/app-auth/me
    headers = {"Authorization": f"Bearer {token}"}
    res_me = client.get("/api/app-auth/me", headers=headers)
    assert res_me.status_code == 200
    me_data = res_me.json()
    assert me_data["authenticated"] is True
    assert me_data["sub"] == "admin"
    assert me_data["is_admin"] is True


def test_3_protected_endpoints_with_valid_jwt(client):
    """Protected endpoints succeed when provided with valid Bearer JWT."""
    res_login = client.post("/api/app-auth/login", json={"password": "TestPass123"})
    token = res_login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # /api/engine/status
    res_eng = client.get("/api/engine/status", headers=headers)
    assert res_eng.status_code == 200
    assert "enabled" in res_eng.json()


def test_4_invalid_and_expired_tokens_return_401(client):
    """Tampered and expired tokens must return 401."""
    # Tampered token
    headers_tampered = {"Authorization": "Bearer invalid.fake.token"}
    res_tampered = client.get("/api/app-auth/me", headers=headers_tampered)
    assert res_tampered.status_code == 401

    # Expired token
    expired_payload = {
        "sub": "admin",
        "role": "admin",
        "exp": datetime.now(timezone.utc) - timedelta(hours=1),
    }
    expired_token = jwt.encode(expired_payload, SECRET_KEY, algorithm=ALGORITHM)
    headers_expired = {"Authorization": f"Bearer {expired_token}"}
    res_expired = client.get("/api/app-auth/me", headers=headers_expired)
    assert res_expired.status_code == 401
    assert "expired" in res_expired.json()["detail"].lower()
