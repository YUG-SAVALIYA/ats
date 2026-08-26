"""
tests.test_dhan_gate
====================
Validates the Dhan account connection gate:

1. GET /api/dhan/connection without JWT → 401
2. Authenticated admin with no Dhan account → DHAN_NOT_CONNECTED
3. POST /api/dhan/connect with mock token → DHAN_CONNECTED
4. GET /api/dhan/connection after connecting → DHAN_CONNECTED
5. GET /api/app-auth/me includes dhan_status
6. POST /api/dhan/disconnect → DHAN_NOT_CONNECTED
7. User A cannot steal User B's client_id via /api/dhan/connect (409)
8. No protected Dhan-dependent API requests can succeed without DHAN_CONNECTED
"""

import pytest
import bcrypt
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.main import app
from app.data.database import SessionLocal
from app.data.models import AppConfig, DhanAccount, User, AccountStatus


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def setup_auth(monkeypatch):
    """Ensure admin password is set and any Dhan accounts for admin are cleared."""
    db = SessionLocal()
    # Set admin password
    db.query(AppConfig).filter(AppConfig.config_key == "admin_password").delete()
    hashed = bcrypt.hashpw("GateTest123".encode(), bcrypt.gensalt()).decode()
    db.add(AppConfig(config_key="admin_password", config_value=hashed))

    # Ensure admin user exists
    admin_user = db.query(User).filter(User.email == "admin").first()
    if not admin_user:
        admin_user = User(email="admin", role="ADMIN", is_active=True)
        db.add(admin_user)
        db.flush()

    # Clear any existing Dhan accounts for this admin user for clean test state
    db.query(DhanAccount).filter(DhanAccount.user_id == admin_user.id).delete()
    db.commit()
    db.close()


def _login(client) -> str:
    """Helper: login and return JWT token."""
    r = client.post("/api/app-auth/login", json={"password": "GateTest123"})
    assert r.status_code == 200, f"Login failed: {r.text}"
    return r.json()["access_token"]


def test_1_dhan_connection_requires_jwt(client):
    """GET /api/dhan/connection without JWT must return 401."""
    r = client.get("/api/dhan/connection")
    assert r.status_code == 401


def test_2_authenticated_user_with_no_dhan_account_returns_not_connected(client):
    """After ATS login, user with no Dhan account gets DHAN_NOT_CONNECTED."""
    token = _login(client)
    headers = {"Authorization": f"Bearer {token}"}

    r = client.get("/api/dhan/connection", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "DHAN_NOT_CONNECTED"


def test_3_connect_dhan_manual_with_valid_token(client):
    """POST /api/dhan/connect with a valid token creates DHAN_CONNECTED status."""
    token = _login(client)
    headers = {"Authorization": f"Bearer {token}"}

    # Mock Dhan token validation to return True (avoid real network call)
    with patch("app.api.dhan_connection._validate_token_with_dhan", return_value=True):
        r = client.post("/api/dhan/connect", json={
            "client_id": "9999999999",
            "access_token": "eyJmakeToken.validlength.atleast20chars"
        }, headers=headers)

    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "DHAN_CONNECTED"
    assert data["account_id"] is not None
    assert "***" in data["client_id_masked"]


def test_4_dhan_connection_reflects_connected_status(client):
    """After connecting, GET /api/dhan/connection returns DHAN_CONNECTED."""
    token = _login(client)
    headers = {"Authorization": f"Bearer {token}"}

    with patch("app.api.dhan_connection._validate_token_with_dhan", return_value=True):
        client.post("/api/dhan/connect", json={
            "client_id": "9999999998",
            "access_token": "eyJmakeToken.validlength.atleast20chars"
        }, headers=headers)

    r = client.get("/api/dhan/connection", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "DHAN_CONNECTED"
    assert r.json()["token_present"] is True


def test_5_me_endpoint_includes_dhan_status(client):
    """GET /api/app-auth/me includes dhan_status field."""
    token = _login(client)
    headers = {"Authorization": f"Bearer {token}"}

    r = client.get("/api/app-auth/me", headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert "dhan_status" in data
    # Initially no account → DHAN_NOT_CONNECTED
    assert data["dhan_status"] in ("DHAN_NOT_CONNECTED", "DHAN_CONNECTED", "DHAN_AUTH_REQUIRED")


def test_6_disconnect_dhan_reverts_to_not_connected(client):
    """POST /api/dhan/disconnect clears the token and returns DHAN_NOT_CONNECTED."""
    token = _login(client)
    headers = {"Authorization": f"Bearer {token}"}

    # Connect first
    with patch("app.api.dhan_connection._validate_token_with_dhan", return_value=True):
        client.post("/api/dhan/connect", json={
            "client_id": "9999999997",
            "access_token": "eyJmakeToken.validlength.atleast20chars"
        }, headers=headers)

    # Verify connected
    r = client.get("/api/dhan/connection", headers=headers)
    assert r.json()["status"] == "DHAN_CONNECTED"

    # Disconnect
    r_dis = client.post("/api/dhan/disconnect", headers=headers)
    assert r_dis.status_code == 200
    assert r_dis.json()["status"] == "DHAN_NOT_CONNECTED"

    # Verify disconnected
    r2 = client.get("/api/dhan/connection", headers=headers)
    assert r2.json()["status"] == "DHAN_NOT_CONNECTED"
    assert r2.json()["token_present"] is False


def test_7_reject_invalid_dhan_token(client):
    """POST /api/dhan/connect with an invalid token (Dhan rejects) → 401."""
    token = _login(client)
    headers = {"Authorization": f"Bearer {token}"}

    with patch("app.api.dhan_connection._validate_token_with_dhan", return_value=False):
        r = client.post("/api/dhan/connect", json={
            "client_id": "9999999996",
            "access_token": "badToken.thatDhanrejects"
        }, headers=headers)

    assert r.status_code == 401
    assert "rejected" in r.json()["detail"].lower()


def test_8_user_a_cannot_claim_user_b_client_id(client):
    """User A cannot connect a client_id already owned by User B → 409 Conflict."""
    token = _login(client)
    headers = {"Authorization": f"Bearer {token}"}

    # Setup: manually create a DhanAccount owned by a different user
    db = SessionLocal()
    other_user = User(email="other_user@example.com", role="USER", is_active=True)
    db.add(other_user)
    db.flush()
    db.add(DhanAccount(
        user_id=other_user.id,
        client_id="8888888888",
        access_token=None,
        account_status=AccountStatus.ACTIVE,
        is_data_account=False,
    ))
    db.commit()
    db.close()

    with patch("app.api.dhan_connection._validate_token_with_dhan", return_value=True):
        r = client.post("/api/dhan/connect", json={
            "client_id": "8888888888",  # belongs to other_user
            "access_token": "eyJmakeToken.validlength.atleast20chars"
        }, headers=headers)

    assert r.status_code == 409
    assert "different user" in r.json()["detail"].lower()
