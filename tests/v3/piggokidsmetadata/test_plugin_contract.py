"""在轻量宿主替身下验证 V3 插件边界和持久化幂等性。"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import types
import unittest
from enum import Enum
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugins.v3" / "piggokidsmetadata"


class FakeResponse:
    def __class_getitem__(cls, _: Any) -> type["FakeResponse"]:
        return cls

    def __init__(self, success: bool, data: Any = None, message: str = "") -> None:
        self.success = success
        self.data = data
        self.message = message


class FakeMediaInfo:
    def __init__(self, **values: Any) -> None:
        self.__dict__.update(values)


class FakeFileItem(FakeMediaInfo):
    pass


class FakeMediaSource(str):
    @property
    def value(self) -> str:
        return str(self)


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


def load_plugin_module() -> types.ModuleType:
    """安装最小公开宿主合同并导入插件包。"""

    app = types.ModuleType("app")
    schemas = types.ModuleType("app.schemas")
    schemas.Response = FakeResponse
    schemas.MediaInfo = FakeMediaInfo
    schemas.FileItem = FakeFileItem
    app.schemas = schemas

    plugins = types.ModuleType("app.plugins")
    plugins._PluginBase = FakePluginBase

    schema_types = types.ModuleType("app.schemas.types")
    schema_types.MediaSource = FakeMediaSource
    schema_types.MediaType = FakeMediaType
    schema_types.EventType = FakeEventType

    sdk = types.ModuleType("app.sdk")
    sdk_logging = types.ModuleType("app.sdk.logging")
    sdk_logging.logger = FakeLogger()
    sdk_events = types.ModuleType("app.sdk.events")
    sdk_events.Event = FakeEvent
    sdk_events.eventmanager = FakeEventManager()

    sys.modules.update({
        "app": app,
        "app.schemas": schemas,
        "app.plugins": plugins,
        "app.schemas.types": schema_types,
        "app.sdk": sdk,
        "app.sdk.logging": sdk_logging,
        "app.sdk.events": sdk_events,
    })

    module_name = "piggokidsmetadata_v3_contract"
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


class PluginContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_plugin_module()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        payload = self.root / "KidsMovie"
        payload.mkdir()
        (payload / "KidsMovie.mkv").write_bytes(b"")
        (payload / "poster.jpg").write_bytes(b"")
        (payload / "movie.nfo").write_text(
            "<movie><title>儿童电影</title><year>2024</year><genre>儿童</genre></movie>",
            encoding="utf-8",
        )
        self.plugin = self.module.PigGoKidsMetadata()
        self.plugin.init_plugin({
            "enabled": True,
            "scan_root": str(self.root),
            "minimum_confidence": 0.8,
        })

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_all_dynamic_apis_require_login(self) -> None:
        apis = self.plugin.get_api()
        self.assertEqual({item["auth"] for item in apis}, {"bear"})
        self.assertEqual({item["path"] for item in apis}, {
            "/status", "/registry", "/scan", "/candidates",
            "/candidates/refresh", "/candidates/import", "/candidates/download",
            "/tasks", "/tasks/retry",
        })

    def test_media_source_and_exact_recognition_use_same_identity(self) -> None:
        response = self.plugin.api_scan({"relative_path": "KidsMovie", "site_item_id": "123"})
        self.assertTrue(response.success)
        media_id = response.data["decision"]["item"]["media_id"]
        media = self.plugin.recognize_media(
            media_source=self.module.PLUGIN_SOURCE,
            media_id=media_id,
        )
        self.assertIsNotNone(media)
        self.assertEqual(media.media_source, self.module.PLUGIN_SOURCE)
        self.assertEqual(media.media_id, media_id)
        source = self.plugin.get_media_source()[0]
        self.assertEqual(source["media_source"], self.module.PLUGIN_SOURCE)

    def test_repeated_scan_upserts_task_and_decision(self) -> None:
        first = self.plugin.api_scan({"relative_path": "KidsMovie"})
        second = self.plugin.api_scan({"relative_path": "KidsMovie"})
        self.assertTrue(first.success)
        self.assertTrue(second.success)
        status = self.plugin.api_status().data
        self.assertEqual(status["task_count"], 1)
        self.assertEqual(status["decision_count"], 1)
        self.assertEqual(status["registry_count"], 1)

    def test_review_item_is_not_exposed_as_recognizable_media(self) -> None:
        mixed = self.root / "Mixed"
        mixed.mkdir()
        (mixed / "Mixed.mkv").write_bytes(b"")
        (mixed / "movie.nfo").write_text(
            "<movie><title>冲突条目</title><year>2024</year></movie>",
            encoding="utf-8",
        )
        (mixed / "tvshow.nfo").write_text(
            "<tvshow><title>冲突条目</title><year>2024</year></tvshow>",
            encoding="utf-8",
        )
        response = self.plugin.api_scan({"relative_path": "Mixed"})
        self.assertTrue(response.success)
        self.assertFalse(response.data["decision"]["auto_eligible"])
        media_id = response.data["task"]["media_id"]
        self.assertNotIn(media_id, self.plugin._load_registry())

    def test_invalid_site_identifier_is_not_persisted(self) -> None:
        response = self.plugin.api_scan({
            "relative_path": "KidsMovie",
            "site_item_id": "passkey=never-store-this",
        })
        self.assertTrue(response.success)
        task = response.data["task"]
        self.assertIsNone(task["site_item_id"])
        self.assertNotIn("never-store-this", str(self.plugin._plugin_data))

    def test_download_mapping_only_accepts_infohash(self) -> None:
        valid_hash = "a" * 40
        response = self.plugin.api_scan({
            "relative_path": "KidsMovie",
            "download_hash": valid_hash,
        })
        self.assertTrue(response.success)
        self.assertEqual(response.data["task"]["download_hash"], valid_hash)

        rejected = self.plugin.api_scan({
            "relative_path": "KidsMovie",
            "download_hash": "token=never-store-this",
        })
        self.assertTrue(rejected.success)
        self.assertIsNone(rejected.data["task"]["download_hash"])
        self.assertNotIn("never-store-this", str(self.plugin._plugin_data))

    def test_status_does_not_expose_absolute_scan_root(self) -> None:
        status = self.plugin.api_status()
        self.assertNotIn(str(self.root), str(status.data))
        self.assertTrue(status.data["scan_root_configured"])

    def test_pasted_candidate_download_is_idempotent_and_does_not_persist_secret(self) -> None:
        secret = "abcdefghijklmnopqrstuvwxyz123456"
        imported = self.plugin.api_import_candidate({
            "download_reference": f"https://piggo.example/download.php?id=77&passkey={secret}",
            "title": "儿童电影",
            "media_type": "movie",
        })
        self.assertTrue(imported.success)
        candidate_id = imported.data["candidate"]["candidate_id"]
        calls = []

        def submit(candidate, reference):
            calls.append((candidate.candidate_id, reference))
            return "a" * 40

        self.plugin._submit_download_to_host = submit
        first = self.plugin.api_download_candidate({"candidate_id": candidate_id})
        second = self.plugin.api_download_candidate({"candidate_id": candidate_id})
        self.assertTrue(first.success)
        self.assertTrue(second.success)
        self.assertEqual(len(calls), 1)
        self.assertEqual(first.data["task"]["state"], "DOWNLOADING")
        self.assertNotIn(secret, json.dumps(self.plugin._plugin_data, ensure_ascii=False))

    def test_transfer_failed_scans_payload_and_completion_is_idempotent(self) -> None:
        imported = self.plugin.api_import_candidate({
            "download_reference": "https://piggo.example/download.php?id=78&passkey=abcdefghijklmnopqrstuvwxyz123456",
            "title": "儿童电影",
            "media_type": "movie",
        })
        candidate_id = imported.data["candidate"]["candidate_id"]
        self.plugin._submit_download_to_host = lambda *_: "b" * 40
        submitted = self.plugin.api_download_candidate({"candidate_id": candidate_id})
        self.assertTrue(submitted.success)
        self.plugin.on_transfer_failed(FakeEvent({
            "download_hash": "b" * 40,
            "fileitem": FakeFileItem(path=str(self.root / "KidsMovie")),
        }))
        task = self.plugin.api_tasks().data["items"][0]
        self.assertEqual(task["state"], "READY_TO_TRANSFER")
        self.assertEqual(task["relative_source_path"], "KidsMovie")
        self.plugin.on_transfer_complete(FakeEvent({"download_hash": "b" * 40}))
        self.plugin.on_transfer_complete(FakeEvent({"download_hash": "b" * 40}))
        task = self.plugin.api_tasks().data["items"][0]
        self.assertEqual(task["state"], "COMPLETED")

    def test_synchronous_download_event_is_not_overwritten(self) -> None:
        imported = self.plugin.api_import_candidate({
            "download_reference": "magnet:?xt=urn:btih:" + "e" * 40,
            "title": "同步事件样例",
        })
        candidate_id = imported.data["candidate"]["candidate_id"]

        def submit(*_: Any) -> str:
            context = types.SimpleNamespace(
                torrent_info=types.SimpleNamespace(
                    file_list=["Show/episode.mkv", "../unsafe.txt"],
                ),
            )
            self.plugin.on_download_added(FakeEvent({
                "hash": "e" * 40,
                "context": context,
                "downloader": "fake-downloader",
                "source": self.module.DOWNLOAD_SOURCE,
            }))
            return "e" * 40

        self.plugin._submit_download_to_host = submit
        response = self.plugin.api_download_candidate({"candidate_id": candidate_id})
        self.assertTrue(response.success)
        task = response.data["task"]
        self.assertEqual(task["torrent_files"], ["Show/episode.mkv"])
        self.assertEqual(task["downloader"], "fake-downloader")

    def test_restart_recovery_queries_completed_torrent_by_hash(self) -> None:
        imported = self.plugin.api_import_candidate({
            "download_reference": "magnet:?xt=urn:btih:" + "1" * 40,
            "title": "重启恢复样例",
            "media_type": "movie",
        })
        candidate_id = imported.data["candidate"]["candidate_id"]
        self.plugin._submit_download_to_host = lambda *_: "1" * 40
        self.assertTrue(self.plugin.api_download_candidate({"candidate_id": candidate_id}).success)
        calls = []

        class FakeDownloadChain:
            def list_torrents(self, **kwargs: Any) -> list[dict[str, Any]]:
                calls.append(kwargs)
                return [{
                    "hash": "1" * 40,
                    "progress": 100,
                    "path": str(self_root / "KidsMovie"),
                    "downloader": "fake-downloader",
                }]

        self_root = self.root
        chain = types.ModuleType("app.chain")
        chain_download = types.ModuleType("app.chain.download")
        chain_download.DownloadChain = FakeDownloadChain
        sys.modules.update({"app.chain": chain, "app.chain.download": chain_download})
        result = self.plugin.reconcile_downloads()
        self.assertTrue(result["success"])
        self.assertEqual(calls[0]["hashs"], ["1" * 40])
        self.assertEqual(self.plugin.api_tasks().data["items"][0]["state"], "READY_TO_TRANSFER")


if __name__ == "__main__":
    unittest.main()
