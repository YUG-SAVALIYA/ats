"""
dhan/websocket.py — Persistent Dhan WebSocket Feed v2 Client
============================================================
Maintains a resilient WebSocket connection to `wss://api-feed.dhan.co`.
Handles binary and text frame parsing, automatic reconnect with exponential back-off,
and tick dispatching to registered callbacks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Callable, Dict, List, Optional, Set, Any
from dhan.endpoints import DHAN_WS_BASE_URL, get_websocket_feed_url

logger = logging.getLogger("ats.dhan.websocket")

_RECONNECT_BASE_DELAY = 2.0
_RECONNECT_MAX_DELAY = 60.0
_WS_BASE_URL = DHAN_WS_BASE_URL


class MarketFeedManager:
    """Manages a persistent WebSocket connection to the Dhan market feed v2."""

    def __init__(
        self,
        client_id: str,
        get_token_fn: Callable[[], Optional[str]],
    ):
        self._client_id = client_id
        self._get_token = get_token_fn

        self._subscribed_ids: Set[str] = set()
        self._callbacks: List[Callable] = []
        self._ws = None
        self._running = False
        self._lock = asyncio.Lock()
        self._connect_task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

    def register_tick_callback(self, callback: Callable) -> None:
        """Register a coroutine or callable: callback(security_id: str, ltp: float)."""
        if callback not in self._callbacks:
            self._callbacks.append(callback)

    async def start(self) -> None:
        """Start the persistent connection loop."""
        if self._running:
            return
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        self._running = True
        self._connect_task = asyncio.create_task(self._connection_loop())
        logger.info("[WS] Market feed manager started")

    async def stop(self) -> None:
        """Cleanly shut down the WebSocket connection."""
        self._running = False
        if self._connect_task:
            self._connect_task.cancel()
            try:
                await self._connect_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        logger.info("[WS] Market feed manager stopped")

    async def restart(self) -> None:
        """Seamlessly reconnects the WebSocket to load fresh access token and resume live subscriptions."""
        logger.info("[WS] Restarting Market Feed WebSocket with fresh token & subscriptions...")
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        if not self._running and self._subscribed_ids:
            await self.start()

    async def subscribe(self, security_ids: List[str]) -> None:
        """Subscribe to live ticks for the given security IDs and persist to DB."""
        from database.database import SessionLocal
        from database.models import ActiveSubscription

        async with self._lock:
            new_ids = [sid for sid in security_ids if sid not in self._subscribed_ids]
            if not new_ids:
                return
            self._subscribed_ids.update(new_ids)

            db = SessionLocal()
            try:
                for sid in new_ids:
                    existing = db.query(ActiveSubscription).filter_by(security_id=sid).first()
                    if not existing:
                        db.add(ActiveSubscription(security_id=sid))
                db.commit()
            except Exception as e:
                logger.error(f"[WS] Error saving subscriptions to DB: {e}")
                db.rollback()
            finally:
                db.close()

        if not self._running:
            logger.info(f"[WS] Auto-starting market feed to subscribe to {new_ids}")
            await self.start()
        elif self._ws:
            await self._send_subscription(list(self._subscribed_ids))
            logger.info(f"[WS] Subscribed to {new_ids}")

    async def unsubscribe(self, security_ids: List[str]) -> None:
        """Unsubscribe from ticks for the given security IDs and remove from DB."""
        from database.database import SessionLocal
        from database.models import ActiveSubscription

        async with self._lock:
            removed_ids = []
            for sid in security_ids:
                if sid in self._subscribed_ids:
                    self._subscribed_ids.discard(sid)
                    removed_ids.append(sid)
            
            if removed_ids:
                db = SessionLocal()
                try:
                    for sid in removed_ids:
                        db.query(ActiveSubscription).filter_by(security_id=sid).delete()
                    db.commit()
                except Exception as e:
                    logger.error(f"[WS] Error removing subscriptions from DB: {e}")
                    db.rollback()
                finally:
                    db.close()

        if not self._subscribed_ids:
            logger.info("[WS] No active subscriptions remaining. Shutting down market feed WebSocket.")
            await self.stop()
        elif self._ws:
            await self._send_subscription(list(self._subscribed_ids))

    def is_connected(self) -> bool:
        return self._ws is not None

    def get_subscribed_ids(self) -> Set[str]:
        return set(self._subscribed_ids)

    async def _connection_loop(self) -> None:
        """Infinite reconnect loop with exponential back-off."""
        delay = _RECONNECT_BASE_DELAY
        while self._running:
            try:
                start_time = time.time()
                await self._connect_and_run()
                if time.time() - start_time > 30.0:
                    delay = _RECONNECT_BASE_DELAY
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(f"[WS] Connection lost: {exc}. Reconnecting in {delay:.0f}s…")
            
            self._ws = None
            if self._running:
                logger.info(f"[WS] Waiting {delay:.0f}s before reconnecting...")
                await asyncio.sleep(delay)
                delay = min(delay * 2, _RECONNECT_MAX_DELAY)

    async def _connect_and_run(self) -> None:
        """Open a WebSocket, subscribe, and read ticks until disconnect."""
        try:
            import websockets
        except ImportError:
            raise RuntimeError(
                "The 'websockets' package is required. Run: pip install websockets"
            )

        token = self._get_token()
        if not token:
            raise RuntimeError("No valid Dhan auth token available for WebSocket connection")

        ws_url = get_websocket_feed_url(token, self._client_id, 2)
        logger.info(f"[WS] Connecting to {_WS_BASE_URL} (v2)…")
        async with websockets.connect(
            ws_url,
            ping_interval=None,
            ping_timeout=None,
        ) as ws:
            self._ws = ws
            logger.info("[WS] Connected to Dhan Market Feed v2")

            if self._subscribed_ids:
                await self._send_subscription(list(self._subscribed_ids), ws=ws)
                logger.info(f"[WS] Re-subscribed to {len(self._subscribed_ids)} securities after reconnect")
                for cb in self._callbacks:
                    try:
                        if asyncio.iscoroutinefunction(cb):
                            asyncio.create_task(cb("__reconnect__", 0.0))
                        else:
                            cb("__reconnect__", 0.0)
                    except Exception:
                        pass

            async for raw in ws:
                if not self._running:
                    break
                try:
                    await self._parse_and_dispatch(raw)
                except Exception as exc:
                    logger.debug(f"[WS] Tick processing notice: {exc}")

        self._ws = None

    async def _send_subscription(
        self,
        security_ids: List[str],
        ws=None,
    ) -> None:
        """Send a subscription request to Dhan market feed."""
        target_ws = ws or self._ws
        if not target_ws:
            return

        instrument_list = [
            {"ExchangeSegment": "NSE_EQ", "SecurityId": str(sid)}
            for sid in security_ids
        ]
        payload = {
            "RequestCode": 15,
            "InstrumentCount": len(instrument_list),
            "InstrumentList": instrument_list,
        }
        try:
            await target_ws.send(json.dumps(payload, separators=(',', ':')))
        except Exception as exc:
            logger.warning(f"[WS] Failed to send subscription: {exc}")

    async def _parse_and_dispatch(self, raw: Any) -> None:
        """Parse binary or text frame and dispatch tick to registered callbacks."""
        import struct

        security_id: Optional[str] = None
        ltp: float = 0.0

        if isinstance(raw, bytes):
            if len(raw) >= 8:
                try:
                    resp_code, msg_len, exchange_seg, sec_id_int = struct.unpack("<BHBI", raw[:8])
                    security_id = str(sec_id_int)
                    if len(raw) >= 12:
                        ltp = round(float(struct.unpack("<f", raw[8:12])[0]), 2)
                except Exception:
                    pass
        elif isinstance(raw, str):
            try:
                msg = json.loads(raw)
                if isinstance(msg, dict):
                    security_id = str(msg.get("securityId") or msg.get("SecurityId") or "").strip() or None
                    ltp = float(msg.get("LTP") or msg.get("lastTradedPrice") or msg.get("last_price") or 0.0)
            except Exception:
                pass

        if not security_id or ltp <= 0.0:
            return

        for cb in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    asyncio.create_task(cb(security_id, ltp))
                else:
                    cb(security_id, ltp)
            except Exception as exc:
                logger.error(f"[WS] Callback error for sec {security_id}: {exc}")


_manager_instance: Optional[MarketFeedManager] = None


def get_market_feed_manager() -> Optional[MarketFeedManager]:
    """Return the running MarketFeedManager, or None if not started."""
    return _manager_instance


def init_market_feed_manager(client_id: str, get_token_fn: Callable) -> MarketFeedManager:
    """Create and return the singleton MarketFeedManager."""
    global _manager_instance
    _manager_instance = MarketFeedManager(client_id=client_id, get_token_fn=get_token_fn)
    return _manager_instance


async def restart_market_feed_manager() -> None:
    """Restarts the global MarketFeedManager instance with fresh credentials."""
    mgr = get_market_feed_manager()
    if mgr:
        await mgr.restart()


def trigger_market_feed_reconnect() -> None:
    """Thread-safe non-blocking trigger to reconnect MarketFeed WebSocket immediately with fresh token."""
    mgr = get_market_feed_manager()
    if not mgr:
        return
    try:
        loop = getattr(mgr, "_loop", None)
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(mgr.restart(), loop)
        else:
            try:
                cur_loop = asyncio.get_running_loop()
                if cur_loop.is_running():
                    cur_loop.create_task(mgr.restart())
            except RuntimeError:
                pass
    except Exception as exc:
        logger.warning(f"[WS] Failed to trigger market feed reconnect: {exc}")

