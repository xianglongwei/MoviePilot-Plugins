"""MoviePilot V2 兼容层的轻量加载与扫描合同测试。"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import types
import unittest
from unittest import mock
from enum import Enum
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugins.v2" / "piggokidsmetadata"


class FakePluginBase:
    def __init__(self) -> None:
        self._plugin_data: dict[str, Any] = {}

    def get_data(self, key: str) -> Any:
        return self._plugin_data.get(key)

    def save_data(self, key: str, value: Any) -> None:
        self._plugin_data[key] = value


class FakeLogger:
    def error(self, *_: Any, **__: Any) -> None:
        return None


class FakeFileItem:
    def __init__(self, **values: Any) -> None:
        self.__dict__.update(values)


class FakeMediaType(Enum):
    MOVIE = "电影"
    TV = "电视剧"
    UNKNOWN = "未知"


class FakeEventType(Enum):
    DownloadAdded = "download.added"
    TransferComplete = "transfer.complete"
    TransferFailed = "transfer.failed"


class FakeEvent:
    def __init__(self, event_data: Any = None) -> None:
        self.event_data = event_data


class FakeEventManager:
    @staticmethod
    def register(_: Any):
        def decorator(function):
            return function

        return decorator


def load_plugin_module() -> types.ModuleType:
    app = types.ModuleType("app")
    schemas = types.ModuleType("app.schemas")
    schemas.FileItem = FakeFileItem
    app.schemas = schemas
    plugins = types.ModuleType("app.plugins")
    plugins._PluginBase = FakePluginBase
    log = types.ModuleType("app.log")
    log.logger = FakeLogger()
    core = types.ModuleType("app.core")
    core_event = types.ModuleType("app.core.event")
    core_event.Event = FakeEvent
    core_event.eventmanager = FakeEventManager()
    schema_types = types.ModuleType("app.schemas.types")
    schema_types.EventType = FakeEventType
    schema_types.MediaType = FakeMediaType
    sys.modules.update({
        "app": app,
        "app.schemas": schemas,
        "app.plugins": plugins,
        "app.log": log,
        "app.core": core,
        "app.core.event": core_event,
        "app.schemas.types": schema_types,
    })

    module_name = "piggokidsmetadata_v2_contract"
    spec = importlib.util.spec_from_file_location(
        module_name,
        PLUGIN_ROOT / "__init__.py",
        submodule_search_locations=[str(PLUGIN_ROOT)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class V2PluginContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_plugin_module()

    def test_load_and_scan_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "Movie"
            payload.mkdir()
            (payload / "Movie.mkv").write_bytes(b"")
            (payload / "movie.nfo").write_text(
                "<movie><title>V2 儿童电影</title><year>2024</year></movie>",
                encoding="utf-8",
            )
            plugin = self.module.PigGoKidsMetadata()
            plugin.init_plugin({"enabled": True, "scan_root": str(root)})
            response = plugin.api_scan({"relative_path": "Movie"})
            self.assertTrue(response["success"])
            self.assertEqual(response["data"]["task"]["state"], "READY_TO_TRANSFER")
            self.assertTrue(response["data"]["decision"]["transfer_preview"])
            self.assertEqual({item["auth"] for item in plugin.get_api()}, {"bear"})
            self.assertEqual({item["path"] for item in plugin.get_api()}, {
                "/status", "/registry", "/feeds", "/contribution-drafts", "/scan", "/candidates",
                "/candidates/refresh", "/candidates/import", "/candidates/ignore", "/candidates/update",
                "/candidates/download",
                "/candidates/download-action", "/tasks", "/tasks/retry", "/tasks/review",
                "/tasks/retry-action",
            })
            with mock.patch.object(self.module.Path, "is_file", return_value=False):
                self.assertEqual(plugin.get_render_mode(), ("vuetify", ""))
                self.assertEqual(plugin.get_sidebar_nav(), [])
            with mock.patch.object(self.module.Path, "is_file", return_value=True):
                self.assertEqual(plugin.get_render_mode(), ("vue", "dist/assets"))
                self.assertEqual(plugin.get_sidebar_nav()[0]["nav_key"], "main")

    def test_feed_status_drops_private_query_parameters(self) -> None:
        plugin = self.module.PigGoKidsMetadata()
        plugin.init_plugin({"enabled": True})
        plugin.save_data(self.module.FEED_STATUS_KEY, {
            "feed:one": {
                "feed_id": "feed:one",
                "url": "https://piggo.example/rss.php?passkey=never-expose-this&uid=7",
                "last_attempt_at": "2026-08-31T10:00:00+00:00",
                "last_success_at": "2026-08-31T10:00:00+00:00",
                "http_status": 200,
                "parsed_count": 3,
                "error_code": None,
            }
        })
        response = plugin.api_feeds()
        self.assertTrue(response["success"])
        self.assertEqual(response["data"]["items"][0]["source"], "https://piggo.example/rss.php")
        self.assertNotIn("never-expose-this", json.dumps(response, ensure_ascii=False))

    def test_conflict_review_requires_explicit_transfer_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "Mixed"
            payload.mkdir()
            (payload / "Mixed.mkv").write_bytes(b"")
            (payload / "movie.nfo").write_text(
                "<movie><title>冲突样例</title><year>2024</year></movie>",
                encoding="utf-8",
            )
            (payload / "tvshow.nfo").write_text(
                "<tvshow><title>冲突样例</title><year>2024</year></tvshow>",
                encoding="utf-8",
            )
            plugin = self.module.PigGoKidsMetadata()
            plugin.init_plugin({"enabled": True, "scan_root": str(root)})
            scanned = plugin.api_scan({"relative_path": "Mixed"})
            self.assertTrue(scanned["success"])
            self.assertEqual(scanned["data"]["task"]["state"], "NEEDS_REVIEW")
            task_id = scanned["data"]["task"]["task_id"]
            approved = plugin.api_review_task({"task_id": task_id, "action": "approve"})
            self.assertTrue(approved["success"])
            self.assertEqual(approved["data"]["task"]["state"], "READY_TO_TRANSFER")
            self.assertEqual(approved["data"]["decision"]["review"]["action"], "approve")
            self.assertFalse(plugin.api_review_task({"task_id": task_id, "action": "approve"})["success"])

    def test_exact_tmdb_match_reuses_public_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "Movie"
            payload.mkdir()
            (payload / "Movie.mkv").write_bytes(b"")
            (payload / "movie.nfo").write_text(
                "<movie><title>V2 儿童电影</title><year>2024</year></movie>",
                encoding="utf-8",
            )

            class FakeMetaInfo:
                def __init__(self, title: str) -> None:
                    self.title = title

            recognize_calls = []

            class FakeMediaChain:
                @staticmethod
                def recognize_by_meta(*_: Any, **kwargs: Any) -> Any:
                    recognize_calls.append(kwargs)
                    return types.SimpleNamespace(
                        source="themoviedb",
                        media_id=None,
                        tmdb_id=24680,
                        title="V2 儿童电影",
                        original_title=None,
                        names=[],
                        year="2024",
                        type=FakeMediaType.MOVIE,
                        season=None,
                    )

            chain = types.ModuleType("app.chain")
            chain_media = types.ModuleType("app.chain.media")
            chain_media.MediaChain = FakeMediaChain
            core_metainfo = types.ModuleType("app.core.metainfo")
            core_metainfo.MetaInfo = FakeMetaInfo
            module_names = ["app.chain", "app.chain.media", "app.core.metainfo"]
            previous = {name: sys.modules.get(name) for name in module_names}
            sys.modules.update({
                "app.chain": chain,
                "app.chain.media": chain_media,
                "app.core.metainfo": core_metainfo,
            })
            try:
                plugin = self.module.PigGoKidsMetadata()
                plugin.init_plugin({"enabled": True, "scan_root": str(root)})
                response = plugin.api_scan({"relative_path": "Movie"})
            finally:
                for name, value in previous.items():
                    if value is None:
                        sys.modules.pop(name, None)
                    else:
                        sys.modules[name] = value

            self.assertTrue(response["success"])
            self.assertEqual(recognize_calls, [{"source": "themoviedb", "obtain_images": False}])
            decision = response["data"]["decision"]
            self.assertTrue(decision["public_match"]["exact"])
            self.assertEqual(decision["item"]["media_source"], "themoviedb")
            self.assertEqual(decision["item"]["media_id"], "24680")
            self.assertEqual(plugin.api_status()["data"]["registry_count"], 0)
            drafts = plugin.api_contribution_drafts()["data"]
            self.assertEqual(drafts["total"], 1)
            self.assertEqual(drafts["items"][0]["mode"], "update_existing")
            self.assertEqual(drafts["items"][0]["target"]["media_id"], "24680")

    def test_pasted_download_is_idempotent_and_secret_is_not_persisted(self) -> None:
        plugin = self.module.PigGoKidsMetadata()
        plugin.init_plugin({"enabled": True})
        secret = "abcdefghijklmnopqrstuvwxyz123456"
        imported = plugin.api_import_candidate({
            "download_reference": f"https://piggo.example/download.php?id=88&passkey={secret}",
            "title": "V2 儿童电影",
            "media_type": "movie",
        })
        self.assertTrue(imported["success"])
        candidate_id = imported["data"]["candidate"]["candidate_id"]
        calls = []

        def submit(candidate, reference):
            calls.append((candidate.candidate_id, reference))
            return "c" * 40

        plugin._submit_download_to_host = submit
        first = plugin.api_download_candidate({"candidate_id": candidate_id})
        second = plugin.api_download_candidate({"candidate_id": candidate_id})
        self.assertTrue(first["success"])
        self.assertTrue(second["success"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(first["data"]["task"]["state"], "DOWNLOADING")
        self.assertFalse(plugin.api_ignore_candidate({"candidate_id": candidate_id})["success"])
        self.assertNotIn(secret, json.dumps(plugin._plugin_data, ensure_ascii=False))

    def test_unassigned_candidate_can_be_ignored_and_restored(self) -> None:
        plugin = self.module.PigGoKidsMetadata()
        plugin.init_plugin({"enabled": True})
        imported = plugin.api_import_candidate({
            "download_reference": "magnet:?xt=urn:btih:" + "9" * 40,
            "title": "稍后处理样例",
        })
        candidate_id = imported["data"]["candidate"]["candidate_id"]
        ignored = plugin.api_ignore_candidate({"candidate_id": candidate_id, "ignored": True})
        self.assertTrue(ignored["success"])
        self.assertEqual(ignored["data"]["candidate"]["status"], "ignored")
        restored = plugin.api_ignore_candidate({"candidate_id": candidate_id, "ignored": "false"})
        self.assertTrue(restored["success"])
        self.assertEqual(restored["data"]["candidate"]["status"], "discovered")

    def test_unassigned_candidate_identity_can_be_corrected(self) -> None:
        plugin = self.module.PigGoKidsMetadata()
        plugin.init_plugin({"enabled": True})
        imported = plugin.api_import_candidate({
            "download_reference": "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567",
            "title": "待修正标题",
        })
        candidate_id = imported["data"]["candidate"]["candidate_id"]
        updated = plugin.api_update_candidate({
            "candidate_id": candidate_id,
            "title": "  正确 标题  ",
            "media_type": "movie",
        })
        self.assertTrue(updated["success"])
        self.assertEqual(updated["data"]["candidate"]["candidate_id"], candidate_id)
        self.assertEqual(updated["data"]["candidate"]["title"], "正确 标题")
        self.assertEqual(updated["data"]["candidate"]["media_type"], "movie")
        self.assertTrue(updated["data"]["candidate"]["title_overridden"])
        self.assertTrue(updated["data"]["candidate"]["media_type_overridden"])
        self.assertFalse(plugin.api_update_candidate({
            "candidate_id": candidate_id,
            "title": "",
        })["success"])

    def test_retry_resubmits_failed_download_when_reference_is_available(self) -> None:
        plugin = self.module.PigGoKidsMetadata()
        plugin.init_plugin({"enabled": True})
        imported = plugin.api_import_candidate({
            "download_reference": "magnet:?xt=urn:btih:" + "e" * 40,
            "title": "重试下载样例",
        })
        candidate_id = imported["data"]["candidate"]["candidate_id"]
        calls = []

        def submit(*_: Any) -> str:
            calls.append(True)
            if len(calls) == 1:
                raise self.module.PigGoCoreError("下载器暂时不可用")
            return "e" * 40

        plugin._submit_download_to_host = submit
        failed = plugin.api_download_candidate({"candidate_id": candidate_id})
        self.assertFalse(failed["success"])
        self.assertEqual(failed["data"]["task"]["state"], "RETRYABLE_FAILED")
        retried = plugin.api_retry_task({"task_id": failed["data"]["task"]["task_id"]})
        self.assertTrue(retried["success"])
        self.assertEqual(retried["data"]["task"]["state"], "DOWNLOADING")
        self.assertEqual(len(calls), 2)

    def test_transfer_failure_scans_and_completion_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "Movie"
            payload.mkdir()
            (payload / "Movie.mkv").write_bytes(b"")
            (payload / "movie.nfo").write_text(
                "<movie><title>V2 儿童电影</title><year>2024</year></movie>",
                encoding="utf-8",
            )
            plugin = self.module.PigGoKidsMetadata()
            plugin.init_plugin({"enabled": True, "scan_root": str(root)})
            imported = plugin.api_import_candidate({
                "download_reference": "magnet:?xt=urn:btih:" + "d" * 40,
                "title": "V2 儿童电影",
                "media_type": "movie",
            })
            candidate_id = imported["data"]["candidate"]["candidate_id"]
            plugin._submit_download_to_host = lambda *_: "d" * 40
            self.assertTrue(plugin.api_download_candidate({"candidate_id": candidate_id})["success"])
            plugin.on_transfer_failed(FakeEvent({
                "download_hash": "d" * 40,
                "fileitem": FakeFileItem(path=str(payload)),
            }))
            task = plugin.api_tasks()["data"]["items"][0]
            self.assertEqual(task["state"], "READY_TO_TRANSFER")
            self.assertTrue(task["transfer_failed_files"])
            plugin._submit_transfer_to_host = lambda *_: (True, None)
            retried = plugin.api_retry_task({"task_id": task["task_id"]})
            self.assertTrue(retried["success"])
            self.assertEqual(retried["data"]["task"]["transfer_failed_files"], [])
            completed_event = FakeEvent({
                "download_hash": "d" * 40,
                "fileitem": FakeFileItem(path=str(payload / "Movie.mkv")),
            })
            plugin.on_transfer_complete(completed_event)
            plugin.on_transfer_complete(completed_event)
            self.assertEqual(plugin.api_tasks()["data"]["items"][0]["state"], "COMPLETED")

    def test_synchronous_download_event_is_not_overwritten(self) -> None:
        plugin = self.module.PigGoKidsMetadata()
        plugin.init_plugin({"enabled": True})
        imported = plugin.api_import_candidate({
            "download_reference": "magnet:?xt=urn:btih:" + "f" * 40,
            "title": "同步事件样例",
        })
        candidate_id = imported["data"]["candidate"]["candidate_id"]

        def submit(*_: Any) -> str:
            context = types.SimpleNamespace(
                torrent_info=types.SimpleNamespace(
                    file_list=["Show/episode.mkv", "/unsafe/absolute.mkv"],
                ),
            )
            plugin.on_download_added(FakeEvent({
                "hash": "f" * 40,
                "context": context,
                "downloader": "fake-downloader",
                "source": self.module.DOWNLOAD_SOURCE,
            }))
            return "f" * 40

        plugin._submit_download_to_host = submit
        response = plugin.api_download_candidate({"candidate_id": candidate_id})
        self.assertTrue(response["success"])
        task = response["data"]["task"]
        self.assertEqual(task["torrent_files"], ["Show/episode.mkv"])
        self.assertEqual(task["downloader"], "fake-downloader")

    def test_restart_recovery_queries_completed_torrent_by_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "Movie"
            payload.mkdir()
            (payload / "Movie.mkv").write_bytes(b"")
            (payload / "movie.nfo").write_text(
                "<movie><title>恢复样例</title><year>2024</year></movie>",
                encoding="utf-8",
            )
            plugin = self.module.PigGoKidsMetadata()
            plugin.init_plugin({"enabled": True, "scan_root": str(root)})
            imported = plugin.api_import_candidate({
                "download_reference": "magnet:?xt=urn:btih:" + "2" * 40,
                "title": "恢复样例",
                "media_type": "movie",
            })
            candidate_id = imported["data"]["candidate"]["candidate_id"]
            plugin._submit_download_to_host = lambda *_: "2" * 40
            self.assertTrue(plugin.api_download_candidate({"candidate_id": candidate_id})["success"])
            calls = []

            class FakeDownloadChain:
                def list_torrents(self, **kwargs: Any) -> list[dict[str, Any]]:
                    calls.append(kwargs)
                    return [{
                        "hash": "2" * 40,
                        "progress": 100,
                        "path": str(payload),
                    }]

            chain = types.ModuleType("app.chain")
            chain_download = types.ModuleType("app.chain.download")
            chain_download.DownloadChain = FakeDownloadChain
            sys.modules.update({"app.chain": chain, "app.chain.download": chain_download})
            result = plugin.reconcile_downloads()
            self.assertTrue(result["success"])
            self.assertEqual(calls[0]["hashs"], ["2" * 40])
            self.assertEqual(plugin.api_tasks()["data"]["items"][0]["state"], "READY_TO_TRANSFER")


if __name__ == "__main__":
    unittest.main()
