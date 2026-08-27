"""
dhan/orders.py — Direct Dhan API Orders Endpoint Client
======================================================
Dhan-specific low-level order placement, cancellation, and retrieval API helpers.
"""

from __future__ import annotations

import logging
from typing import Dict, Any, Optional

from dhan.client import get_dhan_client
from dhan.endpoints import ORDERS_URL, get_order_by_id_url

logger = logging.getLogger("ats.dhan.orders")

_DHAN_ORDERS_URL = ORDERS_URL


def place_dhan_order(payload: dict) -> Any:
    """Sends POST /v2/orders request to Dhan API."""
    client = get_dhan_client()
    return client.execute_v2_post(ORDERS_URL, payload)


def cancel_dhan_order(dhan_order_id: str) -> Any:
    """Sends DELETE /v2/orders/{dhan_order_id} request to Dhan API."""
    client = get_dhan_client()
    url = get_order_by_id_url(dhan_order_id)
    return client.execute_v2_delete(url)


def get_dhan_order_by_id(dhan_order_id: str) -> Any:
    """Sends GET /v2/orders/{dhan_order_id} request to Dhan API."""
    client = get_dhan_client()
    url = get_order_by_id_url(dhan_order_id)
    return client.execute_v2_get(url)
