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

def test_full_exit():
    print("=========================================================")
    print("  TESTING MANUAL FULL EXIT API (By Security)             ")
    print("=========================================================")

    payload = {
        "security_id": "14366", # Example: IDEA
        "quantity": 1 # Sell the remaining 1 share
    }

    print("Target Endpoint: POST /api/trades/exit-by-security")
    print(f"Payload: {payload}")
    
    # Send the request
    response = client.post("/api/trades/exit-by-security", json=payload)
    
    print("\n--- Response ---")
    print(f"Status Code: {response.status_code}")
    print(f"JSON Body: {response.json()}")

if __name__ == "__main__":
    test_full_exit()
