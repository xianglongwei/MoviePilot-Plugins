"""MoviePilot V2 兼容层的轻量加载与扫描合同测试。"""

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
                "/status", "/registry", "/scan", "/candidates",
                "/candidates/refresh", "/candidates/import", "/candidates/download",
                "/tasks", "/tasks/retry",
            })

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
        self.assertNotIn(secret, json.dumps(plugin._plugin_data, ensure_ascii=False))

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
            plugin.on_transfer_complete(FakeEvent({"download_hash": "d" * 40}))
            plugin.on_transfer_complete(FakeEvent({"download_hash": "d" * 40}))
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
