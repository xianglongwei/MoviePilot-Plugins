"""Exercise the bridge with host stubs and in-memory HTTP; no network is used."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import threading
import types
import unittest
from enum import Enum
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx


ROOT = Path(__file__).resolve().parents[3]


class FakeBase:
    def __init__(self):
        self.saved_config = {}

    def get_config(self):
        return dict(self.saved_config)

    def update_config(self, config):
        self.saved_config = dict(config)


class FakeEventType(Enum):
    TransferComplete = "transfer.complete"
    MetadataScrape = "metadata.scrape"


class FakeEventManager:
    def __init__(self):
        self.handlers = {}

    def register(self, event_type):
        def decorator(handler):
            self.handlers[event_type] = handler
            return handler
        return decorator


def load_plugin():
    modules = {name: types.ModuleType(name) for name in (
        "app", "app.schemas", "app.schemas.types", "app.sdk", "app.sdk.plugin",
        "app.sdk.events", "app.sdk.logging",
    )}
    modules["app.sdk.plugin"]._PluginBase = FakeBase
    modules["app.schemas.types"].EventType = FakeEventType
    modules["app.sdk.events"].Event = types.SimpleNamespace
    modules["app.sdk.events"].eventmanager = FakeEventManager()
    modules["app.sdk.logging"].logger = Mock()
    spec = importlib.util.spec_from_file_location(
        "mpeventbridge_contract", ROOT / "plugins.v3/mpeventbridge/__init__.py",
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


bridge = load_plugin()


def payload(episode=1, target="/115/link/电视剧/剧名/Season 1", **extra):
    return {"transferinfo": {"target_dir": target}, "meta": {"season": 1, "episode": episode}, **extra}


class FakeClient:
    def __init__(self, outcomes=()):
        self.outcomes = list(outcomes)
        self.calls = []
        self.valid_requests = []
        self.closed = False
        self.responded = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        result = self.outcomes.pop(0) if self.outcomes else httpx.Response(
            200, json={"ok": True, "queued": 1, "reason": "scheduled"},
        )
        if isinstance(result, Exception):
            raise result
        self.valid_requests.append((url, kwargs))
        self.responded.set()
        return result


class NormalizationTest(unittest.TestCase):
    def test_transfer_dict_contract_and_priority(self):
        body = bridge.normalize_event("transfer.complete", payload(
            10, idempotency_key="host-key", occurred_at=1793016921,
            fileitem={"path": "/source/wrong.mkv", "type": "file"},
            file_contexts=[{"meta": {"season": 2, "episode": 7}}],
        ))
        self.assertEqual(body, {
            "event": "transfer.complete", "target": "/link/电视剧/剧名/Season 1",
            "episodes": [{"season": 1, "episode": 10}],
            "idempotency_key": "host-key", "occurred_at": 1793016921.0,
        })

    def test_scrape_object_and_file_contexts(self):
        ns = types.SimpleNamespace
        body = bridge.normalize_event("metadata.scrape", ns(
            fileitem=ns(path="/115/link/Show/Season 1/", type="dir"),
            file_contexts=[ns(path="one.mkv", meta=ns(season=1, episode=1)),
                           ns(path="two.mkv", meta=ns(season=1, episode=[2, 3]))],
        ))
        self.assertEqual(body["event"], "metadata.scrape")
        self.assertEqual(body["target"], "/link/Show/Season 1")
        self.assertEqual(body["episodes"], [{"season": 1, "episode": i} for i in (1, 2, 3)])
        self.assertEqual(set(body), {"event", "target", "episodes", "idempotency_key", "occurred_at"})

    def test_v3_destination_fileitem_over_source(self):
        body = bridge.normalize_event("transfer.complete", {
            "transferinfo": {"target_diritem": {"path": "/115/link/destination/"}},
            "fileitem": {"path": "/downloads/source.mkv", "type": "file"},
        })
        self.assertEqual(body["target"], "/link/destination")
        self.assertEqual(body["episodes"], [])

    def test_fileitem_file_uses_parent(self):
        body = bridge.normalize_event("metadata.scrape", {
            "fileitem": {"path": "/115/link/Movie (2026)/Movie.mkv", "type": "file"},
        })
        self.assertEqual(body["target"], "/link/Movie (2026)")
        self.assertEqual(body["episodes"], [])

    def test_file_list_common_parent_and_stable_key(self):
        data = {"file_list": ["/115/link/Show/Season 1/a.mkv", "/115/link/Show/Season 1/b.mkv"]}
        first = bridge.normalize_event("metadata.scrape", data)
        second = bridge.normalize_event("metadata.scrape", data)
        self.assertEqual(first["target"], "/link/Show/Season 1")
        self.assertEqual(first["idempotency_key"], second["idempotency_key"])
        self.assertNotEqual(first["idempotency_key"], bridge.normalize_event("transfer.complete", data)["idempotency_key"])

    def test_episode_sort_dedup_and_missing_meta(self):
        self.assertEqual(bridge.meta_episodes({"season": "0", "episode": [3, 2, 3]}),
                         [{"season": 0, "episode": 2}, {"season": 0, "episode": 3}])
        for meta in (None, {}, {"season": 1}, {"season": "bad", "episode": 1}):
            self.assertEqual(bridge.meta_episodes(meta), [])

    def test_prefix_is_only_removed_at_segment_boundary(self):
        self.assertEqual(bridge.normalize_path("/115/link/Show/"), "/link/Show")
        self.assertEqual(bridge.normalize_path("/1150/link/Show/"), "/1150/link/Show")
        self.assertEqual(bridge.normalize_path("/other/115/link/Show"), "/other/115/link/Show")
        self.assertIsNone(bridge.normalize_path("relative/file.mkv"))
        self.assertIsNone(bridge.normalize_event("metadata.scrape", {}))

    def test_invalid_timestamp_falls_back_to_now(self):
        with patch.object(bridge.time, "time", return_value=1793016921.0):
            body = bridge.normalize_event("transfer.complete", payload(occurred_at=float("nan")))
        self.assertEqual(body["occurred_at"], 1793016921.0)

    def test_signature_fixed_vector(self):
        # Independently computed with OpenSSL, including the timestamp separator and raw bytes.
        self.assertEqual(
            bridge.signature("test-secret", "1793016921", b'{"event":"transfer.complete"}'),
            "sha256=dc2dd10256b5d6ddd7f101b221408f9b6c8dd80800ab6be2777d31688f5dca09",
        )

    def test_sdk_subscriptions_manifest_and_auth(self):
        plugin = bridge.MpEventBridge()
        handlers = bridge.eventmanager.handlers
        self.assertIs(handlers[FakeEventType.TransferComplete], bridge.MpEventBridge.on_transfer_complete)
        self.assertIs(handlers[FakeEventType.MetadataScrape], bridge.MpEventBridge.on_metadata_scrape)
        self.assertEqual({(api["path"], tuple(api["methods"]), api["auth"]) for api in plugin.get_api()},
                         {("/state", ("GET",), "bear"), ("/test", ("POST",), "bear")})
        manifest = json.loads((ROOT / "package.v3.json").read_text())["MpEventBridge"]
        self.assertEqual(manifest["version"], plugin.plugin_version)
        self.assertEqual(manifest["system_version"], ">=3.0.0")
        self.assertEqual(plugin.plugin_config_prefix, "mpeventbridge_")
        self.assertEqual(plugin.get_form()[1], bridge.DEFAULTS)


class AsyncBridgeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = FakeClient()
        self.factory = patch.object(bridge.httpx, "AsyncClient", return_value=self.client)
        self.constructor = self.factory.start()
        self.plugin = bridge.MpEventBridge()
        self.config = {"enabled": True, "secret": "secret", "debounce_seconds": 0.02}
        self.plugin.init_plugin(self.config)
        await asyncio.sleep(0)

    async def asyncTearDown(self):
        cleanup = self.plugin.close()
        if cleanup:
            await cleanup
        self.factory.stop()

    async def wait_record(self):
        async def wait():
            while not self.plugin._records:
                await asyncio.sleep(0.001)
        await asyncio.wait_for(wait(), timeout=1)

    def emit(self, episode=1, **extra):
        self.plugin.on_transfer_complete(types.SimpleNamespace(event_data=payload(episode, **extra)))

    async def test_three_events_debounce_union_and_wire_signature(self):
        self.emit(1, idempotency_key="one")
        self.emit(2, idempotency_key="two")
        self.plugin.on_metadata_scrape(types.SimpleNamespace(event_data=payload([2, 3], idempotency_key="three")))
        await self.wait_record()
        self.assertEqual(len(self.client.calls), 1)
        url, request = self.client.calls[0]
        body = json.loads(request["content"])
        self.assertEqual(url, "http://192.168.10.25:7811/api/hooks/mp")
        self.assertEqual(body["episodes"], [{"season": 1, "episode": i} for i in (1, 2, 3)])
        self.assertEqual(body["event"], "metadata.scrape")
        self.assertEqual(body["idempotency_key"], bridge.event_key(body["event"], body["target"], body["episodes"]))
        headers = request["headers"]
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["X-MP-Signature"], bridge.signature("secret", headers["X-MP-Timestamp"], request["content"]))
        self.assertEqual((await self.plugin.api_state())["counts"],
                         dict(received=3, merged=2, sent_success=1, failed=0))
        self.constructor.assert_called_once_with(timeout=5.0, trust_env=False)

    async def test_debounce_extends_from_last_arrival(self):
        self.emit(1)
        await asyncio.sleep(0.012)
        self.emit(2)
        await asyncio.sleep(0.012)
        self.assertEqual(len(self.client.calls), 0)
        await self.wait_record()
        self.assertEqual(len(self.client.calls), 1)

    async def test_different_targets_remain_separate(self):
        self.emit(1, target="/115/link/one")
        self.emit(2, target="/115/link/two")
        await self.wait_record()
        async def wait():
            while len(self.client.calls) < 2:
                await asyncio.sleep(0)
        await asyncio.wait_for(wait(), 1)
        self.assertEqual({json.loads(call[1]["content"])["target"] for call in self.client.calls},
                         {"/link/one", "/link/two"})

    async def test_callbacks_return_while_http_is_waiting(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocked(*_, **__):
            entered.set()
            await release.wait()
            return httpx.Response(200, json={"ok": True})

        self.client.post = blocked
        self.emit(1)
        await asyncio.wait_for(entered.wait(), 1)
        self.emit(2)
        await asyncio.sleep(0)
        self.assertEqual(self.plugin._counts["received"], 2)
        release.set()
        await self.wait_record()

    async def test_threaded_event_delivery_uses_owning_queue(self):
        await asyncio.to_thread(self.emit, 4)
        await self.wait_record()
        self.assertEqual(self.plugin._counts["received"], 1)

    async def test_transport_retry_two_errors_then_one_valid_request(self):
        client = FakeClient([httpx.ConnectError("one"), httpx.ReadTimeout("two")])
        body = bridge.normalize_event("transfer.complete", payload())
        with patch.object(bridge.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            await self.plugin._send(client, body, self.plugin._config)
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(len(client.valid_requests), 1)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 5])
        self.assertEqual(self.plugin._counts["sent_success"], 1)
        self.assertEqual(self.plugin._counts["failed"], 0)
        self.assertEqual(len({call[1]["content"] for call in client.calls}), 1)
        self.assertEqual(len(self.plugin._records), 1)

    async def test_final_failure_three_retries_and_single_record(self):
        client = FakeClient([httpx.ConnectError("no connection")] * 4)
        with patch.object(bridge.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            await self.plugin._send(client, bridge.normalize_event("transfer.complete", payload()), self.plugin._config)
        self.assertEqual(len(client.calls), 4)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 5, 30])
        self.assertEqual(self.plugin._counts["failed"], 1)
        self.assertEqual(self.plugin._counts["sent_success"], 0)
        self.assertEqual(len(self.plugin._records), 1)
        self.assertIsNone(self.plugin._records[0]["http_status"])

    async def test_business_rejection_is_failure_but_duplicate_is_success(self):
        client = FakeClient([httpx.Response(200, json={"ok": False, "reason": "unknown_dir"})] * 4)
        body = bridge.normalize_event("transfer.complete", payload())
        with patch.object(bridge.asyncio, "sleep", new_callable=AsyncMock):
            await self.plugin._send(client, body, self.plugin._config)
        self.assertEqual(self.plugin._records[-1]["result"], "unknown_dir")
        self.assertEqual(self.plugin._counts["failed"], 1)
        duplicate = FakeClient([httpx.Response(200, json={"ok": True, "queued": 0, "reason": "duplicate"})])
        await self.plugin._send(duplicate, body, self.plugin._config)
        self.assertEqual(self.plugin._counts["sent_success"], 1)
        self.assertEqual(self.plugin._records[-1]["result"], "duplicate")

    async def test_http_errors_and_invalid_json_retry(self):
        client = FakeClient([httpx.Response(503, json={"ok": False, "reason": "hooks_disabled"}),
                             httpx.Response(200, content=b"invalid-json")])
        with patch.object(bridge.asyncio, "sleep", new_callable=AsyncMock):
            await self.plugin._send(client, bridge.normalize_event("metadata.scrape", payload()), self.plugin._config)
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(self.plugin._counts["sent_success"], 1)

    async def test_disabled_does_not_queue_or_send(self):
        self.plugin.init_plugin({"enabled": False})
        self.emit(1)
        result = await self.plugin.api_test()
        await asyncio.sleep(0.04)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "disabled")
        self.assertEqual(self.plugin._counts["received"], 0)
        self.assertEqual(self.client.calls, [])
        self.assertIsNone(self.plugin._queue)
        self.assertTrue(self.client.closed)

    async def test_disabled_or_stale_worker_cannot_start_request(self):
        body = bridge.normalize_event("transfer.complete", payload())
        previous = self.plugin._generation
        self.plugin.init_plugin(self.config)
        await self.plugin._send(self.client, body, self.plugin._config, previous)
        self.plugin.init_plugin({"enabled": False})
        await self.plugin._send(self.client, body, self.plugin._config)
        self.assertEqual(self.client.calls, [])

    async def test_config_restart_closes_old_worker_and_uses_new_credentials(self):
        self.emit(1)
        await asyncio.sleep(0)
        replacement = FakeClient()
        self.constructor.return_value = replacement
        self.plugin.init_plugin({**self.config, "endpoint": "http://receiver:7811", "secret": "new-secret"})
        await asyncio.sleep(0)
        self.emit(2)
        await self.wait_record()
        self.assertTrue(self.client.closed)
        self.assertEqual(self.client.calls, [])
        url, request = replacement.calls[0]
        self.assertEqual(url, "http://receiver:7811/api/hooks/mp")
        self.assertEqual(request["headers"]["X-MP-Signature"],
                         bridge.signature("new-secret", request["headers"]["X-MP-Timestamp"], request["content"]))

    async def test_rapid_reload_and_close_reap_all_tasks(self):
        for _ in range(5):
            self.plugin.init_plugin(self.config)
            await asyncio.sleep(0)
        await self.plugin.close()
        self.assertTrue(all(task.done() for task in self.plugin._tasks))
        self.assertTrue(self.client.closed)

    async def test_stop_then_immediate_reinit_does_not_clear_new_queue(self):
        cleanup = self.plugin.stop_service()
        self.plugin.init_plugin(self.config)
        self.emit(5)
        await cleanup
        await self.wait_record()
        self.assertEqual(self.plugin._counts["sent_success"], 1)
        self.assertIsNotNone(self.plugin._queue)

    async def test_close_cancels_retry_without_waiting_for_backoff(self):
        client = FakeClient([httpx.ConnectError("no connection")])
        self.constructor.return_value = client
        self.plugin.init_plugin(self.config)
        await asyncio.sleep(0)
        self.emit()
        async def wait():
            while not client.calls:
                await asyncio.sleep(0.001)
        await asyncio.wait_for(wait(), 1)
        await asyncio.wait_for(self.plugin.close(), 0.2)
        self.assertTrue(client.closed)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(self.plugin._counts["failed"], 0)

    async def test_debug_test_uses_complete_pipeline_and_unique_host_keys(self):
        result = await self.plugin.api_test(target="/115/link/mapped/Season 1/")
        self.assertTrue(result["ok"])
        await self.wait_record()
        body = json.loads(self.client.calls[0][1]["content"])
        self.assertEqual(body["target"], "/link/mapped/Season 1")
        self.assertEqual(body["episodes"], [])
        self.assertEqual(body["idempotency_key"], result["idempotency_key"])
        second = await self.plugin.api_test()
        self.assertNotEqual(second["idempotency_key"], result["idempotency_key"])

    async def test_recent_history_is_limited_to_twenty(self):
        body = bridge.normalize_event("transfer.complete", payload())
        for i in range(25):
            self.plugin._record(body, 200, str(i))
        state = await self.plugin.api_state()
        self.assertEqual(len(state["records"]), 20)
        self.assertEqual(state["records"][0]["result"], "5")
        self.assertNotIn("secret", json.dumps(state))


class SynchronousLifecycleTest(unittest.TestCase):
    def test_init_without_loop_and_thread_safe_delivery_and_close(self):
        received = threading.Event()
        client = FakeClient()
        original_post = client.post

        async def post(*args, **kwargs):
            response = await original_post(*args, **kwargs)
            received.set()
            return response

        client.post = post
        plugin = bridge.MpEventBridge()
        with patch.object(bridge.httpx, "AsyncClient", return_value=client):
            try:
                plugin.init_plugin({"enabled": True, "secret": "secret", "debounce_seconds": 0})
                plugin.on_transfer_complete(types.SimpleNamespace(event_data=payload()))
                self.assertTrue(received.wait(1))
                state = asyncio.run(plugin.api_state())
                self.assertEqual(state["counts"]["sent_success"], 1)
            finally:
                cleanup = plugin.close()
                cleanup.result(timeout=1)
                plugin._thread.join(timeout=1)
        self.assertFalse(plugin._thread.is_alive())
        self.assertTrue(client.closed)

    def test_defaults_persistence_and_disabled_api_without_loop(self):
        plugin = bridge.MpEventBridge()
        plugin.init_plugin()
        self.assertEqual(plugin.saved_config, bridge.DEFAULTS)
        self.assertIsNone(plugin._loop)
        plugin.on_transfer_complete(types.SimpleNamespace(event_data=payload()))
        self.assertFalse(asyncio.run(plugin.api_test())["ok"])
        self.assertEqual(asyncio.run(plugin.api_state())["counts"]["received"], 0)
        self.assertIsNone(plugin.close())

    def test_invalid_config_is_inactive(self):
        for config in ({"secret": ""}, {"secret": "s", "endpoint": "file:///tmp/path"},
                       {"secret": "s", "endpoint": "http://receiver/path"},
                       {"secret": "s", "endpoint": "http://["},
                       {"secret": "s", "endpoint": "http://receiver:bad"}):
            plugin = bridge.MpEventBridge()
            plugin.init_plugin({"enabled": True, **config})
            self.assertFalse(plugin.get_state())
            self.assertIsNone(plugin._loop)

    def test_stop_and_reconfigure_background_loop(self):
        plugin = bridge.MpEventBridge()
        client = FakeClient()
        received = threading.Event()
        original_post = client.post

        async def post(*args, **kwargs):
            reply = await original_post(*args, **kwargs)
            received.set()
            return reply

        client.post = post
        config = {"enabled": True, "secret": "secret", "debounce_seconds": 0}
        with patch.object(bridge.httpx, "AsyncClient", return_value=client):
            try:
                plugin.init_plugin(config)
                plugin.stop_service()
                plugin.init_plugin(config)
                plugin.on_metadata_scrape(types.SimpleNamespace(event_data=payload()))
                self.assertTrue(received.wait(1))
            finally:
                plugin.close().result(timeout=1)
                plugin._thread.join(timeout=1)
        self.assertFalse(plugin._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
