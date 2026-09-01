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
                "/tasks/artwork", "/tasks/reconcile", "/tasks/retry-action",
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

    def test_rss_artwork_is_transient_and_rejects_unsafe_sources(self) -> None:
        feeds = sys.modules[f"{self.module.__name__}.feeds"]
        xml = """<rss><channel><item>
          <title>Kids Show S01</title>
          <link>https://piggo.example/details.php?id=321</link>
          <enclosure url="https://piggo.example/download.php?id=321" />
          <description><![CDATA[
            <img src="https://images.example.test/poster.webp?token=abcdefghijklmnopqrstuvwxyz123456" />
            <img src="http://127.0.0.1/private.jpg" />
            <img src="javascript:alert(1)" />
          ]]></description>
        </item></channel></rss>"""
        item = feeds.parse_feed_document(xml, source_feed_id="feed:artwork")[0]
        self.assertEqual(item.artwork_references, (
            "https://images.example.test/poster.webp?token=abcdefghijklmnopqrstuvwxyz123456",
        ))
        self.assertNotIn(
            "abcdefghijklmnopqrstuvwxyz123456",
            str(item.candidate.to_dict()),
        )

    def test_only_credential_free_https_artwork_is_shared_with_host(self) -> None:
        plugin = self.module.PigGoKidsMetadata()
        plugin.init_plugin({"enabled": True})
        plugin._candidate_artwork_references["candidate:test"] = (
            "https://m.ykimg.com/***",
            "https://images.example.test/poster.jpg?token=secret",
            "http://images.example.test/poster.jpg",
            "https://images.example.test/public/poster.jpg",
        )
        self.assertEqual(
            plugin._display_artwork_reference("candidate:test"),
            "https://images.example.test/public/poster.jpg",
        )

    def test_candidate_api_exposes_safe_transient_poster(self) -> None:
        plugin = self.module.PigGoKidsMetadata()
        plugin.init_plugin({"enabled": True})
        imported = plugin.api_import_candidate({
            "download_reference": "magnet:?xt=urn:btih:" + "7" * 40,
            "title": "海报候选",
        })
        candidate_id = imported["data"]["candidate"]["candidate_id"]
        plugin._candidate_artwork_references[candidate_id] = (
            "https://m.ykimg.com/***",
            "https://images.example.test/poster.jpg",
        )
        candidate = plugin.api_candidates()["data"]["items"][0]
        self.assertEqual(candidate["poster_url"], "https://images.example.test/poster.jpg")

    def test_plugin_download_is_reserved_from_host_auto_transfer(self) -> None:
        calls = []

        class FakeDownloadChain:
            @staticmethod
            def set_torrents_tag(**kwargs: Any) -> bool:
                calls.append(kwargs)
                return True

        chain = types.ModuleType("app.chain")
        chain_download = types.ModuleType("app.chain.download")
        chain_download.DownloadChain = FakeDownloadChain
        sys.modules.update({"app.chain": chain, "app.chain.download": chain_download})
        task = self.module.ImportTask(
            task_id="reserved-download",
            download_hash="8" * 40,
            downloader="QB",
        )
        self.assertTrue(self.module.PigGoKidsMetadata._reserve_download_for_plugin(task))
        self.assertEqual(calls, [{
            "hashs": "8" * 40,
            "tags": ["piggokids", "已整理"],
            "downloader": "QB",
        }])

    def test_existing_host_histories_receive_artwork(self) -> None:
        class FakeHistory:
            def __init__(self, *, poster: str = "", image: str = "") -> None:
                self.poster = poster
                self.image = image

            def update(self, _: Any, payload: dict[str, Any]) -> None:
                self.__dict__.update(payload)

        download_history = FakeHistory()
        transfer_histories = [FakeHistory(), FakeHistory(image="existing")]

        class FakeDownloadHistoryOper:
            _db = None

            @staticmethod
            def get_by_hash(_: str) -> Any:
                return download_history

        class FakeTransferHistoryOper:
            _db = None

            @staticmethod
            def list_by_hash(_: str) -> list[Any]:
                return transfer_histories

        app_db = types.ModuleType("app.db")
        download_oper = types.ModuleType("app.db.downloadhistory_oper")
        download_oper.DownloadHistoryOper = FakeDownloadHistoryOper
        transfer_oper = types.ModuleType("app.db.transferhistory_oper")
        transfer_oper.TransferHistoryOper = FakeTransferHistoryOper
        sys.modules.update({
            "app.db": app_db,
            "app.db.downloadhistory_oper": download_oper,
            "app.db.transferhistory_oper": transfer_oper,
        })
        task = self.module.ImportTask(task_id="artwork", download_hash="a" * 40)
        updated = self.module.PigGoKidsMetadata._backfill_host_history_artwork(
            task,
            "https://images.example.test/poster.jpg",
        )
        self.assertEqual(updated, 2)
        self.assertEqual(download_history.poster, "https://images.example.test/poster.jpg")
        self.assertEqual(download_history.image, "https://images.example.test/poster.jpg")
        self.assertEqual(transfer_histories[0].image, "https://images.example.test/poster.jpg")
        self.assertEqual(transfer_histories[1].image, "existing")

    def test_transfer_history_reconciles_missed_success_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Show"
            target = root / "Library" / "Show (2026)" / "Season 1"
            source.mkdir()
            target.mkdir(parents=True)
            expected = []
            histories = []
            for episode in (1, 2):
                source_file = source / f"Show.S01E{episode:02d}.mp4"
                target_file = target / f"Show S01E{episode:02d}.mp4"
                source_file.write_bytes(b"")
                target_file.write_bytes(b"")
                expected.append(source_file.relative_to(root).as_posix())
                histories.append(types.SimpleNamespace(
                    id=episode,
                    src=str(source_file),
                    dest=str(target_file),
                    dest_storage="local",
                    status=True,
                    type=FakeMediaType.TV.value,
                ))

            class FakeTransferHistoryOper:
                @staticmethod
                def list_by_hash(_: str) -> list[Any]:
                    return histories

            app_db = types.ModuleType("app.db")
            transfer_oper = types.ModuleType("app.db.transferhistory_oper")
            transfer_oper.TransferHistoryOper = FakeTransferHistoryOper
            sys.modules.update({
                "app.db": app_db,
                "app.db.transferhistory_oper": transfer_oper,
            })
            plugin = self.module.PigGoKidsMetadata()
            plugin.init_plugin({"enabled": True, "scan_root": str(root)})
            task = self.module.ImportTask(
                task_id="transfer-reconcile",
                state=self.module.TaskState.TRANSFERRING,
                download_hash="a" * 40,
                relative_source_path="Show",
                transfer_expected_files=expected,
            )
            plugin._save_task(task)
            self.assertEqual(plugin._reconcile_transfer_history(task), "completed")
            restored = plugin._find_task(task.task_id)
            self.assertEqual(restored.state, self.module.TaskState.COMPLETED)
            self.assertEqual(restored.transfer_completed_files, expected)

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

    def test_plugin_owned_conflict_is_locally_identified_and_auto_transferred(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "Mixed"
            payload.mkdir()
            (payload / "Mixed.mkv").write_bytes(b"")
            (payload / "movie.nfo").write_text(
                "<movie><title>插件自动识别</title><year>2024</year></movie>",
                encoding="utf-8",
            )
            (payload / "tvshow.nfo").write_text(
                "<tvshow><title>插件自动识别</title><year>2024</year></tvshow>",
                encoding="utf-8",
            )
            plugin = self.module.PigGoKidsMetadata()
            plugin.init_plugin({
                "enabled": True,
                "scan_root": str(root),
                "auto_transfer": False,
                "public_match_enabled": True,
            })
            imported = plugin.api_import_candidate({
                "download_reference": "magnet:?xt=urn:btih:" + "6" * 40,
                "title": "插件自动识别",
                "media_type": "tv",
            })
            candidate_id = imported["data"]["candidate"]["candidate_id"]
            plugin._submit_download_to_host = lambda *_: "6" * 40
            submitted = plugin.api_download_candidate({"candidate_id": candidate_id})
            task = plugin._find_task(submitted["data"]["task"]["task_id"])
            task.relative_source_path = "Mixed"
            plugin._save_task(task)
            plugin._apply_public_match = lambda *_: self.fail("插件下载不应调用 MP 公共识别")
            transfers = []

            def start_transfer(current: Any, _: dict[str, Any]) -> tuple[bool, str]:
                transfers.append(current.task_id)
                current.transition(self.module.TaskState.TRANSFERRING, "test_auto_transfer")
                plugin._save_task(current)
                return True, "MoviePilot 已接受整理任务"

            plugin._start_host_transfer = start_transfer
            success, decision, message = plugin._scan_task(task, reason="download_poll_completed")
            self.assertTrue(success)
            self.assertTrue(decision["auto_eligible"])
            self.assertEqual(message, "插件已自动识别并提交整理")
            self.assertEqual(transfers, [task.task_id])
            self.assertEqual(plugin._find_task(task.task_id).state, self.module.TaskState.TRANSFERRING)

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

    def test_confirmed_transfer_is_submitted_as_manual_to_clear_failed_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "Show"
            payload.mkdir()
            calls = []

            class FakeMediaInfo:
                def __init__(self, **values: Any) -> None:
                    self.__dict__.update(values)

            class FakeTransferChain:
                def do_transfer(self, **kwargs: Any) -> tuple[bool, str]:
                    calls.append(kwargs)
                    return True, ""

            chain = types.ModuleType("app.chain")
            chain_transfer = types.ModuleType("app.chain.transfer")
            chain_transfer.TransferChain = FakeTransferChain
            core_context = types.ModuleType("app.core.context")
            core_context.MediaInfo = FakeMediaInfo
            module_names = ["app.chain", "app.chain.transfer", "app.core.context"]
            previous = {name: sys.modules.get(name) for name in module_names}
            sys.modules.update({
                "app.chain": chain,
                "app.chain.transfer": chain_transfer,
                "app.core.context": core_context,
            })
            try:
                plugin = self.module.PigGoKidsMetadata()
                plugin.init_plugin({"enabled": True, "scan_root": str(root)})
                task = types.SimpleNamespace(
                    relative_source_path="Show",
                    media_id="piggo:tv:item:1",
                    downloader="QB",
                    download_hash="1" * 40,
                )
                success, _ = plugin._submit_transfer_to_host(task, {
                    "item": {
                        "media_type": "tv",
                        "media_source": "piggokids",
                        "media_id": "piggo:tv:item:1",
                        "title": "测试动画",
                        "season": 1,
                    }
                })
            finally:
                for name, value in previous.items():
                    if value is None:
                        sys.modules.pop(name, None)
                    else:
                        sys.modules[name] = value

            self.assertTrue(success)
            self.assertEqual(len(calls), 1)
            self.assertTrue(calls[0]["manual"])
            self.assertTrue(calls[0]["background"])
            self.assertEqual(calls[0]["mediainfo"].source, "piggokids")
            self.assertEqual(calls[0]["mediainfo"].category, "儿童动画")

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
                        "progress": 1.0,
                        "state": "completed",
                        "path": str(payload),
                    }]

            chain = types.ModuleType("app.chain")
            chain_download = types.ModuleType("app.chain.download")
            chain_download.DownloadChain = FakeDownloadChain
            sys.modules.update({"app.chain": chain, "app.chain.download": chain_download})
            transfers = []

            def start_transfer(task: Any, _: dict[str, Any]) -> tuple[bool, str]:
                transfers.append(task.task_id)
                task.transition(self.module.TaskState.TRANSFERRING, "test_auto_transfer")
                plugin._save_task(task)
                return True, ""

            plugin._start_host_transfer = start_transfer
            result = plugin.reconcile_downloads()
            self.assertTrue(result["success"])
            self.assertEqual(calls[0]["hashs"], ["2" * 40])
            self.assertTrue(calls[0]["include_all_tags"])
            self.assertEqual(transfers, [plugin.api_tasks()["data"]["items"][0]["task_id"]])
            self.assertEqual(plugin.api_tasks()["data"]["items"][0]["state"], "TRANSFERRING")

    def test_download_completion_supports_host_state_and_both_progress_scales(self) -> None:
        completed = self.module.PigGoKidsMetadata._torrent_completed
        self.assertTrue(completed({"state": "completed", "progress": 1.0}))
        self.assertTrue(completed({"progress": 1.0}))
        self.assertTrue(completed({"state": "downloading", "progress": 100}))
        self.assertFalse(completed({"state": "downloading", "progress": 1.0}))
        self.assertFalse(completed({"progress": 0.99}))

    def test_restart_recovery_uses_download_history_when_torrent_is_gone(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "CompletedMovie"
            payload.mkdir()
            (payload / "CompletedMovie.mkv").write_bytes(b"")
            (payload / "movie.nfo").write_text(
                "<movie><title>历史恢复样例</title><year>2024</year></movie>",
                encoding="utf-8",
            )
            plugin = self.module.PigGoKidsMetadata()
            plugin.init_plugin({"enabled": True, "scan_root": str(root)})
            imported = plugin.api_import_candidate({
                "download_reference": "magnet:?xt=urn:btih:" + "3" * 40,
                "title": "历史恢复样例",
                "media_type": "movie",
            })
            candidate_id = imported["data"]["candidate"]["candidate_id"]
            plugin._submit_download_to_host = lambda *_: "3" * 40
            self.assertTrue(plugin.api_download_candidate({"candidate_id": candidate_id})["success"])
            history_calls = []

            class FakeDownloadChain:
                @staticmethod
                def list_torrents(**_: Any) -> list[dict[str, Any]]:
                    return []

            class FakeDownloadHistoryOper:
                def get_by_hashes(self, hashes: list[str]) -> dict[str, Any]:
                    history_calls.append(hashes)
                    return {
                        "3" * 40: types.SimpleNamespace(
                            download_hash="3" * 40,
                            path=str(payload),
                            downloader="fake-downloader",
                        )
                    }

            chain = types.ModuleType("app.chain")
            chain_download = types.ModuleType("app.chain.download")
            chain_download.DownloadChain = FakeDownloadChain
            app_db = types.ModuleType("app.db")
            history_oper = types.ModuleType("app.db.downloadhistory_oper")
            history_oper.DownloadHistoryOper = FakeDownloadHistoryOper
            sys.modules.update({
                "app.chain": chain,
                "app.chain.download": chain_download,
                "app.db": app_db,
                "app.db.downloadhistory_oper": history_oper,
            })
            transfers = []

            def start_transfer(task: Any, _: dict[str, Any]) -> tuple[bool, str]:
                transfers.append(task.task_id)
                task.transition(self.module.TaskState.TRANSFERRING, "test_auto_transfer")
                plugin._save_task(task)
                return True, ""

            plugin._start_host_transfer = start_transfer
            result = plugin.reconcile_downloads()
            self.assertTrue(result["success"])
            self.assertEqual(history_calls, [["3" * 40]])
            self.assertEqual(result["data"]["history_recovered"], 1)
            task = plugin.api_tasks()["data"]["items"][0]
            self.assertEqual(transfers, [task["task_id"]])
            self.assertEqual(task["state"], "TRANSFERRING")
            self.assertEqual(task["relative_source_path"], "CompletedMovie")
            self.assertEqual(task["downloader"], "fake-downloader")
            self.assertIn(
                "download_history_payload",
                {entry["reason"] for entry in task["history"]},
            )


if __name__ == "__main__":
    unittest.main()
