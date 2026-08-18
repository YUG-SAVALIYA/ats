import os
import sys

# Add backend root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from app.main import app
from app.api.auth_app import get_current_user

# Bypass authentication
app.dependency_overrides[get_current_user] = lambda: {"user_id": "test_user"}

client = TestClient(app)

def test_buy():
    print("=========================================================")
    print("  TESTING MANUAL BUY API ENTRY                           ")
    print("=========================================================")

    payload = {
        "security_id": "14366", # Example: IDEA
        "trading_symbol": "IDEA",
        "quantity": 2,
        "allocated_capital": 20.0,
        "product_type": "AUTO"
    }

    print("Target Endpoint: POST /api/trades/manual-entry")
    print(f"Payload: {payload}")
    
    # Send the request
    response = client.post("/api/trades/manual-entry", json=payload)
    
    print("\n--- Response ---")
    print(f"Status Code: {response.status_code}")
    print(f"JSON Body: {response.json()}")

if __name__ == "__main__":
    test_buy()
