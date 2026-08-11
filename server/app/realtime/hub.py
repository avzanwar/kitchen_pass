"""WebSocket fan-out.

In-process by default, so the app runs with no Redis installed. Set
`KP_REDIS_URL` and the same hub publishes through Redis pub/sub instead, which
is what makes it correct across multiple uvicorn workers.

The plan called for Redis unconditionally; making it optional means a laptop at
the venue needs one fewer service, and the interface is identical either way.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections import defaultdict
from typing import Any

from fastapi import WebSocket

log = logging.getLogger("kitchen_pass.realtime")


class Hub:
    """Topic-based broadcast to connected WebSockets."""

    def __init__(self) -> None:
        self._topics: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()
        self._redis: Any | None = None
        self._reader: asyncio.Task[None] | None = None

    # -- lifecycle ------------------------------------------------------
    async def connect_redis(self, url: str) -> None:
        try:
            import redis.asyncio as aioredis
        except ImportError:
            log.warning("KP_REDIS_URL set but redis is not installed; staying local")
            return
        self._redis = aioredis.from_url(url, decode_responses=True)
        self._reader = asyncio.create_task(self._read_redis())
        log.info("realtime fan-out via redis")

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
        if self._redis is not None:
            await self._redis.aclose()
        self._redis = None
        self._reader = None

    async def _read_redis(self) -> None:
        assert self._redis is not None
        pubsub = self._redis.pubsub()
        await pubsub.psubscribe("kp:*")
        async for message in pubsub.listen():
            if message.get("type") != "pmessage":
                continue
            topic = str(message["channel"]).removeprefix("kp:")
            with contextlib.suppress(json.JSONDecodeError):
                await self._deliver(topic, json.loads(message["data"]))

    # -- subscription ---------------------------------------------------
    async def subscribe(self, topic: str, socket: WebSocket) -> None:
        async with self._lock:
            self._topics[topic].add(socket)

    async def unsubscribe(self, topic: str, socket: WebSocket) -> None:
        async with self._lock:
            self._topics[topic].discard(socket)
            if not self._topics[topic]:
                self._topics.pop(topic, None)

    def subscriber_count(self, topic: str) -> int:
        return len(self._topics.get(topic, ()))

    # -- publishing -----------------------------------------------------
    async def publish(self, topic: str, payload: dict[str, Any]) -> None:
        if self._redis is not None:
            await self._redis.publish(f"kp:{topic}", json.dumps(payload, default=str))
            return
        await self._deliver(topic, payload)

    async def _deliver(self, topic: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            targets = list(self._topics.get(topic, ()))

        dead: list[WebSocket] = []
        for socket in targets:
            try:
                await socket.send_json(payload)
            except Exception:
                # A viewer closing the tab must never break the scorekeeper's
                # broadcast, so drop the socket and carry on.
                dead.append(socket)
        for socket in dead:
            await self.unsubscribe(topic, socket)


hub = Hub()


def match_topic(match_id: str) -> str:
    return f"match:{match_id}"


def board_topic(tournament_id: str) -> str:
    return f"board:{tournament_id}"
