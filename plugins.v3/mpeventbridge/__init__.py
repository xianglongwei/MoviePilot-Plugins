"""Forward MoviePilot events without coupling media work to hook delivery."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import posixpath
import threading
import time
from collections import deque
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.schemas.types import EventType
from app.sdk.events import Event, eventmanager
from app.sdk.logging import logger
from app.sdk.plugin import _PluginBase


DEFAULTS = {
    "enabled": False,
    "endpoint": "http://192.168.10.25:7811",
    "secret": "",
    "debounce_seconds": 45,
}
RETRY_DELAYS = (1, 5, 30)


def field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def event_key(event: str, target: str, episodes: list[dict]) -> str:
    material = f"{event}|{target}|".encode("utf-8") + canonical_json(episodes)
    return "mp-" + hashlib.sha1(material).hexdigest()


def signature(secret: str, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(
        secret.encode("utf-8"), timestamp.encode("ascii") + b"." + body, hashlib.sha256
    ).hexdigest()


def normalize_path(value: Any) -> str | None:
    if not value:
        return None
    path = str(value)
    if not path.startswith("/"):
        return None
    path = posixpath.normpath("/" + path.lstrip("/"))
    if path == "/115" or path.startswith("/115/"):
        path = path[4:] or "/"
    return path


def meta_episodes(meta: Any) -> list[dict[str, int]]:
    season, episodes = field(meta, "season"), field(meta, "episode")
    if season is None or episodes is None:
        return []
    if not isinstance(episodes, (list, tuple, set, frozenset)):
        episodes = [episodes]
    try:
        season = int(season)
        numbers = {int(episode) for episode in episodes}
    except (TypeError, ValueError, OverflowError):
        return []
    return [{"season": season, "episode": episode} for episode in sorted(numbers)
            if season >= 0 and episode > 0]


def normalize_event(event: str, payload: Any) -> dict | None:
    transfer = field(payload, "transferinfo")
    target = normalize_path(field(transfer, "target_dir"))
    if not target:
        # The v3 refresh reference exposes the destination as a FileItem instead.
        target = normalize_path(field(field(transfer, "target_diritem"), "path"))
    if not target:
        item = field(payload, "fileitem")
        target = normalize_path(field(item, "path"))
        if target and field(item, "type") != "dir":
            target = str(PurePosixPath(target).parent)
    if not target:
        paths = [normalize_path(path) for path in (field(payload, "file_list", []) or [])]
        if paths and all(paths):
            target = posixpath.commonpath([str(PurePosixPath(path).parent) for path in paths])
    if not target:
        return None
    episodes = meta_episodes(field(payload, "meta"))
    if not episodes:
        pairs = {(entry["season"], entry["episode"])
                 for context in (field(payload, "file_contexts", []) or [])
                 for entry in meta_episodes(field(context, "meta"))}
        episodes = [{"season": season, "episode": episode} for season, episode in sorted(pairs)]
    occurred_at = field(payload, "occurred_at")
    try:
        occurred_at = float(occurred_at)
        if not math.isfinite(occurred_at):
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        occurred_at = time.time()
    return {
        "event": event,
        "target": target,
        "episodes": episodes,
        "idempotency_key": field(payload, "idempotency_key") or event_key(event, target, episodes),
        "occurred_at": occurred_at,
    }


def merge_event(previous: dict, incoming: dict) -> dict:
    pairs = {(item["season"], item["episode"])
             for item in previous["episodes"] + incoming["episodes"]}
    merged = dict(incoming)
    merged["episodes"] = [{"season": season, "episode": episode} for season, episode in sorted(pairs)]
    # A merged notification has new semantics, so do not reuse one constituent's host key.
    if previous != incoming:
        merged["idempotency_key"] = event_key(merged["event"], merged["target"], merged["episodes"])
    merged["occurred_at"] = max(previous["occurred_at"], incoming["occurred_at"])
    return merged


class MpEventBridge(_PluginBase):
    plugin_name = "事件桥"
    plugin_desc = "将整理和刮削事件异步转发给 p115-302，按目录去抖合并并签名"
    plugin_icon = "https://raw.githubusercontent.com/xianglongwei/MoviePilot-Plugins/main/icons/speed.png"
    plugin_version = "0.1.0"
    plugin_author = "wei"
    author_url = "https://github.com/xianglongwei/MoviePilot-Plugins"
    plugin_config_prefix = "mpeventbridge_"
    plugin_order = 30
    auth_level = 1

    def __init__(self):
        super().__init__()
        self._enabled = False
        self._config = dict(DEFAULTS)
        self._config_error = None
        self._generation = 0
        self._loop = None
        self._thread = None
        self._owned_loop = False
        self._task = None
        self._tasks = set()
        self._queue = None
        self._records = deque(maxlen=20)
        self._counts = dict(received=0, merged=0, sent_success=0, failed=0)

    def _ensure_loop(self):
        if self._loop and not self._loop.is_closed():
            return
        try:
            self._loop = asyncio.get_running_loop()
            self._owned_loop = False
        except RuntimeError:
            # MP may initialize synchronous plugins off-loop; never run an event loop in its caller.
            self._loop = asyncio.new_event_loop()
            self._owned_loop = True
            loop = self._loop

            def run():
                asyncio.set_event_loop(loop)
                try:
                    loop.run_forever()
                finally:
                    loop.close()

            self._thread = threading.Thread(target=run, name="mpeventbridge", daemon=True)
            self._thread.start()

    def init_plugin(self, config: dict | None = None) -> None:
        values = dict(DEFAULTS)
        values.update((self.get_config() or {}) if config is None else config)
        try:
            delay = float(values["debounce_seconds"])
            if not math.isfinite(delay) or delay < 0:
                raise ValueError
            values["debounce_seconds"] = delay
        except (TypeError, ValueError, OverflowError):
            values["debounce_seconds"] = 45
        values["endpoint"] = str(values["endpoint"] or DEFAULTS["endpoint"]).strip().rstrip("/")
        values["secret"] = str(values["secret"] or "")
        self.update_config(values)
        self._config = values
        self._config_error = None
        try:
            endpoint = urlsplit(values["endpoint"])
            valid_endpoint = (endpoint.scheme in ("http", "https") and endpoint.hostname
                              and not (endpoint.path or endpoint.query or endpoint.fragment))
            endpoint.port
        except ValueError:
            valid_endpoint = False
        if values["enabled"] and (not values["secret"] or not valid_endpoint):
            self._config_error = "invalid_config"
            logger.warning("事件桥未启动：请填写签名密钥及有效的接收端地址")
        self._enabled = bool(values["enabled"] and not self._config_error)
        self._generation += 1
        generation = self._generation
        if self._enabled or self._loop:
            self._ensure_loop()
            self._loop.call_soon_threadsafe(self._restart, generation, dict(values))

    def _restart(self, generation: int, config: dict) -> None:
        if generation != self._generation:
            return
        previous = tuple(self._tasks)
        for task in previous:
            task.cancel()
        self._queue = asyncio.Queue() if self._enabled else None
        self._task = self._loop.create_task(self._run(previous, self._queue, config, generation))
        self._tasks.add(self._task)
        self._task.add_done_callback(self._tasks.discard)

    async def _run(self, previous, queue, config, generation):
        if previous:
            await asyncio.gather(*previous, return_exceptions=True)
        if queue is None or not self._enabled or generation != self._generation:
            return
        try:
            async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
                await self._worker(queue, client, config, generation)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"事件桥后台任务终止：{type(exc).__name__}")

    def _enqueue(self, generation: int, body: dict) -> None:
        if generation == self._generation and self._enabled and self._queue is not None:
            self._counts["received"] += 1
            self._queue.put_nowait((self._loop.time(), body))

    def _accept(self, event: str, payload: Any) -> dict | None:
        if not self._enabled or self._loop is None:
            return None
        body = normalize_event(event, payload)
        if body:
            # Event delivery may use host threads; Queue mutations belong to its owning loop.
            self._loop.call_soon_threadsafe(self._enqueue, self._generation, body)
        return body

    @eventmanager.register(EventType.TransferComplete)
    def on_transfer_complete(self, event: Event) -> None:
        self._accept("transfer.complete", event.event_data)

    @eventmanager.register(EventType.MetadataScrape)
    def on_metadata_scrape(self, event: Event) -> None:
        self._accept("metadata.scrape", event.event_data)

    def _merge_pending(self, pending, queued, delay):
        arrived, body = queued
        target = body["target"]
        if target in pending:
            self._counts["merged"] += 1
            body = merge_event(pending[target][1], body)
        pending[target] = (arrived + delay, body)

    async def _worker(self, queue, client, config, generation):
        pending = {}
        delay = config["debounce_seconds"]
        while True:
            if not self._enabled or generation != self._generation:
                return
            # Drain arrivals before choosing due targets, including arrivals during HTTP retries.
            while not queue.empty():
                self._merge_pending(pending, queue.get_nowait(), delay)
                queue.task_done()
            now = self._loop.time()
            due = [target for target, (deadline, _) in pending.items() if deadline <= now]
            if due:
                target = min(due, key=lambda item: pending[item][0])
                _, body = pending.pop(target)
                await self._send(client, body, config, generation)
                continue
            timeout = max(0, min(entry[0] for entry in pending.values()) - now) if pending else None
            try:
                queued = await asyncio.wait_for(queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                continue
            self._merge_pending(pending, queued, delay)
            queue.task_done()

    async def _send(self, client, body, config, generation=None):
        raw_body = canonical_json(body)
        status, result = None, "request_failed"
        for attempt in range(len(RETRY_DELAYS) + 1):
            # A settings change can precede its scheduled cancellation on another thread.
            if not self._enabled or (generation is not None and generation != self._generation):
                return
            status = None
            try:
                timestamp = str(int(time.time()))
                response = await client.post(
                    config["endpoint"] + "/api/hooks/mp",
                    content=raw_body,
                    headers={"Content-Type": "application/json", "X-MP-Timestamp": timestamp,
                             "X-MP-Signature": signature(config["secret"], timestamp, raw_body)},
                )
                status = response.status_code
                reply = response.json()
                if status == 200 and isinstance(reply, dict) and reply.get("ok") is True:
                    self._counts["sent_success"] += 1
                    self._record(body, status, str(reply.get("reason", "accepted")))
                    return
                result = str(reply.get("reason", "rejected")) if isinstance(reply, dict) else "invalid_response"
            except (httpx.HTTPError, ValueError) as exc:
                result = type(exc).__name__
            if attempt < len(RETRY_DELAYS):
                await asyncio.sleep(RETRY_DELAYS[attempt])
        self._counts["failed"] += 1
        self._record(body, status, result)
        logger.warning(f"事件桥发送失败，已丢弃：{body['event']} {body['target']}，状态 {status}，结果 {result}")

    def _record(self, body, status, result):
        self._records.append({"time": time.time(), "event": body["event"], "target": body["target"],
                              "http_status": status, "result": result})

    def get_state(self) -> bool:
        return self._enabled

    def get_api(self) -> list[dict]:
        return [
            {"path": "/state", "endpoint": self.api_state, "methods": ["GET"], "auth": "bear",
             "summary": "查看事件桥状态", "description": "最近二十条发送记录与累计计数"},
            {"path": "/test", "endpoint": self.api_test, "methods": ["POST"], "auth": "bear",
             "summary": "测试事件桥", "description": "合成事件经过归一化、队列、去抖、签名和发送"},
        ]

    async def _snapshot(self):
        return {"enabled": self._enabled, "config_error": self._config_error,
                "records": list(self._records), "counts": dict(self._counts)}

    async def api_state(self) -> dict:
        if self._loop and self._loop.is_running() and self._loop is not asyncio.get_running_loop():
            return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self._snapshot(), self._loop))
        return await self._snapshot()

    async def api_test(self, target: str = "/115/link/__mp_event_bridge_test__") -> dict:
        # A query parameter lets operators choose a mapped directory without adding wire fields.
        body = self._accept("metadata.scrape", {"fileitem": {"path": target, "type": "dir"},
                                               "occurred_at": time.time(),
                                               "idempotency_key": "mp-test-" + str(time.time_ns())})
        return {"ok": body is not None, "queued": int(body is not None),
                "reason": "scheduled" if body else ("disabled" if not self._enabled else "invalid_target"),
                "idempotency_key": body["idempotency_key"] if body else None}

    def get_form(self) -> tuple[list[dict], dict]:
        controls = [
            ("VSwitch", {"model": "enabled", "label": "启用事件桥"}),
            ("VTextField", {"model": "endpoint", "label": "接收端地址", "placeholder": DEFAULTS["endpoint"]}),
            ("VTextField", {"model": "secret", "label": "签名密钥", "type": "password"}),
            ("VTextField", {"model": "debounce_seconds", "label": "去抖秒数", "type": "number", "min": 0}),
        ]
        return [{"component": "VForm", "content": [
            {"component": component, "props": props} for component, props in controls
        ]}], dict(DEFAULTS)

    def get_page(self) -> list[dict]:
        return []

    @staticmethod
    def get_command() -> list[dict]:
        return []

    def close(self):
        return self._close(stop_loop=True)

    def _close(self, stop_loop: bool):
        self._enabled = False
        self._generation += 1
        generation = self._generation
        if not self._loop or self._loop.is_closed():
            return None
        loop = self._loop

        async def shutdown():
            if generation != self._generation:
                return
            tasks = tuple(self._tasks)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if generation == self._generation:
                self._task = None
                self._queue = None

        def stop_if_current():
            if generation == self._generation:
                loop.stop()

        # Schedule cleanup even when MP calls its synchronous close hook without awaiting it.
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is loop:
            return loop.create_task(shutdown())
        future = asyncio.run_coroutine_threadsafe(shutdown(), loop)
        if self._owned_loop and stop_loop:
            future.add_done_callback(lambda _: loop.call_soon_threadsafe(stop_if_current))
        return asyncio.wrap_future(future) if current else future

    def stop_service(self):
        # Hosts can stop services before reinitializing the same instance on a settings save.
        return self._close(stop_loop=False)
