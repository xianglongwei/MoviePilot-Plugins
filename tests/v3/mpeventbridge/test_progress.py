"""Progress callback tests with scoped MP modules and in-memory HTTP only."""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import sys
import threading
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from fastapi import FastAPI


ROOT = Path(__file__).resolve().parents[3]


class MediaType(Enum):
    TV = "电视剧"
    MOVIE = "电影"


class EventType(Enum):
    TransferComplete = "transfer.complete"
    MetadataScrape = "metadata.scrape"


def load_plugin():
    modules = {name: types.ModuleType(name) for name in (
        "app", "app.schemas", "app.schemas.types", "app.sdk", "app.sdk.plugin",
        "app.sdk.events", "app.sdk.logging",
    )}
    modules["app.sdk.plugin"]._PluginBase = object
    modules["app.schemas.types"].EventType = EventType
    modules["app.sdk.events"].Event = types.SimpleNamespace
    modules["app.sdk.events"].eventmanager = types.SimpleNamespace(
        register=lambda _: lambda handler: handler,
    )
    modules["app.sdk.logging"].logger = Mock()
    spec = importlib.util.spec_from_file_location(
        "mpeventbridge_progress", ROOT / "plugins.v3/mpeventbridge/__init__.py",
    )
    module = importlib.util.module_from_spec(spec)
    # No app.db / app.chain stubs: importing the plugin must not need them.
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


bridge = load_plugin()


class ProgressTest(unittest.TestCase):
    def setUp(self):
        self.plugin = bridge.MpEventBridge()
        self.row = types.SimpleNamespace(
            id=1573, name="Test show", year="2026", type=MediaType.TV.value,
            season=4, total_episode=8, start_episode=1, lack_episode=7,
            note=[1], best_version=0, state="S", media_source="themoviedb", media_id="123",
        )
        self.rows = {1573: self.row}
        # Detached copies model separate Oper queries rather than sharing ORM objects.
        self.oper = Mock()
        self.oper.get.side_effect = lambda sid: copy.deepcopy(self.rows.get(sid))

        def update(sid, fields):
            row = self.rows.get(sid)
            if row is not None:
                for key, value in fields.items():
                    setattr(row, key, value)
            return copy.deepcopy(row)

        self.oper.update.side_effect = update
        self.meta = object()
        self.media = object()
        self.build_meta = Mock(return_value=self.meta)
        self.identity = Mock(return_value={"media_source": "themoviedb", "media_id": "123"})
        self.media_chain = Mock()
        self.media_chain.recognize_media.return_value = self.media
        self.subscribe_chain = Mock()
        self.subscribe_chain.finish_subscribe_or_not.side_effect = (
            lambda **kwargs: self.rows.pop(kwargs["subscribe"].id, None)
        )
        names = (
            "app", "app.schemas", "app.schemas.types", "app.db", "app.db.oper",
            "app.db.oper.subscribe", "app.application", "app.application.subscription",
            "app.application.subscription.contract", "app.chain", "app.chain.media",
            "app.chain.subscribe", "app.chain.subscribe.identity",
        )
        modules = {name: types.ModuleType(name) for name in names}
        modules["app.schemas.types"].MediaType = MediaType
        modules["app.db.oper.subscribe"].SubscribeOper = Mock(return_value=self.oper)
        modules["app.application.subscription.contract"].build_subscribe_meta = self.build_meta
        modules["app.chain.media"].MediaChain = Mock(return_value=self.media_chain)
        modules["app.chain.subscribe"].SubscribeChain = Mock(return_value=self.subscribe_chain)
        modules["app.chain.subscribe.identity"].subscribe_recognize_kwargs = self.identity
        self.stub_modules = patch.dict(sys.modules, modules)
        self.stub_modules.start()
        self.addCleanup(self.stub_modules.stop)

    def push(self, episodes, **extra):
        return self.plugin.api_progress({"sub_id": 1573, "season": 4,
                                         "episodes": episodes, **extra})

    def assert_no_completion(self):
        self.build_meta.assert_not_called()
        self.media_chain.recognize_media.assert_not_called()
        self.subscribe_chain.finish_subscribe_or_not.assert_not_called()

    def test_merge_numeric_note_and_compute_lack(self):
        self.row.note = [1, "2", "invalid", 2, 99]
        self.row.lack_episode = 6
        result = self.push([4, 3, 3, 0, -1], origin="p115-302", observed_at=1793585616.0)
        self.assertEqual(result, {"ok": True, "state": "updated", "lack_episode": 4,
                                  "reason": None, "note": [1, 2, 3, 4, 99]})
        self.oper.update.assert_called_once_with(1573, {"note": [1, 2, 3, 4, 99], "lack_episode": 4})
        self.assert_no_completion()
        self.assertIsNone(self.plugin._loop)

    def test_start_episode_limits_expected_range(self):
        self.row.start_episode = 5
        self.row.note = [1, "5"]
        self.row.lack_episode = 3
        result = self.push([1, 2, 4, 6, 7])
        self.assertEqual(result["note"], [1, 5, 6, 7])
        self.assertEqual(result["lack_episode"], 1)
        self.assert_no_completion()

    def test_null_start_episode_defaults_to_one(self):
        self.row.start_episode = None
        result = self.push([2])
        self.assertEqual(result["lack_episode"], 6)

    def test_out_of_range_does_not_write_or_finish(self):
        result = self.push(list(range(1, 10)))
        self.assertEqual(result, {"ok": True, "state": "skipped", "reason": "episode_out_of_range"})
        self.oper.update.assert_not_called()
        self.assert_no_completion()

    def test_complete_paused_subscription_uses_exact_chain_arguments(self):
        result = self.push(list(range(1, 9)))
        self.assertEqual(result["state"], "finished")
        self.assertEqual(result["lack_episode"], 0)
        self.media_chain.recognize_media.assert_called_once_with(
            meta=self.meta, mtype=MediaType.TV, media_source="themoviedb", media_id="123", cache=False,
        )
        self.build_meta.assert_called_once()
        self.identity.assert_called_once_with(self.build_meta.call_args.args[0])
        subscribe = self.build_meta.call_args.args[0]
        self.assertEqual(subscribe.note, list(range(1, 9)))
        self.assertEqual(subscribe.lack_episode, 0)
        self.assertEqual(subscribe.state, "S")
        self.subscribe_chain.finish_subscribe_or_not.assert_called_once_with(
            subscribe=subscribe, meta=self.meta, mediainfo=self.media,
            downloads=None, lefts={}, force=True,
        )

    def test_pending_updates_progress_without_completion(self):
        self.row.state = "P"
        result = self.push(list(range(1, 9)))
        self.assertEqual((result["state"], result["reason"], result["lack_episode"]),
                         ("updated", "pending", 0))
        self.oper.update.assert_called_once()
        self.assert_no_completion()

    def test_best_version_updates_progress_without_completion(self):
        self.row.best_version = 1
        result = self.push(list(range(1, 9)))
        self.assertEqual((result["state"], result["reason"]), ("updated", "best_version"))
        self.oper.update.assert_called_once()
        self.assert_no_completion()

    def test_missing_subscribe(self):
        self.rows.clear()
        self.assertEqual(self.push([1]), {"ok": False, "state": "not_found"})
        self.oper.get.assert_called_once_with(1573)
        self.oper.update.assert_not_called()
        self.assert_no_completion()

    def test_non_tv(self):
        self.row.type = MediaType.MOVIE.value
        self.assertEqual(self.push([1]), {"ok": True, "state": "skipped", "reason": "not_tv"})
        self.oper.update.assert_not_called()
        self.assert_no_completion()

    def test_unknown_total(self):
        for total in (0, None, -1):
            with self.subTest(total=total):
                self.row.total_episode = total
                self.assertEqual(self.push([1]), {"ok": True, "state": "skipped",
                                                  "reason": "no_total_episode"})
        self.oper.update.assert_not_called()
        self.assert_no_completion()

    def test_season_zero_uses_subscribe_season(self):
        self.assertEqual(self.push([2], season=0)["state"], "updated")

    def test_unknown_or_mismatched_season(self):
        self.assertEqual(self.push([2], season=3)["reason"], "no_season")
        self.row.season = None
        self.assertEqual(self.push([2], season=0)["reason"], "no_season")
        self.oper.update.assert_not_called()
        self.assert_no_completion()

    def test_empty_observation_preserves_existing_note_and_recomputes(self):
        self.row.note = [1, 2]
        self.row.lack_episode = 99
        result = self.push([])
        self.assertEqual(result["note"], [1, 2])
        self.assertEqual(result["lack_episode"], 6)
        self.oper.update.assert_called_once_with(1573, {"lack_episode": 6})

    def test_only_changed_fields_are_written(self):
        self.row.lack_episode = 6
        self.push([2])
        self.oper.update.assert_called_once_with(1573, {"note": [1, 2]})

    def test_repeated_partial_push_returns_updated_without_another_write(self):
        first = self.push([2, 3])
        second = self.push([3, 2, 2])
        self.assertEqual(first, second)
        self.assertEqual(second["state"], "updated")
        self.oper.update.assert_called_once()
        self.assert_no_completion()

    def test_repeat_after_completion_finds_deleted_subscription(self):
        episodes = list(range(1, 9))
        self.assertEqual(self.push(episodes)["state"], "finished")
        self.assertEqual(self.push(episodes), {"ok": False, "state": "not_found"})
        self.oper.update.assert_called_once()
        self.subscribe_chain.finish_subscribe_or_not.assert_called_once()

    def test_internal_errors_are_caught_and_truncated(self):
        for target in (self.oper.get, self.oper.update, self.build_meta,
                       self.media_chain.recognize_media, self.subscribe_chain.finish_subscribe_or_not):
            with self.subTest(target=target):
                original = target.side_effect
                target.side_effect = RuntimeError("failure" * 50)
                result = self.push(list(range(1, 9)))
                self.assertEqual(result, {"ok": False, "state": "error", "reason": ("failure" * 50)[:200]})
                target.side_effect = original
                self.row.note, self.row.lack_episode = [1], 7

    def test_recognition_failure_can_retry_even_when_progress_unchanged(self):
        self.media_chain.recognize_media.return_value = None
        result = self.push(list(range(1, 9)))
        self.assertEqual(result, {"ok": False, "state": "error", "reason": "media recognition failed"})
        self.subscribe_chain.finish_subscribe_or_not.assert_not_called()
        self.media_chain.recognize_media.return_value = self.media
        self.assertEqual(self.push(list(range(1, 9)))["state"], "finished")
        self.oper.update.assert_called_once()
        self.subscribe_chain.finish_subscribe_or_not.assert_called_once()

    def test_invalid_input_returns_error_without_mutation(self):
        for episodes in ("1,2", [1.5], [True], None):
            with self.subTest(episodes=episodes):
                result = self.push(episodes)
                self.assertEqual((result["ok"], result["state"]), (False, "error"))
        self.oper.update.assert_not_called()
        self.assert_no_completion()

    def test_concurrent_requests_preserve_union_and_finish_once(self):
        self.row.total_episode, self.row.lack_episode, self.row.note = 3, 3, []
        barrier = threading.Barrier(2)

        def push(episodes):
            barrier.wait(timeout=3)
            return self.push(episodes)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(push, [1, 2])
            second = pool.submit(push, [2, 3])
            results = [first.result(timeout=3), second.result(timeout=3)]
        self.assertEqual({result["state"] for result in results}, {"updated", "finished"})
        self.subscribe_chain.finish_subscribe_or_not.assert_called_once()
        subscribe = self.subscribe_chain.finish_subscribe_or_not.call_args.kwargs["subscribe"]
        self.assertEqual(subscribe.note, [1, 2, 3])

    def test_json_body_and_internal_error_over_http(self):
        app = FastAPI()
        route = next(item for item in self.plugin.get_api() if item["path"] == "/progress")
        self.assertEqual((route["methods"], route["auth"]), (["POST"], "bear"))
        app.add_api_route("/api/v1/plugin/MpEventBridge/progress", route["endpoint"], methods=route["methods"])

        async def request():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mp") as client:
                response = await client.post("/api/v1/plugin/MpEventBridge/progress", json={
                    "sub_id": 1573, "season": 4, "episodes": [2], "origin": "p115-302",
                })
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["note"], [1, 2])
                self.oper.get.side_effect = RuntimeError("db unavailable")
                response = await client.post("/api/v1/plugin/MpEventBridge/progress", json={
                    "sub_id": 1573, "season": 4, "episodes": [2],
                })
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"ok": False, "state": "error", "reason": "db unavailable"})

        asyncio.run(request())


if __name__ == "__main__":
    unittest.main()
