"""PigGo 儿童内容下载后元数据识别插件（MoviePilot V2 兼容层）。"""

from __future__ import annotations

import hashlib
import base64
import binascii
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urljoin, urlsplit

from app import schemas
from app.core.event import Event, eventmanager
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import EventType, MediaType

from .core import (
    VIDEO_EXTENSIONS,
    ImportTask,
    MediaKind,
    PigGoCoreError,
    ScanPolicy,
    TaskState,
    build_contribution_draft,
    evaluate_public_media_match,
    inspect_downloaded_payload,
    normalize_download_hash,
    normalize_site_item_id,
    normalize_title,
)
from .feeds import (
    MAX_FEED_BYTES,
    CandidateStatus,
    FeedCandidate,
    InvalidReferenceError,
    candidate_from_reference,
    extract_artwork_references,
    feed_id_for_url,
    parse_feed_document,
    parse_feed_urls_config,
    reference_fingerprint,
    safe_persisted_artwork_reference,
    upsert_candidates,
    utc_now,
    validate_download_reference,
    validate_public_http_url,
)


REGISTRY_KEY = "local_media_registry_v1"
TASKS_KEY = "import_tasks_v1"
DECISIONS_KEY = "recognition_decisions_v1"
CANDIDATES_KEY = "feed_candidates_v1"
FEED_STATUS_KEY = "feed_status_v1"
MAX_REGISTRY_ITEMS = 500
MAX_CANDIDATE_ITEMS = 1_000
MAX_ARTWORK_BYTES = 8 * 1024 * 1024
DOWNLOAD_SOURCE = "PigGoKidsMetadata"


class PigGoKidsMetadata(_PluginBase):
    """为 V2 提供 RSS 下载、任务恢复和本地元数据识别兼容能力。"""

    plugin_name = "PigGo 儿童动画增强识别"
    plugin_desc = "从 PigGo RSS 或粘贴链接发起下载，并用本地 NFO、图片和文件名增强识别"
    plugin_icon = "https://raw.githubusercontent.com/xianglongwei/MoviePilot-Plugins/main/icons/emby.png"
    plugin_version = "0.7.8"
    plugin_author = "xianglongwei"
    author_url = "https://github.com/xianglongwei/MoviePilot-Plugins"
    plugin_config_prefix = "piggokidsmetadata_"
    plugin_order = 30
    auth_level = 1

    _enabled = False
    _scan_root = ""
    _minimum_confidence = 0.80
    _max_files = 10_000
    _rss_urls: list[str] = []
    _downloader = ""
    _download_save_path = ""
    _auto_transfer = False
    _public_match_enabled = True
    _config_error: Optional[str] = None

    def init_plugin(self, config: Optional[dict[str, Any]] = None) -> None:
        """读取 RSS、下载、扫描和可选整理配置。"""

        values = dict(config or {})
        self._enabled = bool(values.get("enabled", False))
        self._scan_root = str(values.get("scan_root") or "").strip()
        self._downloader = str(values.get("downloader") or "").strip()
        self._download_save_path = str(values.get("download_save_path") or "").strip()
        self._auto_transfer = bool(values.get("auto_transfer", False))
        self._public_match_enabled = bool(values.get("public_match_enabled", True))
        self._config_error = None
        self._state_lock = threading.RLock()
        self._submission_lock = threading.RLock()
        self._candidate_download_references: dict[str, str] = {}
        self._candidate_artwork_references: dict[str, tuple[str, ...]] = {}
        self._active_submission_task_id: Optional[str] = None
        try:
            self._rss_urls = parse_feed_urls_config(values.get("rss_urls"))
        except InvalidReferenceError:
            self._rss_urls = []
            self._config_error = "invalid_rss_url"
            logger.error("PigGoKidsMetadata V2 RSS 配置无效：invalid_rss_url")
        try:
            self._minimum_confidence = max(
                0.0,
                min(1.0, float(values.get("minimum_confidence", 0.80))),
            )
        except (TypeError, ValueError):
            self._minimum_confidence = 0.80
        try:
            self._max_files = max(100, min(50_000, int(values.get("max_files", 10_000))))
        except (TypeError, ValueError):
            self._max_files = 10_000
        self._repair_placeholder_download_titles()
        self._restore_uploaded_artwork_cache()

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> list[dict[str, Any]]:
        return []

    @staticmethod
    def get_render_mode() -> tuple[str, str]:
        """使用 MoviePilot V2 联邦组件渲染配置、详情和全页工作台。"""

        remote_entry = Path(__file__).resolve().parent / "dist" / "assets" / "remoteEntry.js"
        if remote_entry.is_file():
            return "vue", "dist/assets"
        return "vuetify", ""

    def get_sidebar_nav(self) -> list[dict[str, Any]]:
        if not self._enabled or self.get_render_mode()[0] != "vue":
            return []
        return [{
            "nav_key": "main",
            "title": "PigGo 儿童内容",
            "icon": "mdi-movie-open-star",
            "section": "organize",
            "permission": "manage",
            "order": 30,
        }]

    def get_api(self) -> list[dict[str, Any]]:
        """V2 接口返回明确的 ``success/message/data`` 结构。"""

        return [
            {
                "path": "/status",
                "endpoint": self.api_status,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "获取 PigGoKidsMetadata 运行状态",
            },
            {
                "path": "/registry",
                "endpoint": self.api_registry,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "读取本地媒体登记表",
            },
            {
                "path": "/feeds",
                "endpoint": self.api_feeds,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "读取脱敏 RSS 抓取状态",
            },
            {
                "path": "/contribution-drafts",
                "endpoint": self.api_contribution_drafts,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "生成只读 TMDb 贡献草稿",
            },
            {
                "path": "/scan",
                "endpoint": self.api_scan,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "扫描下载根目录内的一个相对路径",
            },
            {
                "path": "/candidates",
                "endpoint": self.api_candidates,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "筛选已抓取的候选资源",
            },
            {
                "path": "/candidates/refresh",
                "endpoint": self.api_refresh_candidates,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "立即刷新 RSS 候选",
            },
            {
                "path": "/candidates/import",
                "endpoint": self.api_import_candidate,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "导入用户粘贴的下载引用",
            },
            {
                "path": "/downloads/manual",
                "endpoint": self.api_manual_download,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "直接下载用户粘贴的引用",
            },
            {
                "path": "/candidates/ignore",
                "endpoint": self.api_ignore_candidate,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "忽略或恢复尚未建立任务的候选",
            },
            {
                "path": "/candidates/update",
                "endpoint": self.api_update_candidate,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "修正尚未建立任务的候选标题和类型",
            },
            {
                "path": "/candidates/download",
                "endpoint": self.api_download_candidate,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "通过 MoviePilot 下载候选资源",
            },
            {
                "path": "/candidates/download-action",
                "endpoint": self.api_download_candidate_action,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "从插件详情页下载候选资源",
            },
            {
                "path": "/tasks",
                "endpoint": self.api_tasks,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "读取插件下载与整理任务",
            },
            {
                "path": "/tasks/retry",
                "endpoint": self.api_retry_task,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "重试扫描或整理任务",
            },
            {
                "path": "/tasks/review",
                "endpoint": self.api_review_task,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "批准或忽略待人工审核任务",
            },
            {
                "path": "/tasks/artwork",
                "endpoint": self.api_task_artwork,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "从 RSS 为已整理任务补写本地封面",
            },
            {
                "path": "/tasks/artwork-upload",
                "endpoint": self.api_upload_task_artwork,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "上传图片并为已整理任务补写封面",
            },
            {
                "path": "/tasks/reconcile",
                "endpoint": self.api_reconcile_tasks,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "根据 MoviePilot 历史恢复下载与整理任务状态",
            },
            {
                "path": "/tasks/retry-action",
                "endpoint": self.api_retry_task_action,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "从插件详情页重试任务",
            },
        ]

    def get_form(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VAlert",
                        "props": {
                            "type": "info",
                            "variant": "tonal",
                            "text": "V2 工作台支持候选下载、冲突审核和只读 TMDb 精确匹配。完整私密链接不会写入候选记录；自动整理默认关闭。",
                        },
                    },
                    {
                        "component": "VSwitch",
                        "props": {"model": "enabled", "label": "启用插件"},
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "rss_urls",
                            "label": "PigGo RSS 地址（多个请换行）",
                            "type": "password",
                            "autocomplete": "new-password",
                        },
                    },
                    {
                        "component": "VAlert",
                        "props": {
                            "type": "warning",
                            "variant": "tonal",
                            "text": "插件不会定时或隐式刷新 RSS；只有在工作台主动点击“刷新 RSS”才会访问站点。",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "downloader",
                            "label": "MoviePilot 下载器名称（可留空）",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "download_save_path",
                            "label": "MoviePilot 下载保存路径（可留空）",
                        },
                    },
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "auto_transfer",
                            "label": "手工扫描的高置信度任务自动整理",
                            "hint": "插件提交的下载固定由插件本地识别并自动整理，不受此开关影响。",
                            "persistentHint": True,
                        },
                    },
                    {
                        "component": "VSwitch",
                        "props": {
                            "model": "public_match_enabled",
                            "label": "优先尝试 TMDb 精确匹配（只读）",
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "scan_root",
                            "label": "允许扫描的下载根目录",
                            "placeholder": "/media/downloads",
                            "hint": "手工扫描只能提交该目录内的相对路径；不会跟随符号链接。",
                            "persistentHint": True,
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "minimum_confidence",
                            "label": "最低自动化置信度",
                            "type": "number",
                            "min": 0,
                            "max": 1,
                            "step": 0.05,
                        },
                    },
                    {
                        "component": "VTextField",
                        "props": {
                            "model": "max_files",
                            "label": "单个内容包最大扫描文件数",
                            "type": "number",
                            "min": 100,
                            "max": 50000,
                        },
                    },
                ],
            }
        ], {
            "enabled": False,
            "rss_urls": "",
            "downloader": "",
            "download_save_path": "",
            "auto_transfer": False,
            "public_match_enabled": True,
            "scan_root": "",
            "minimum_confidence": 0.80,
            "max_files": 10_000,
        }

    def get_page(self) -> list[dict[str, Any]]:
        tasks = self._load_tasks()
        candidates = self._load_candidates()
        drafts = self._contribution_drafts()
        tasks_by_id = {str(item.get("task_id") or ""): item for item in tasks}
        last_task = tasks[-1] if tasks else None
        summary = "尚未执行手工内容包扫描。"
        if last_task:
            summary = (
                f"最近任务状态：{last_task.get('state', 'UNKNOWN')}；"
                f"媒体身份：{last_task.get('media_id') or '未生成'}"
            )
        candidate_content: list[dict[str, Any]] = [
            {"component": "VCardTitle", "text": "候选资源"},
            {
                "component": "VBtn",
                "props": {"color": "primary", "variant": "tonal", "class": "ma-3"},
                "text": "立即刷新 RSS",
                "events": {
                    "click": {
                        "api": "plugin/PigGoKidsMetadata/candidates/refresh",
                        "method": "post",
                    }
                },
            },
        ]
        for item in reversed(candidates[-20:]):
            if poster_url := self._display_artwork_reference(item.candidate_id):
                candidate_content.append({
                    "component": "VImg",
                    "props": {
                        "src": poster_url,
                        "width": 92,
                        "height": 124,
                        "cover": True,
                        "class": "mx-4 mt-3 rounded",
                    },
                })
            candidate_content.append({
                "component": "VCardText",
                "text": f"{item.title}｜{item.media_type.value}｜{item.status.value}",
            })
            task = tasks_by_id.get(str(item.task_id or ""))
            if task and task.get("state") in {
                TaskState.RETRYABLE_FAILED.value,
                TaskState.READY_TO_TRANSFER.value,
            }:
                candidate_content.append({
                    "component": "VBtn",
                    "props": {"size": "small", "variant": "outlined", "class": "mx-4 mb-3"},
                    "text": "重试任务",
                    "events": {"click": {
                        "api": (
                            "plugin/PigGoKidsMetadata/tasks/retry-action?task_id="
                            f"{quote(str(item.task_id), safe='')}"
                        ),
                        "method": "post",
                    }},
                })
            elif item.status == CandidateStatus.DISCOVERED:
                candidate_content.append({
                    "component": "VBtn",
                    "props": {"size": "small", "color": "primary", "class": "mx-4 mb-3"},
                    "text": "选择并下载",
                    "events": {"click": {
                        "api": (
                            "plugin/PigGoKidsMetadata/candidates/download-action?candidate_id="
                            f"{quote(item.candidate_id, safe='')}"
                        ),
                        "method": "post",
                    }},
                })
        if not candidates:
            candidate_content.append({
                "component": "VCardText",
                "text": "尚无候选，请配置 RSS 后点击刷新。",
            })
        return [
            {
                "component": "VAlert",
                "props": {
                    "type": "success" if self._enabled and self._scan_root else "warning",
                    "variant": "tonal",
                    "text": (
                        f"V2 工作台已启用：{len(candidates)} 个候选，{len(tasks)} 个任务，"
                        f"{len(drafts)} 份只读贡献草稿。"
                        if self._enabled
                        else "请先启用插件并配置下载根目录。"
                    ),
                },
            },
            {
                "component": "VCard",
                "props": {"variant": "tonal", "class": "mt-3"},
                "content": [
                    {"component": "VCardTitle", "text": "本地识别状态"},
                    {"component": "VCardText", "text": summary},
                ],
            },
            {
                "component": "VCard",
                "props": {"variant": "tonal", "class": "mt-3"},
                "content": candidate_content,
            },
        ]

    def stop_service(self) -> None:
        self._enabled = False
        self._candidate_download_references = {}
        self._active_submission_task_id = None

    def get_service(self) -> list[dict[str, Any]]:
        if not self._enabled:
            return []
        from apscheduler.triggers.interval import IntervalTrigger

        return [{
            "id": "PigGoKidsMetadata.DownloadTracking",
            "name": "PigGo 儿童下载状态恢复",
            "trigger": IntervalTrigger(minutes=5),
            "func": self.reconcile_downloads,
            "kwargs": {},
        }]

    def _load_registry(self) -> dict[str, dict[str, Any]]:
        raw = self.get_data(REGISTRY_KEY) or {}
        return dict(raw) if isinstance(raw, dict) else {}

    def _load_tasks(self) -> list[dict[str, Any]]:
        raw = self.get_data(TASKS_KEY) or []
        return list(raw) if isinstance(raw, list) else []

    def _load_decisions(self) -> dict[str, dict[str, Any]]:
        raw = self.get_data(DECISIONS_KEY) or {}
        return dict(raw) if isinstance(raw, dict) else {}

    def _load_candidates(self) -> list[FeedCandidate]:
        raw = self.get_data(CANDIDATES_KEY) or []
        candidates = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            try:
                candidates.append(FeedCandidate.from_dict(item))
            except (TypeError, ValueError):
                continue
        return candidates

    def _save_candidates(self, candidates: list[FeedCandidate]) -> None:
        with self._state_lock:
            self.save_data(CANDIDATES_KEY, [item.to_dict() for item in candidates[-MAX_CANDIDATE_ITEMS:]])

    def _load_feed_status(self) -> dict[str, dict[str, Any]]:
        raw = self.get_data(FEED_STATUS_KEY) or {}
        return dict(raw) if isinstance(raw, dict) else {}

    def _save_feed_status(self, status: dict[str, dict[str, Any]]) -> None:
        self.save_data(FEED_STATUS_KEY, status)

    def _save_registry_item(self, item: dict[str, Any]) -> None:
        with self._state_lock:
            registry = self._load_registry()
            registry[str(item["media_id"])] = item
            if len(registry) > MAX_REGISTRY_ITEMS:
                registry = dict(list(registry.items())[-MAX_REGISTRY_ITEMS:])
            self.save_data(REGISTRY_KEY, registry)

    def _save_task(self, task: ImportTask) -> None:
        with self._state_lock:
            tasks = [item for item in self._load_tasks() if item.get("task_id") != task.task_id]
            tasks.append(task.to_dict())
            self.save_data(TASKS_KEY, tasks[-MAX_REGISTRY_ITEMS:])

    def _find_task(self, task_id: str) -> Optional[ImportTask]:
        for item in reversed(self._load_tasks()):
            if item.get("task_id") == task_id:
                return ImportTask.from_dict(item)
        return None

    def _find_task_by_hash(self, download_hash: Any) -> Optional[ImportTask]:
        normalized = normalize_download_hash(download_hash)
        if not normalized:
            return None
        for item in reversed(self._load_tasks()):
            if normalize_download_hash(item.get("download_hash")) == normalized:
                return ImportTask.from_dict(item)
        return None

    def _update_candidate(
        self,
        candidate_id: Optional[str],
        *,
        status: CandidateStatus,
        task_id: Optional[str] = None,
    ) -> None:
        if not candidate_id:
            return
        with self._state_lock:
            candidates = self._load_candidates()
            for item in candidates:
                if item.candidate_id == candidate_id:
                    item.status = status
                    if task_id:
                        item.task_id = task_id
                    self._save_candidates(candidates)
                    return

    @staticmethod
    def _is_placeholder_download_title(value: Any) -> bool:
        """判断标题是否只是下载接口文件名，而非真实资源名。"""

        title = str(value or "").strip().casefold()
        return title in {"download", "download.php", "download.torrent", "手工粘贴资源"}

    def _adopt_download_name(self, task: ImportTask, value: Any) -> Optional[str]:
        """用下载器解析出的种子名纠正候选和 MP 历史中的占位标题。"""

        name = Path(str(value or "").strip()).name.strip()[:500]
        if not name or self._is_placeholder_download_title(name):
            return None
        if name.casefold().endswith(".torrent"):
            name = name[:-8].strip()
        if not name:
            return None
        with self._state_lock:
            candidates = self._load_candidates()
            for candidate in candidates:
                if (
                    candidate.candidate_id == task.candidate_id
                    and not candidate.title_overridden
                    and self._is_placeholder_download_title(candidate.title)
                ):
                    candidate.title = name
                    candidate.updated_at = utc_now()
                    self._save_candidates(candidates)
                    break
        if not task.download_hash:
            return name
        try:
            from app.db.downloadhistory_oper import DownloadHistoryOper

            oper = DownloadHistoryOper()
            history = oper.get_by_hash(task.download_hash)
            if history:
                payload = {}
                if self._is_placeholder_download_title(getattr(history, "title", None)):
                    payload["title"] = name
                if self._is_placeholder_download_title(getattr(history, "torrent_name", None)):
                    payload["torrent_name"] = name
                if payload:
                    history.update(oper._db, payload)
        except Exception:
            logger.error("PigGoKidsMetadata V2 下载标题回填失败：unexpected_error")
        return name

    def _recover_download_name_from_host(self, task: ImportTask) -> Optional[str]:
        """按 hash 从本地下载器读取真实种子名，不访问资源站点。"""

        if not task.download_hash:
            return None
        try:
            from app.chain.download import DownloadChain

            chain = DownloadChain()
            try:
                torrents = chain.list_torrents(
                    downloader=task.downloader or self._downloader or None,
                    hashs=[task.download_hash],
                    include_all_tags=True,
                ) or []
            except TypeError:
                torrents = chain.list_torrents(
                    downloader=task.downloader or self._downloader or None,
                    hashs=[task.download_hash],
                ) or []
            for torrent in torrents:
                values = self._record_values(torrent)
                if normalize_download_hash(values.get("hash")) == normalize_download_hash(task.download_hash):
                    return self._adopt_download_name(task, values.get("name"))
        except Exception:
            logger.error("PigGoKidsMetadata V2 下载名称恢复失败：unexpected_error")
        return None

    def _repair_placeholder_download_titles(self) -> int:
        """插件加载时修复既有已完成任务的 download.php 占位标题。"""

        repaired = 0
        for raw in self._load_tasks():
            try:
                task = ImportTask.from_dict(raw)
            except (TypeError, ValueError):
                continue
            if not task.relative_source_path:
                continue
            candidate = next(
                (
                    item for item in self._load_candidates()
                    if item.candidate_id == task.candidate_id
                ),
                None,
            )
            if not candidate or not self._is_placeholder_download_title(candidate.title):
                continue
            if self._adopt_download_name(task, Path(task.relative_source_path).name):
                repaired += 1
        return repaired

    def _save_decision(self, task_id: str, decision: dict[str, Any]) -> None:
        with self._state_lock:
            decisions = self._load_decisions()
            decisions[task_id] = decision
            if len(decisions) > MAX_REGISTRY_ITEMS:
                decisions = dict(list(decisions.items())[-MAX_REGISTRY_ITEMS:])
            self.save_data(DECISIONS_KEY, decisions)

    @staticmethod
    def _fetch_feed_content(url: str) -> tuple[bytes, int]:
        from app.utils.http import RequestUtils

        current_url = url
        for _ in range(6):
            validate_public_http_url(current_url)
            response = RequestUtils(timeout=20).get_res(
                current_url,
                stream=True,
                allow_redirects=False,
            )
            if response is None:
                raise PigGoCoreError("RSS 请求没有返回响应")
            try:
                status_code = int(getattr(response, "status_code", 0) or 0)
                if status_code in {301, 302, 303, 307, 308}:
                    location = str((getattr(response, "headers", {}) or {}).get("Location") or "")
                    if not location:
                        raise PigGoCoreError("RSS 重定向缺少目标地址")
                    current_url = urljoin(current_url, location)
                    continue
                if status_code != 200:
                    raise PigGoCoreError(f"RSS 请求返回 HTTP {status_code or 'unknown'}")
                headers = getattr(response, "headers", {}) or {}
                try:
                    content_length = int(headers.get("Content-Length") or 0)
                except (TypeError, ValueError):
                    content_length = 0
                if content_length > MAX_FEED_BYTES:
                    raise PigGoCoreError("RSS 内容超过安全大小限制")
                chunks: list[bytes] = []
                total = 0
                iterator = getattr(response, "iter_content", None)
                source = (
                    iterator(chunk_size=64 * 1024)
                    if callable(iterator)
                    else [getattr(response, "content", b"")]
                )
                for chunk in source:
                    data = bytes(chunk or b"")
                    total += len(data)
                    if total > MAX_FEED_BYTES:
                        raise PigGoCoreError("RSS 内容超过安全大小限制")
                    chunks.append(data)
                return b"".join(chunks), status_code
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
        raise PigGoCoreError("RSS 重定向次数过多")

    @staticmethod
    def _fetch_artwork_content(url: str) -> tuple[bytes, str]:
        """下载并校验一张公网图片，限制重定向、类型和响应大小。"""

        from app.utils.http import RequestUtils

        current_url = url
        headers = {
            "User-Agent": "Mozilla/5.0 (MoviePilot PigGoKidsMetadata)",
            "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8",
        }
        for _ in range(6):
            validate_public_http_url(current_url)
            response = RequestUtils(headers=headers, timeout=20).get_res(
                current_url,
                stream=True,
                allow_redirects=False,
            )
            if response is None:
                raise PigGoCoreError("封面请求没有返回响应")
            try:
                status_code = int(getattr(response, "status_code", 0) or 0)
                if status_code in {301, 302, 303, 307, 308}:
                    location = str((getattr(response, "headers", {}) or {}).get("Location") or "")
                    if not location:
                        raise PigGoCoreError("封面重定向缺少目标地址")
                    current_url = urljoin(current_url, location)
                    continue
                if status_code != 200:
                    raise PigGoCoreError(f"封面请求返回 HTTP {status_code or 'unknown'}")
                response_headers = getattr(response, "headers", {}) or {}
                content_type = str(response_headers.get("Content-Type") or "").split(";", 1)[0].casefold()
                if content_type and content_type not in {
                    "image/jpeg", "image/png", "image/webp", "application/octet-stream",
                }:
                    raise PigGoCoreError("封面响应不是受支持的图片类型")
                try:
                    content_length = int(response_headers.get("Content-Length") or 0)
                except (TypeError, ValueError):
                    content_length = 0
                if content_length > MAX_ARTWORK_BYTES:
                    raise PigGoCoreError("封面超过安全大小限制")
                chunks: list[bytes] = []
                total = 0
                iterator = getattr(response, "iter_content", None)
                source = (
                    iterator(chunk_size=64 * 1024)
                    if callable(iterator)
                    else [getattr(response, "content", b"")]
                )
                for chunk in source:
                    data = bytes(chunk or b"")
                    total += len(data)
                    if total > MAX_ARTWORK_BYTES:
                        raise PigGoCoreError("封面超过安全大小限制")
                    chunks.append(data)
                content = b"".join(chunks)
                if content.startswith(b"\xff\xd8\xff"):
                    extension = ".jpg"
                elif content.startswith(b"\x89PNG\r\n\x1a\n"):
                    extension = ".png"
                elif len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
                    extension = ".webp"
                else:
                    raise PigGoCoreError("封面文件签名无效或格式不受支持")
                return content, extension
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
        raise PigGoCoreError("封面重定向次数过多")

    @staticmethod
    def _existing_poster(target_dir: Path) -> Optional[Path]:
        for name in ("poster.jpg", "poster.jpeg", "poster.png", "poster.webp"):
            candidate = target_dir / name
            if candidate.exists() or candidate.is_symlink():
                return candidate
        return None

    @classmethod
    def _write_artwork(cls, target_dir: Path, content: bytes, extension: str) -> Path:
        """以原子替换写入 poster，且不覆盖已有的人工封面。"""

        directory = target_dir.expanduser().resolve(strict=True)
        if not directory.is_dir():
            raise PigGoCoreError("整理目标不是本地目录")
        if existing := cls._existing_poster(directory):
            return existing
        temporary_name: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".piggokids-poster-",
                suffix=".tmp",
                dir=directory,
                delete=False,
            ) as handle:
                temporary_name = handle.name
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            target = directory / f"poster{extension}"
            os.replace(temporary_name, target)
            temporary_name = None
            return target
        finally:
            if temporary_name:
                try:
                    Path(temporary_name).unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _event_target_dir(data: dict[str, Any]) -> Optional[Path]:
        transferinfo = data.get("transferinfo")
        directory_item = getattr(transferinfo, "target_diritem", None)
        storage = str(getattr(directory_item, "storage", "") or "").casefold()
        path = getattr(directory_item, "path", None)
        if not path or storage not in {"", "local"}:
            return None
        try:
            directory = Path(str(path)).expanduser().resolve(strict=True)
            return directory if directory.is_dir() else None
        except (OSError, RuntimeError, ValueError):
            return None

    @staticmethod
    def _history_target_dir(task: ImportTask) -> Optional[Path]:
        if not task.download_hash:
            return None
        try:
            from app.db.transferhistory_oper import TransferHistoryOper

            histories = TransferHistoryOper().list_by_hash(task.download_hash)
        except Exception:
            return None
        parents: list[Path] = []
        media_type = ""
        for history in histories or []:
            if not bool(getattr(history, "status", False)):
                continue
            storage = str(getattr(history, "dest_storage", "") or "").casefold()
            destination = getattr(history, "dest", None)
            if not destination or storage not in {"", "local"}:
                continue
            try:
                parent = Path(str(destination)).expanduser().parent.resolve(strict=True)
            except (OSError, RuntimeError, ValueError):
                continue
            if parent.is_dir():
                parents.append(parent)
                media_type = media_type or str(getattr(history, "type", "") or "")
        if not parents:
            return None
        try:
            common = Path(os.path.commonpath([str(path) for path in parents]))
        except (OSError, ValueError):
            return None
        if media_type in {MediaType.TV.value, "tv"} and common.name.casefold().startswith("season "):
            common = common.parent
        return common if common.is_dir() else None

    def _install_task_artwork(
        self,
        task: ImportTask,
        target_dir: Path,
    ) -> tuple[bool, Optional[Path], str]:
        try:
            directory = target_dir.expanduser().resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            return False, None, "整理目标目录不存在"
        if existing := self._existing_poster(directory):
            return True, existing, "目标目录已有封面"
        self._restore_candidate_artwork_references(task.candidate_id)
        references = self._candidate_artwork_references.get(str(task.candidate_id or ""), ())
        if not references:
            return False, None, "RSS 候选中没有可用封面"
        for reference in references:
            try:
                content, extension = self._fetch_artwork_content(reference)
                target = self._write_artwork(directory, content, extension)
                return True, target, "封面已写入媒体目录"
            except (InvalidReferenceError, PigGoCoreError, OSError):
                continue
            except Exception:
                logger.error("PigGoKidsMetadata V2 封面处理失败：unexpected_error")
        return False, None, "RSS 封面下载失败"

    def _display_artwork_reference(self, candidate_id: Optional[str]) -> Optional[str]:
        """只把不含查询参数和凭据的 HTTPS 图片地址交给 MoviePilot 持久化。"""

        self._restore_candidate_artwork_references(candidate_id)
        for reference in self._candidate_artwork_references.get(str(candidate_id or ""), ()):
            try:
                plugin_parts = urlsplit(reference)
            except ValueError:
                plugin_parts = None
            if (
                plugin_parts
                and plugin_parts.path.startswith(
                    "/api/v1/plugin/file/PigGoKidsMetadata/user-artwork/"
                )
                and plugin_parts.scheme.casefold() in {"", "http", "https"}
                and (not plugin_parts.scheme or plugin_parts.hostname)
                and not plugin_parts.username
                and not plugin_parts.password
                and not plugin_parts.query
                and not plugin_parts.fragment
                and ".." not in reference
            ):
                return reference
            if "***" in reference:
                continue
            try:
                parts = urlsplit(reference)
            except ValueError:
                continue
            if (
                parts.scheme.casefold() == "https"
                and parts.hostname
                and not parts.username
                and not parts.password
                and not parts.query
                and not parts.fragment
                and len(reference) <= 2_048
            ):
                return reference
        return None

    @staticmethod
    def _decode_uploaded_artwork(value: Any) -> tuple[bytes, str]:
        """解码受限的 JPEG/PNG/WebP data URL 或纯 Base64。"""

        encoded = str(value or "").strip()
        if encoded.startswith("data:"):
            header, separator, encoded = encoded.partition(",")
            if not separator or ";base64" not in header.casefold():
                raise PigGoCoreError("上传封面必须使用 Base64 编码")
            mime = header[5:].split(";", 1)[0].casefold()
            if mime not in {"image/jpeg", "image/png", "image/webp"}:
                raise PigGoCoreError("封面格式仅支持 JPEG、PNG 或 WebP")
        if not encoded or len(encoded) > (MAX_ARTWORK_BYTES * 4 // 3) + 16:
            raise PigGoCoreError("上传封面为空或超过大小限制")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise PigGoCoreError("上传封面的 Base64 数据无效") from error
        if not content or len(content) > MAX_ARTWORK_BYTES:
            raise PigGoCoreError("上传封面为空或超过大小限制")
        if content.startswith(b"\xff\xd8\xff"):
            return content, ".jpg"
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return content, ".png"
        if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
            return content, ".webp"
        raise PigGoCoreError("封面文件签名无效或格式不受支持")

    @staticmethod
    def _task_artwork_cache_name(task: ImportTask, extension: str) -> str:
        digest = hashlib.sha256(task.task_id.encode("utf-8")).hexdigest()[:32]
        return f"{digest}{extension}"

    @classmethod
    def _cache_task_artwork(cls, task: ImportTask, content: bytes, extension: str) -> Path:
        directory = Path(__file__).resolve().parent / "user-artwork"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / cls._task_artwork_cache_name(task, extension)
        temporary_name: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=".upload-", suffix=".tmp", dir=directory, delete=False
            ) as handle:
                temporary_name = handle.name
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, target)
            temporary_name = None
            return target
        finally:
            if temporary_name:
                try:
                    Path(temporary_name).unlink(missing_ok=True)
                except OSError:
                    pass

    @classmethod
    def _task_artwork_public_url(
        cls,
        task: ImportTask,
        extension: str,
        public_base_url: str = "",
    ) -> str:
        name = cls._task_artwork_cache_name(task, extension)
        path = f"/api/v1/plugin/file/PigGoKidsMetadata/user-artwork/{name}"
        base = str(public_base_url or "").strip().rstrip("/")
        if not base:
            return path
        try:
            parts = urlsplit(base)
        except ValueError as error:
            raise PigGoCoreError("MoviePilot API 地址格式无效") from error
        if (
            parts.scheme.casefold() not in {"http", "https"}
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or parts.path not in {"", "/"}
        ):
            raise PigGoCoreError("MoviePilot API 地址必须是 HTTP(S) 站点根地址")
        return f"{base}{path}"

    def _set_candidate_uploaded_artwork(self, task: ImportTask, artwork_url: str) -> None:
        if not task.candidate_id:
            return
        with self._state_lock:
            candidates = self._load_candidates()
            for candidate in candidates:
                if candidate.candidate_id == task.candidate_id:
                    candidate.poster_url = artwork_url
                    candidate.updated_at = utc_now()
                    self._save_candidates(candidates)
                    self._candidate_artwork_references[candidate.candidate_id] = (artwork_url,)
                    return

    def _restore_uploaded_artwork_cache(self) -> int:
        """升级删除插件目录后，从媒体目录的 poster 重建历史小图副本。"""

        restored = 0
        for raw in self._load_tasks():
            try:
                task = ImportTask.from_dict(raw)
            except (TypeError, ValueError):
                continue
            target_dir = self._history_target_dir(task)
            poster = self._existing_poster(target_dir) if target_dir else None
            if not poster or not poster.is_file():
                continue
            extension = poster.suffix.casefold()
            if extension not in {".jpg", ".jpeg", ".png", ".webp"}:
                continue
            extension = ".jpg" if extension == ".jpeg" else extension
            try:
                content = poster.read_bytes()
                if len(content) > MAX_ARTWORK_BYTES:
                    continue
                cache = self._cache_task_artwork(task, content, extension)
                candidate = next(
                    (
                        item for item in self._load_candidates()
                        if item.candidate_id == task.candidate_id
                    ),
                    None,
                )
                existing_url = str(getattr(candidate, "poster_url", "") or "")
                expected_path = self._task_artwork_public_url(task, cache.suffix)
                artwork_url = existing_url if existing_url.endswith(expected_path) else expected_path
                self._set_candidate_uploaded_artwork(task, artwork_url)
                self._backfill_host_history_artwork(task, artwork_url)
                restored += 1
            except OSError:
                continue
        return restored

    def _restore_candidate_artwork_references(self, candidate_id: Optional[str]) -> None:
        """仅从已持久化候选恢复安全封面，绝不访问或刷新 RSS。"""

        key = str(candidate_id or "")
        if not key or self._candidate_artwork_references.get(key):
            return
        candidates = self._load_candidates()
        candidate = next((item for item in candidates if item.candidate_id == key), None)
        if not candidate:
            return
        related = [candidate]
        if candidate.site_item_id:
            related.extend(
                item for item in candidates
                if item.candidate_id != key and item.site_item_id == candidate.site_item_id
            )
        references: list[str] = []
        for item in related:
            if item.poster_url:
                references.append(item.poster_url)
            references.extend(extract_artwork_references(item.summary or ""))
        if references:
            self._candidate_artwork_references[key] = tuple(dict.fromkeys(references))

    @staticmethod
    def _reserve_download_for_plugin(task: ImportTask) -> bool:
        """标记插件下载已由自身接管，阻止 MP 下载器监控再次自动识别整理。"""

        download_hash = normalize_download_hash(task.download_hash)
        if not download_hash:
            return False
        try:
            from app.chain.download import DownloadChain

            result = DownloadChain().set_torrents_tag(
                hashs=download_hash,
                tags=["piggokids", "已整理"],
                downloader=task.downloader,
            )
            return bool(result)
        except (ImportError, AttributeError):
            return False
        except Exception:
            logger.error("PigGoKidsMetadata V2 下载接管标记失败：unexpected_error")
            return False

    @staticmethod
    def _backfill_host_history_artwork(
        task: ImportTask,
        artwork_url: Optional[str],
        *,
        replace_plugin_artwork: bool = False,
    ) -> int:
        """为既有 MP 下载/整理历史补齐封面字段，不覆盖已有图片。"""

        if not task.download_hash or not artwork_url:
            return 0
        updated = 0
        try:
            from app.db.downloadhistory_oper import DownloadHistoryOper
            from app.db.transferhistory_oper import TransferHistoryOper

            download_oper = DownloadHistoryOper()
            download_history = download_oper.get_by_hash(task.download_hash)
            if download_history:
                payload = {}
                current_poster = str(getattr(download_history, "poster", None) or "")
                current_image = str(getattr(download_history, "image", None) or "")
                if not current_poster or (
                    replace_plugin_artwork and "/user-artwork/" in current_poster
                ):
                    payload["poster"] = artwork_url
                if not current_image or (
                    replace_plugin_artwork and "/user-artwork/" in current_image
                ):
                    payload["image"] = artwork_url
                if payload:
                    download_history.update(download_oper._db, payload)
                    updated += 1
            transfer_oper = TransferHistoryOper()
            for history in transfer_oper.list_by_hash(task.download_hash) or []:
                current_image = str(getattr(history, "image", None) or "")
                if not current_image or (
                    replace_plugin_artwork and "/user-artwork/" in current_image
                ):
                    history.update(transfer_oper._db, {"image": artwork_url})
                    updated += 1
            return updated
        except Exception:
            logger.error("PigGoKidsMetadata V2 历史封面回填失败：unexpected_error")
            return 0

    def refresh_candidates(self) -> dict[str, Any]:
        if not self._enabled:
            return self._response(False, message="插件尚未启用")
        statuses = self._load_feed_status()
        incoming: list[FeedCandidate] = []
        parsed_count = 0
        successful = 0
        for url in self._rss_urls:
            feed_id = feed_id_for_url(url)
            from .core import redact_url

            status = {
                "feed_id": feed_id,
                "url": redact_url(url),
                "last_attempt_at": utc_now(),
                "last_success_at": statuses.get(feed_id, {}).get("last_success_at"),
                "http_status": None,
                "parsed_count": 0,
                "error_code": None,
            }
            try:
                content, http_status = self._fetch_feed_content(url)
                parsed = parse_feed_document(content, source_feed_id=feed_id)
                for item in parsed:
                    self._candidate_download_references[item.candidate.candidate_id] = item.download_reference
                    self._candidate_artwork_references[item.candidate.candidate_id] = item.artwork_references
                    incoming.append(item.candidate)
                status.update({
                    "last_success_at": utc_now(),
                    "http_status": http_status,
                    "parsed_count": len(parsed),
                })
                parsed_count += len(parsed)
                successful += 1
            except PigGoCoreError as error:
                status["error_code"] = error.__class__.__name__
            except Exception:
                status["error_code"] = "unexpected_error"
                logger.error("PigGoKidsMetadata V2 RSS 刷新失败：unexpected_error")
            statuses[feed_id] = status
        merged = upsert_candidates(self._load_candidates(), incoming)
        self._save_candidates(merged)
        self._save_feed_status(statuses)
        return self._response(
            successful > 0 or not self._rss_urls,
            {
                "feed_count": len(self._rss_urls),
                "successful_feeds": successful,
                "parsed_count": parsed_count,
                "candidate_count": len(merged),
            },
            "" if successful > 0 else ("尚未配置 RSS" if not self._rss_urls else "RSS 刷新失败"),
        )

    def _resolve_candidate_reference(
        self,
        candidate: FeedCandidate,
        provided_reference: Optional[str] = None,
    ) -> Optional[str]:
        if provided_reference:
            reference = validate_download_reference(provided_reference)
            if reference_fingerprint(reference) != candidate.reference_fingerprint:
                raise InvalidReferenceError("下载引用与候选指纹不匹配")
            return reference
        if reference := self._candidate_download_references.get(candidate.candidate_id):
            return reference
        return None

    @staticmethod
    def _host_download_label() -> str:
        """保留插件接管标签，同时加入 MP 当前下载列表要求的系统标签。"""

        labels = ["piggokids"]
        try:
            from app.core.config import settings

            labels.extend(
                item.strip()
                for item in str(getattr(settings, "TORRENT_TAG", "") or "").split(",")
                if item.strip()
            )
        except (ImportError, AttributeError):
            pass
        return ",".join(dict.fromkeys(labels))

    def _submit_download_to_host(self, candidate: FeedCandidate, reference: str) -> Any:
        if urlsplit(reference).scheme.casefold() in {"http", "https"}:
            validate_public_http_url(reference)
        from app.chain.download import DownloadChain
        from app.core.context import Context, MediaInfo, TorrentInfo
        from app.core.metainfo import MetaInfo

        mtype = {
            MediaKind.MOVIE: MediaType.MOVIE,
            MediaKind.TV: MediaType.TV,
        }.get(candidate.media_type, getattr(MediaType, "UNKNOWN", None))
        meta = MetaInfo(title=candidate.title, subtitle=candidate.summary)
        media = MediaInfo(
            type=mtype,
            title=candidate.title,
            poster_path=self._display_artwork_reference(candidate.candidate_id),
        )
        torrent = TorrentInfo(
            site_name="PigGo",
            title=candidate.title,
            description=candidate.summary,
            enclosure=reference,
            size=float(candidate.size_bytes or 0),
            pubdate=candidate.published_at,
            category=mtype.value if mtype else None,
        )
        context = Context(
            meta_info=meta,
            media_info=media,
            torrent_info=torrent,
            resource_source="rss" if candidate.source_feed_id != "manual" else "unknown",
            match_source="unknown",
            candidate_recognized=False,
        )
        return DownloadChain().download_single(
            context=context,
            username=DOWNLOAD_SOURCE,
            downloader=self._downloader or None,
            save_path=self._download_save_path or None,
            source=DOWNLOAD_SOURCE,
            label=self._host_download_label(),
        )

    @staticmethod
    def _download_result_id(result: Any) -> Optional[str]:
        if isinstance(result, dict):
            result = result.get("download_id") or result.get("hash") or result.get("id")
        if isinstance(result, tuple):
            result = result[0] if result else None
        text = str(result or "").strip()
        return text[:256] or None

    def _submit_candidate_download(
        self,
        candidate: FeedCandidate,
        reference: str,
    ) -> tuple[bool, ImportTask, str]:
        """串行化提交窗口，避免同步 DownloadAdded 事件跨请求串单。"""

        with self._submission_lock:
            return self._submit_candidate_download_locked(candidate, reference)

    def _submit_candidate_download_locked(
        self,
        candidate: FeedCandidate,
        reference: str,
    ) -> tuple[bool, ImportTask, str]:
        if candidate.task_id:
            existing = self._find_task(candidate.task_id)
            if existing and existing.state not in {TaskState.RETRYABLE_FAILED, TaskState.IGNORED}:
                return True, existing, "任务已经存在"
            if existing and existing.state == TaskState.RETRYABLE_FAILED and (
                existing.download_hash or existing.download_id or existing.relative_source_path
            ):
                return False, existing, "下载阶段已经完成，请从任务列表重试扫描或整理"
        task_id = hashlib.sha256(f"candidate:{candidate.candidate_id}".encode("utf-8")).hexdigest()[:24]
        task = self._find_task(task_id) or ImportTask(
            task_id=task_id,
            site_item_id=candidate.site_item_id,
            candidate_id=candidate.candidate_id,
            downloader=self._downloader or None,
        )
        if task.state == TaskState.RETRYABLE_FAILED:
            task.transition(TaskState.DOWNLOAD_SUBMITTED, "user_retry")
        elif task.state == TaskState.DISCOVERED:
            task.transition(TaskState.SELECTED, "user_selected")
        self._save_task(task)
        self._update_candidate(candidate.candidate_id, status=CandidateStatus.SELECTED, task_id=task.task_id)
        try:
            self._active_submission_task_id = task.task_id
            download_id = self._download_result_id(self._submit_download_to_host(candidate, reference))
            if not download_id:
                raise PigGoCoreError("MoviePilot 未返回下载任务标识")
            task = self._find_task(task.task_id) or task
            task.download_id = task.download_id or download_id
            task.download_hash = task.download_hash or normalize_download_hash(download_id)
            if task.state == TaskState.SELECTED:
                task.transition(TaskState.DOWNLOAD_SUBMITTED, "host_accepted")
            if task.state == TaskState.DOWNLOAD_SUBMITTED:
                task.transition(TaskState.DOWNLOADING, "host_tracking")
            task.last_error_code = None
            self._save_task(task)
            self._reserve_download_for_plugin(task)
            self._update_candidate(candidate.candidate_id, status=CandidateStatus.DOWNLOADING, task_id=task.task_id)
            return True, task, "下载任务已提交"
        except PigGoCoreError as error:
            if task.state != TaskState.RETRYABLE_FAILED:
                task.transition(TaskState.RETRYABLE_FAILED, error.__class__.__name__)
            task.last_error_code = error.__class__.__name__
            self._save_task(task)
            self._update_candidate(candidate.candidate_id, status=CandidateStatus.FAILED, task_id=task.task_id)
            return False, task, str(error)
        except Exception:
            if task.state != TaskState.RETRYABLE_FAILED:
                task.transition(TaskState.RETRYABLE_FAILED, "unexpected_error")
            task.last_error_code = "unexpected_error"
            self._save_task(task)
            self._update_candidate(candidate.candidate_id, status=CandidateStatus.FAILED, task_id=task.task_id)
            logger.error("PigGoKidsMetadata V2 下载提交失败：unexpected_error")
            return False, task, "MoviePilot 下载任务提交失败"
        finally:
            self._active_submission_task_id = None

    @staticmethod
    def _event_data(event: Any) -> dict[str, Any]:
        data = getattr(event, "event_data", None)
        return dict(data) if isinstance(data, dict) else {}

    @staticmethod
    def _safe_torrent_files(context: Any) -> list[str]:
        torrent = getattr(context, "torrent_info", None)
        raw = getattr(torrent, "file_list", None) or getattr(torrent, "files", None) or []
        files = []
        for item in raw if isinstance(raw, (list, tuple, set)) else []:
            value = getattr(item, "path", item)
            path = Path(str(value or ""))
            if not value or path.is_absolute() or ".." in path.parts:
                continue
            files.append(path.as_posix()[:1_000])
        return files[:10_000]

    def _relative_source_from_host_path(self, value: Any) -> Optional[str]:
        """只接受扫描根目录内真实存在的宿主路径。"""

        if not self._scan_root or not value:
            return None
        try:
            root = Path(self._scan_root).expanduser().resolve(strict=True)
            candidate = Path(str(value)).expanduser().resolve(strict=True)
            if candidate.is_file():
                candidate = candidate.parent
            relative = candidate.relative_to(root)
            if ".." in relative.parts:
                return None
            return relative.as_posix() or "."
        except (OSError, RuntimeError, ValueError):
            return None

    def _transfer_source_file_key(self, task: ImportTask, raw_path: Any) -> Optional[str]:
        """把一个宿主源文件路径映射为任务预期的安全相对文件键。"""

        relative: Optional[str] = None
        if self._scan_root and raw_path:
            try:
                root = Path(self._scan_root).expanduser().resolve(strict=True)
                candidate = Path(str(raw_path)).expanduser().resolve(strict=True)
                relative = candidate.relative_to(root).as_posix()
            except (OSError, RuntimeError, ValueError):
                relative = None
        expected = set(task.transfer_expected_files) or {
            Path(item).as_posix()
            for item in task.torrent_files
            if Path(item).suffix.casefold() in VIDEO_EXTENSIONS
        }
        if relative:
            if relative in expected:
                return relative
            suffix_matches = [item for item in expected if relative.endswith(f"/{item}")]
            if len(suffix_matches) == 1:
                return suffix_matches[0]
            basename_matches = [item for item in expected if Path(item).name == Path(relative).name]
            if len(basename_matches) == 1:
                return basename_matches[0]
        return None

    def _transfer_event_file_key(self, task: ImportTask, data: dict[str, Any]) -> Optional[str]:
        """把宿主绝对文件路径映射为任务预期的安全相对文件键。"""

        fileitem = data.get("fileitem")
        raw_path = getattr(fileitem, "path", None)
        if key := self._transfer_source_file_key(task, raw_path):
            return key
        history_id = data.get("transfer_history_id")
        identity = str(history_id or raw_path or "").strip()
        if not identity:
            return None
        return f"event:{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"

    def _record_transfer_result(
        self,
        task: ImportTask,
        data: dict[str, Any],
        *,
        success: bool,
    ) -> bool:
        """记录单文件整理结果；仅在全部预期媒体文件成功时返回完成。"""

        key = self._transfer_event_file_key(task, data)
        if key:
            completed = set(task.transfer_completed_files)
            failed = set(task.transfer_failed_files)
            if success:
                completed.add(key)
                failed.discard(key)
            else:
                failed.add(key)
                completed.discard(key)
            task.transfer_completed_files = sorted(completed)
            task.transfer_failed_files = sorted(failed)
        self._save_task(task)
        expected = set(task.transfer_expected_files) or {
            Path(item).as_posix()
            for item in task.torrent_files
            if Path(item).suffix.casefold() in VIDEO_EXTENSIONS
        }
        return bool(
            expected
            and expected.issubset(task.transfer_completed_files)
            and not task.transfer_failed_files
        )

    def _apply_public_match(self, decision: Any) -> None:
        """通过 MoviePilot 的 TMDb 读取链尝试精确匹配，失败时保留本地身份。"""

        if not self._public_match_enabled or not decision.item or not decision.auto_eligible:
            return
        try:
            from app.chain.media import MediaChain
            from app.core.metainfo import MetaInfo

            meta = MetaInfo(decision.item.title)
            meta.year = decision.item.year
            meta.type = (
                MediaType.TV
                if decision.item.media_type == MediaKind.TV
                else MediaType.MOVIE
            )
            if decision.item.season is not None:
                meta.begin_season = decision.item.season
            candidate = MediaChain().recognize_by_meta(
                meta,
                source="themoviedb",
                obtain_images=False,
            )
        except Exception:
            decision.public_match = {
                "exact": False,
                "confidence": 0.0,
                "reasons": ["public_lookup_unavailable"],
            }
            return
        if not candidate:
            decision.public_match = {
                "exact": False,
                "confidence": 0.0,
                "reasons": ["public_match_not_found"],
            }
            return
        evaluation = evaluate_public_media_match(decision.item, candidate)
        decision.public_match = evaluation
        if not evaluation["exact"]:
            return
        decision.item.media_source = str(evaluation["media_source"])
        decision.item.media_id = str(evaluation["media_id"])
        decision.item.source_fields["media_source"] = "moviepilot_public_exact"
        decision.item.source_fields["media_id"] = "moviepilot_public_exact"

    def _scan_task(
        self,
        task: ImportTask,
        *,
        reason: str,
        allow_auto_transfer: bool = True,
    ) -> tuple[bool, Optional[dict[str, Any]], str]:
        if not self._scan_root or not task.relative_source_path:
            return False, None, "任务尚未定位到扫描根目录内的内容包"
        try:
            if task.state in {
                TaskState.DOWNLOAD_SUBMITTED,
                TaskState.DOWNLOADING,
                TaskState.RETRYABLE_FAILED,
            }:
                task.transition(TaskState.DOWNLOADED, reason)
            if task.state == TaskState.DOWNLOADED:
                task.transition(TaskState.SCANNING, reason)
            decision = inspect_downloaded_payload(
                self._scan_root,
                task.relative_source_path,
                site_item_id=task.site_item_id,
                minimum_confidence=self._minimum_confidence,
                policy=ScanPolicy(max_files=self._max_files),
            )
            plugin_owned = bool(task.candidate_id)
            # 插件提交的下载完全采用本地 NFO/文件名身份；不再交给 MP/TMDb
            # 二次识别，也不因置信度阈值或冲突停在人工审核。
            if plugin_owned and (
                not decision.item
                or decision.item.media_type not in {MediaKind.MOVIE, MediaKind.TV}
            ):
                raise PigGoCoreError("插件无法从本地元数据确定电影或剧集类型")
            if plugin_owned:
                decision.auto_eligible = True
            else:
                self._apply_public_match(decision)
            if task.state == TaskState.SCANNING:
                task.transition(TaskState.MATCHING, "payload_scanned")
            if decision.item:
                task.media_id = decision.item.media_id
            source_prefix = Path(task.relative_source_path)
            task.transfer_expected_files = sorted({
                (source_prefix / relative).as_posix()
                for relative in decision.payload.media_files
            })
            if (
                decision.item
                and decision.auto_eligible
                and decision.item.media_source == "piggokids"
            ):
                self._save_registry_item(decision.item.to_dict())
            if task.state == TaskState.MATCHING:
                task.transition(
                    TaskState.READY_TO_TRANSFER if decision.auto_eligible else TaskState.NEEDS_REVIEW,
                    "high_confidence" if decision.auto_eligible else "review_required",
                )
            task.last_error_code = None
            self._save_decision(task.task_id, decision.to_dict())
            self._save_task(task)
            self._update_candidate(
                task.candidate_id,
                status=CandidateStatus.SELECTED,
                task_id=task.task_id,
            )
            message = "内容包识别完成"
            if (
                decision.auto_eligible
                and allow_auto_transfer
                and (plugin_owned or self._auto_transfer)
            ):
                transfer_success, transfer_message = self._start_host_transfer(
                    task,
                    decision.to_dict(),
                )
                if not transfer_success:
                    return False, decision.to_dict(), transfer_message
                message = "插件已自动识别并提交整理"
            return True, decision.to_dict(), message
        except PigGoCoreError as error:
            if task.state not in {TaskState.RETRYABLE_FAILED, TaskState.NEEDS_REVIEW}:
                try:
                    task.transition(TaskState.RETRYABLE_FAILED, error.__class__.__name__)
                except PigGoCoreError:
                    pass
            task.last_error_code = error.__class__.__name__
            self._save_task(task)
            return False, None, str(error)
        except Exception:
            if task.state not in {TaskState.RETRYABLE_FAILED, TaskState.NEEDS_REVIEW}:
                try:
                    task.transition(TaskState.RETRYABLE_FAILED, "unexpected_error")
                except PigGoCoreError:
                    pass
            task.last_error_code = "unexpected_error"
            self._save_task(task)
            logger.error("PigGoKidsMetadata V2 下载后扫描失败：unexpected_error")
            return False, None, "下载后扫描失败"

    def _submit_transfer_to_host(self, task: ImportTask, decision: dict[str, Any]) -> tuple[bool, Any]:
        """使用 V2 整理链和本地 MediaInfo；插件不自行移动文件。"""

        from app.chain.transfer import TransferChain
        from app.core.context import MediaInfo

        item = dict(decision.get("item") or {})
        kind = str(item.get("media_type") or "")
        mtype = MediaType.TV if kind == MediaKind.TV.value else MediaType.MOVIE
        media_source = str(item.get("media_source") or "piggokids")
        root = Path(self._scan_root).expanduser().resolve(strict=True)
        source = (root / str(task.relative_source_path or "")).resolve(strict=True)
        source.relative_to(root)
        fileitem = schemas.FileItem(
            storage="local",
            path=source.as_posix(),
            type="dir",
            name=source.name,
            basename=source.name,
        )
        media = MediaInfo(
            source=media_source,
            media_id=str(item.get("media_id") or task.media_id or ""),
            type=mtype,
            title=str(item.get("title") or ""),
            original_title=item.get("original_title"),
            year=item.get("year"),
            season=item.get("season"),
            overview=item.get("overview"),
            names=list(item.get("aliases") or []),
            genres=[{"name": value} for value in item.get("genres") or []],
            number_of_episodes=item.get("episode_count") or 0,
            poster_path=self._display_artwork_reference(getattr(task, "candidate_id", None)),
            # V2 在目标目录启用“按媒体类别建目录”时强制要求
            # MediaInfo.category。PigGo 本地身份没有 TMDB 辅助分类，
            # 因此使用与本插件整理预览一致的稳定类别。
            category="儿童动画" if mtype == MediaType.TV else "儿童动画电影",
        )
        return TransferChain().do_transfer(
            fileitem=fileitem,
            mediainfo=media,
            media_source=media_source,
            season=item.get("season"),
            scrape=False,
            background=True,
            # 用户确认后的提交属于手工整理。MoviePilot 会据此清理同一
            # 源文件之前失败的整理历史，避免旧的“未识别到媒体信息”
            # 记录让整批文件被误判为“已整理过”。
            manual=True,
            downloader=task.downloader,
            download_hash=task.download_hash,
            sync_extra_files=True,
        )

    def _start_host_transfer(self, task: ImportTask, decision: dict[str, Any]) -> tuple[bool, str]:
        if task.state == TaskState.TRANSFERRING:
            return True, "整理任务已经提交"
        if task.state != TaskState.READY_TO_TRANSFER:
            return False, "任务尚未达到可整理状态"
        try:
            task.transfer_completed_files = []
            task.transfer_failed_files = []
            task.transition(TaskState.TRANSFERRING, "transfer_requested")
            self._save_task(task)
            self._update_candidate(
                task.candidate_id,
                status=CandidateStatus.DOWNLOADING,
                task_id=task.task_id,
            )
            success, _ = self._submit_transfer_to_host(task, decision)
            if not success:
                task.transition(TaskState.RETRYABLE_FAILED, "host_transfer_rejected")
                task.last_error_code = "host_transfer_rejected"
                self._save_task(task)
                return False, "MoviePilot 未接受整理任务"
            return True, "MoviePilot 已接受整理任务"
        except Exception:
            if task.state == TaskState.TRANSFERRING:
                task.transition(TaskState.RETRYABLE_FAILED, "unexpected_error")
            task.last_error_code = "unexpected_error"
            self._save_task(task)
            logger.error("PigGoKidsMetadata V2 整理提交失败：unexpected_error")
            return False, "MoviePilot 整理任务提交失败"

    def _advance_host_completed(self, task: ImportTask) -> None:
        if task.state == TaskState.DISCOVERED:
            task.transition(TaskState.SELECTED, "host_transfer_complete")
        if task.state == TaskState.SELECTED:
            task.transition(TaskState.DOWNLOAD_SUBMITTED, "host_transfer_complete")
        if task.state == TaskState.DOWNLOAD_SUBMITTED:
            task.transition(TaskState.DOWNLOADING, "host_transfer_complete")
        if task.state == TaskState.DOWNLOADING:
            task.transition(TaskState.DOWNLOADED, "host_transfer_complete")
        if task.state == TaskState.DOWNLOADED:
            task.transition(TaskState.SCANNING, "host_pipeline_complete")
        if task.state == TaskState.SCANNING:
            task.transition(TaskState.MATCHING, "host_pipeline_complete")
        if task.state == TaskState.NEEDS_REVIEW:
            self._save_task(task)
            return
        if task.state == TaskState.MATCHING:
            task.transition(TaskState.READY_TO_TRANSFER, "host_pipeline_complete")
        if task.state in {TaskState.RETRYABLE_FAILED, TaskState.READY_TO_TRANSFER}:
            task.transition(TaskState.TRANSFERRING, "host_transfer_complete")
        if task.state == TaskState.TRANSFERRING:
            task.transition(TaskState.LIBRARY_REFRESHING, "host_transfer_complete")
        if task.state == TaskState.LIBRARY_REFRESHING:
            task.transition(TaskState.COMPLETED, "host_transfer_complete")
        task.last_error_code = None
        self._save_task(task)
        self._update_candidate(task.candidate_id, status=CandidateStatus.COMPLETED, task_id=task.task_id)

    @eventmanager.register(EventType.DownloadAdded)
    def on_download_added(self, event: Event) -> None:
        if not self._enabled:
            return
        data = self._event_data(event)
        download_hash = normalize_download_hash(data.get("hash"))
        task = self._find_task_by_hash(download_hash)
        if not task and data.get("source") == DOWNLOAD_SOURCE and self._active_submission_task_id:
            task = self._find_task(self._active_submission_task_id)
        if not task:
            return
        task.download_hash = download_hash or task.download_hash
        task.download_id = task.download_id or download_hash
        task.downloader = str(data.get("downloader") or task.downloader or "") or None
        task.torrent_files = self._safe_torrent_files(data.get("context"))
        if task.state == TaskState.SELECTED:
            task.transition(TaskState.DOWNLOAD_SUBMITTED, "download_added_event")
        if task.state == TaskState.DOWNLOAD_SUBMITTED:
            task.transition(TaskState.DOWNLOADING, "download_added_event")
        self._save_task(task)
        self._reserve_download_for_plugin(task)
        self._recover_download_name_from_host(task)

    @eventmanager.register(EventType.TransferComplete)
    def on_transfer_complete(self, event: Event) -> None:
        with self._state_lock:
            if not self._enabled:
                return
            data = self._event_data(event)
            task = self._find_task_by_hash(data.get("download_hash"))
            if task and task.state not in {TaskState.COMPLETED, TaskState.IGNORED}:
                if self._record_transfer_result(task, data, success=True):
                    artwork_url = self._display_artwork_reference(task.candidate_id)
                    self._backfill_host_history_artwork(task, artwork_url)
                    if target_dir := self._event_target_dir(data):
                        self._install_task_artwork(task, target_dir)
                    self._advance_host_completed(task)

    @eventmanager.register(EventType.TransferFailed)
    def on_transfer_failed(self, event: Event) -> None:
        with self._state_lock:
            self._on_transfer_failed_locked(event)

    def _on_transfer_failed_locked(self, event: Event) -> None:
        if not self._enabled:
            return
        data = self._event_data(event)
        task = self._find_task_by_hash(data.get("download_hash"))
        if not task or task.state in {TaskState.COMPLETED, TaskState.IGNORED}:
            return
        self._record_transfer_result(task, data, success=False)
        fileitem = data.get("fileitem")
        relative = self._relative_source_from_host_path(getattr(fileitem, "path", None))
        if relative:
            task.relative_source_path = relative
        if task.state != TaskState.RETRYABLE_FAILED:
            task.transition(TaskState.RETRYABLE_FAILED, "host_transfer_failed")
        task.last_error_code = "host_transfer_failed"
        self._save_task(task)
        self._update_candidate(task.candidate_id, status=CandidateStatus.FAILED, task_id=task.task_id)
        if task.relative_source_path:
            self._scan_task(task, reason="transfer_failed_payload", allow_auto_transfer=False)

    @staticmethod
    def _record_values(record: Any) -> dict[str, Any]:
        """把宿主模型或字典转换为只读字段映射。"""

        if isinstance(record, dict):
            return dict(record)
        if callable(getattr(record, "model_dump", None)):
            return dict(record.model_dump())
        try:
            return dict(vars(record))
        except TypeError:
            return {}

    @staticmethod
    def _torrent_completed(torrent: dict[str, Any]) -> bool:
        """兼容宿主返回的完成状态以及 0–1/0–100 两种进度尺度。"""

        state = str(torrent.get("state") or "").strip().casefold()
        if state in {
            "complete",
            "completed",
            "seeding",
            "uploading",
            "stalledup",
            "pausedup",
            "queuedup",
            "checkingup",
            "完成",
            "已完成",
        }:
            return True
        try:
            progress = float(torrent.get("progress") or 0)
        except (TypeError, ValueError):
            return False
        if progress >= 100:
            return True
        return not state and 0 <= progress <= 1 and progress >= 1

    def _download_histories_by_hash(
        self,
        hashes: list[str],
        *,
        active_hashes: set[str],
    ) -> dict[str, dict[str, Any]]:
        """为已从下载器消失的任务读取 MoviePilot 下载历史。"""

        missing_hashes = [value for value in hashes if value not in active_hashes]
        if not missing_hashes:
            return {}
        try:
            from app.db.downloadhistory_oper import DownloadHistoryOper

            records = DownloadHistoryOper().get_by_hashes(missing_hashes) or {}
        except (ImportError, AttributeError):
            return {}
        except Exception:
            logger.error("PigGoKidsMetadata V2 下载历史恢复失败：unexpected_error")
            return {}
        if isinstance(records, dict):
            entries = records.items()
        else:
            entries = ((None, record) for record in records)
        histories: dict[str, dict[str, Any]] = {}
        for key, record in entries:
            values = self._record_values(record)
            download_hash = normalize_download_hash(values.get("download_hash") or key)
            if download_hash:
                histories[download_hash] = values
        return histories

    def _reconcile_transfer_history(self, task: ImportTask) -> str:
        """按 MP 整理历史恢复重载期间漏收的逐文件成功/失败事件。"""

        if not task.download_hash:
            return "pending"
        try:
            from app.db.transferhistory_oper import TransferHistoryOper

            histories = TransferHistoryOper().list_by_hash(task.download_hash) or []
        except (ImportError, AttributeError):
            return "pending"
        except Exception:
            logger.error("PigGoKidsMetadata V2 整理历史恢复失败：unexpected_error")
            return "pending"
        expected = set(task.transfer_expected_files) or {
            Path(item).as_posix()
            for item in task.torrent_files
            if Path(item).suffix.casefold() in VIDEO_EXTENSIONS
        }
        if not expected:
            return "pending"
        latest_by_key: dict[str, tuple[int, bool]] = {}
        for history in histories:
            values = self._record_values(history)
            key = self._transfer_source_file_key(task, values.get("src"))
            if not key or key not in expected:
                continue
            try:
                history_id = int(values.get("id") or 0)
            except (TypeError, ValueError):
                history_id = 0
            previous = latest_by_key.get(key)
            if previous is None or history_id >= previous[0]:
                latest_by_key[key] = (history_id, bool(values.get("status")))
        succeeded = {key for key, (_, status) in latest_by_key.items() if status}
        failed = {key for key, (_, status) in latest_by_key.items() if not status}
        task.transfer_completed_files = sorted(succeeded)
        task.transfer_failed_files = sorted(failed)
        self._save_task(task)
        if expected.issubset(succeeded):
            artwork_url = self._display_artwork_reference(task.candidate_id)
            self._backfill_host_history_artwork(task, artwork_url)
            if target_dir := self._history_target_dir(task):
                self._install_task_artwork(task, target_dir)
            self._advance_host_completed(task)
            return "completed"
        if expected.issubset(succeeded | failed) and failed:
            if task.state != TaskState.RETRYABLE_FAILED:
                task.transition(TaskState.RETRYABLE_FAILED, "transfer_history_failed")
            task.last_error_code = "host_transfer_failed"
            self._save_task(task)
            self._update_candidate(
                task.candidate_id,
                status=CandidateStatus.FAILED,
                task_id=task.task_id,
            )
            return "failed"
        return "pending"

    def reconcile_downloads(self) -> dict[str, Any]:
        if not self._enabled:
            return self._response(False, message="插件尚未启用")
        tasks = [
            ImportTask.from_dict(raw)
            for raw in self._load_tasks()
            if raw.get("state") not in {TaskState.COMPLETED.value, TaskState.IGNORED.value}
        ]
        for task in tasks:
            if task.candidate_id and task.download_hash:
                self._reserve_download_for_plugin(task)
        hashes = sorted({
            value
            for value in (normalize_download_hash(task.download_hash) for task in tasks)
            if value
        })
        try:
            from app.chain.download import DownloadChain

            chain = DownloadChain()
            try:
                if hashes:
                    try:
                        torrents = chain.list_torrents(
                            downloader=self._downloader or None,
                            hashs=hashes,
                            include_all_tags=True,
                        ) or []
                    except TypeError:
                        torrents = chain.list_torrents(
                            downloader=self._downloader or None,
                            hashs=hashes,
                        ) or []
                else:
                    torrents = []
            except (AttributeError, TypeError):
                torrents = chain.downloading(self._downloader or None) or []
        except Exception:
            logger.error("PigGoKidsMetadata V2 下载状态恢复失败：unexpected_error")
            return self._response(False, message="下载状态查询失败")
        by_hash = {}
        for torrent in torrents:
            value = self._record_values(torrent)
            download_hash = normalize_download_hash(value.get("hash"))
            if download_hash:
                by_hash[download_hash] = value
        history_by_hash = self._download_histories_by_hash(
            hashes,
            active_hashes=set(by_hash),
        )
        tracked = 0
        scanned = 0
        history_recovered = 0
        transfer_completed = 0
        transfer_failed = 0
        transfer_pending = 0
        for task in tasks:
            if task.relative_source_path:
                self._adopt_download_name(task, Path(task.relative_source_path).name)
            if task.state in {TaskState.TRANSFERRING, TaskState.LIBRARY_REFRESHING}:
                outcome = self._reconcile_transfer_history(task)
                transfer_completed += int(outcome == "completed")
                transfer_failed += int(outcome == "failed")
                transfer_pending += int(outcome == "pending")
                tracked += 1
                continue
            if task.state == TaskState.READY_TO_TRANSFER and self._auto_transfer:
                decision = self._load_decisions().get(task.task_id)
                if decision and self._start_host_transfer(task, decision)[0]:
                    tracked += 1
                continue
            download_hash = normalize_download_hash(task.download_hash) or ""
            torrent = by_hash.get(download_hash)
            if not torrent:
                history = history_by_hash.get(download_hash)
                if not history:
                    continue
                relative = self._relative_source_from_host_path(history.get("path"))
                if not relative:
                    continue
                tracked += 1
                history_recovered += 1
                task.downloader = str(
                    history.get("downloader") or task.downloader or ""
                ) or None
                task.relative_source_path = relative
                self._adopt_download_name(task, Path(relative).name)
                self._save_task(task)
                success, _, _ = self._scan_task(
                    task,
                    reason="download_history_payload",
                )
                scanned += int(success)
                continue
            tracked += 1
            task.downloader = str(torrent.get("downloader") or task.downloader or "") or None
            self._adopt_download_name(task, torrent.get("name"))
            if not self._torrent_completed(torrent):
                self._save_task(task)
                continue
            relative = self._relative_source_from_host_path(torrent.get("path"))
            if relative:
                task.relative_source_path = relative
                self._adopt_download_name(task, Path(relative).name)
            self._save_task(task)
            if task.relative_source_path:
                success, _, _ = self._scan_task(task, reason="download_poll_completed")
                scanned += int(success)
        return self._response(True, {
            "tracked": tracked,
            "scanned": scanned,
            "history_recovered": history_recovered,
            "transfer_completed": transfer_completed,
            "transfer_failed": transfer_failed,
            "transfer_pending": transfer_pending,
        })

    @staticmethod
    def _response(success: bool, data: Optional[dict[str, Any]] = None, message: str = "") -> dict[str, Any]:
        return {"success": success, "message": message, "data": data or {}}

    def api_status(self) -> dict[str, Any]:
        drafts = self._contribution_drafts()
        return self._response(True, {
            "enabled": self._enabled,
            "scan_root_configured": bool(self._scan_root),
            "minimum_confidence": self._minimum_confidence,
            "registry_count": len(self._load_registry()),
            "task_count": len(self._load_tasks()),
            "decision_count": len(self._load_decisions()),
            "contribution_draft_count": len(drafts),
            "candidate_count": len(self._load_candidates()),
            "rss_feed_count": len(self._rss_urls),
            "rss_configured": bool(self._rss_urls),
            "rss_refresh_mode": "manual_only",
            "downloader_configured": bool(self._downloader),
            "download_save_path_configured": bool(self._download_save_path),
            "auto_transfer": self._auto_transfer,
            "public_match_enabled": self._public_match_enabled,
            "config_error": self._config_error,
            "phase": 4,
            "v2_media_source_adapter": False,
        })

    def api_registry(self) -> dict[str, Any]:
        return self._response(True, {
            "items": list(self._load_registry().values()),
            "decisions": list(self._load_decisions().values()),
        })

    def api_feeds(self) -> dict[str, Any]:
        """返回不含查询参数和私密 RSS 引用的抓取状态。"""

        from .core import redact_url

        items = []
        for status in self._load_feed_status().values():
            if not isinstance(status, dict):
                continue
            parts = urlsplit(redact_url(str(status.get("url") or "")))
            label = f"{parts.scheme}://{parts.netloc}{parts.path}" if parts.hostname else ""
            items.append({
                "feed_id": str(status.get("feed_id") or ""),
                "source": label[:1_000],
                "last_attempt_at": status.get("last_attempt_at"),
                "last_success_at": status.get("last_success_at"),
                "http_status": status.get("http_status"),
                "parsed_count": status.get("parsed_count") or 0,
                "error_code": status.get("error_code"),
            })
        items.sort(key=lambda item: str(item.get("last_attempt_at") or ""), reverse=True)
        return self._response(True, {"items": items, "total": len(items)})

    def _contribution_drafts(self) -> list[dict[str, Any]]:
        tasks = {str(item.get("task_id") or ""): item for item in self._load_tasks()}
        drafts = []
        for task_id, decision in self._load_decisions().items():
            draft = build_contribution_draft(tasks.get(task_id, {"task_id": task_id}), decision)
            if draft:
                drafts.append(draft)
        return drafts

    def api_contribution_drafts(self) -> dict[str, Any]:
        drafts = self._contribution_drafts()
        return self._response(True, {"items": drafts, "total": len(drafts)})

    def api_candidates(
        self,
        query: str = "",
        status: str = "",
        media_type: str = "",
        limit: int = 100,
    ) -> dict[str, Any]:
        normalized_query = normalize_title(query)
        status_value = str(status or "").casefold()
        type_value = str(media_type or "").casefold()
        try:
            safe_limit = max(1, min(MAX_CANDIDATE_ITEMS, int(limit or 100)))
        except (TypeError, ValueError):
            safe_limit = 100
        candidates = self._load_candidates()
        items = []
        total = 0
        for item in reversed(candidates):
            if normalized_query and normalized_query not in normalize_title(item.title):
                continue
            if status_value and item.status.value != status_value:
                continue
            if type_value and item.media_type.value != type_value:
                continue
            total += 1
            if len(items) < safe_limit:
                values = item.to_dict()
                values["poster_url"] = self._display_artwork_reference(item.candidate_id)
                items.append(values)
        return self._response(True, {"items": items, "total": total})

    def api_refresh_candidates(self, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        del payload
        return self.refresh_candidates()

    def api_task_artwork(self, payload: dict[str, Any]) -> dict[str, Any]:
        """根据整理历史定位媒体根目录，并为一个任务补写 RSS 封面。"""

        if not self._enabled:
            return self._response(False, message="插件尚未启用")
        task_id = str(dict(payload or {}).get("task_id") or "").strip()
        task = self._find_task(task_id)
        if not task:
            return self._response(False, message="任务不存在")
        if not task.candidate_id:
            return self._response(False, message="任务没有关联 RSS 候选")
        target_dir = self._history_target_dir(task)
        if not target_dir:
            return self._response(False, message="未从 MoviePilot 整理历史定位到本地媒体目录")
        history_updated = self._backfill_host_history_artwork(
            task,
            self._display_artwork_reference(task.candidate_id),
        )
        success, path, message = self._install_task_artwork(task, target_dir)
        return self._response(
            success,
            {
                "task_id": task.task_id,
                "poster_path": str(path) if path else None,
                "history_images_updated": history_updated,
            },
            message,
        )

    def api_upload_task_artwork(self, payload: dict[str, Any]) -> dict[str, Any]:
        """接收用户图片，写入媒体目录并回填 MP 历史展示地址。"""

        if not self._enabled:
            return self._response(False, message="插件尚未启用")
        values = dict(payload or {})
        task = self._find_task(str(values.get("task_id") or "").strip())
        if not task:
            return self._response(False, message="任务不存在")
        target_dir = self._history_target_dir(task)
        if not target_dir:
            return self._response(False, message="未从 MoviePilot 整理历史定位到本地媒体目录")
        try:
            content, extension = self._decode_uploaded_artwork(values.get("image_base64"))
            poster = self._write_artwork(target_dir, content, extension)
            cached_content = poster.read_bytes()
            cache = self._cache_task_artwork(task, cached_content, poster.suffix.casefold())
            artwork_url = self._task_artwork_public_url(
                task,
                cache.suffix,
                str(values.get("public_base_url") or ""),
            )
            self._set_candidate_uploaded_artwork(task, artwork_url)
            history_updated = self._backfill_host_history_artwork(
                task,
                artwork_url,
                replace_plugin_artwork=True,
            )
            return self._response(True, {
                "task_id": task.task_id,
                "poster_path": str(poster),
                "artwork_url": artwork_url,
                "history_images_updated": history_updated,
            }, "封面已写入媒体目录并回填 MoviePilot 历史")
        except (PigGoCoreError, OSError) as error:
            return self._response(False, message=str(error))

    def api_reconcile_tasks(self, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        del payload
        return self.reconcile_downloads()

    def api_import_candidate(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._enabled:
            return self._response(False, message="插件尚未启用")
        values = dict(payload or {})
        try:
            kind = MediaKind(str(values.get("media_type") or MediaKind.UNKNOWN.value).casefold())
            supplied_title = str(values.get("title") or "").strip()
            parsed = candidate_from_reference(
                str(values.get("download_reference") or ""),
                title=supplied_title or None,
                media_type=kind,
            )
            candidates = self._load_candidates()
            cached = next(
                (
                    item for item in candidates
                    if parsed.candidate.site_item_id
                    and item.site_item_id == parsed.candidate.site_item_id
                    and item.source_feed_id != "manual"
                ),
                None,
            )
            if cached:
                candidate = FeedCandidate.from_dict(cached.to_dict())
                candidate.reference_fingerprint = parsed.candidate.reference_fingerprint
                candidate.download_url = parsed.candidate.download_url
                if supplied_title:
                    candidate.title = supplied_title
                    candidate.title_overridden = True
                if kind != MediaKind.UNKNOWN:
                    candidate.media_type = kind
                    candidate.media_type_overridden = True
                parsed.candidate = candidate
            poster_url = str(values.get("poster_url") or "").strip()
            if poster_url:
                safe_poster = safe_persisted_artwork_reference((poster_url,))
                if not safe_poster:
                    raise InvalidReferenceError("封面地址必须是无账号、无查询参数的公网 HTTPS URL")
                parsed.candidate.poster_url = safe_poster
                self._candidate_artwork_references[parsed.candidate.candidate_id] = (safe_poster,)
            self._candidate_download_references[parsed.candidate.candidate_id] = parsed.download_reference
            merged = upsert_candidates(candidates, [parsed.candidate])
            self._save_candidates(merged)
            candidate = next(item for item in merged if item.candidate_id == parsed.candidate.candidate_id)
            return self._response(True, {"candidate": candidate.to_dict()})
        except (InvalidReferenceError, ValueError) as error:
            return self._response(False, message=str(error))

    def api_ignore_candidate(self, payload: dict[str, Any]) -> dict[str, Any]:
        values = dict(payload or {})
        candidate_id = str(values.get("candidate_id") or "").strip()
        raw_ignored = values.get("ignored", True)
        ignored = (
            raw_ignored
            if isinstance(raw_ignored, bool)
            else str(raw_ignored).strip().casefold() not in {"false", "0", "no", "off"}
        )
        with self._state_lock:
            candidates = self._load_candidates()
            candidate = next(
                (item for item in candidates if item.candidate_id == candidate_id),
                None,
            )
            if not candidate:
                return self._response(False, message="候选资源不存在")
            if candidate.task_id:
                return self._response(False, message="候选已关联任务，请在任务页处理")
            expected = CandidateStatus.DISCOVERED if ignored else CandidateStatus.IGNORED
            if candidate.status != expected:
                return self._response(False, {"candidate": candidate.to_dict()}, "当前候选状态不能执行此动作")
            candidate.status = CandidateStatus.IGNORED if ignored else CandidateStatus.DISCOVERED
            candidate.updated_at = utc_now()
            self._save_candidates(candidates)
            return self._response(True, {"candidate": candidate.to_dict()})

    def api_update_candidate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """修正未进入任务流的候选显示标题和媒体类型，稳定 ID 保持不变。"""

        values = dict(payload or {})
        candidate_id = str(values.get("candidate_id") or "").strip()
        if "title" not in values and "media_type" not in values:
            return self._response(False, message="至少提供标题或媒体类型")
        title = " ".join(str(values.get("title") or "").replace("\x00", " ").split())
        try:
            media_type = (
                MediaKind(str(values.get("media_type") or "").casefold())
                if "media_type" in values
                else None
            )
        except ValueError:
            return self._response(False, message="媒体类型无效")
        if "title" in values and not title:
            return self._response(False, message="候选标题不能为空")
        with self._state_lock:
            candidates = self._load_candidates()
            candidate = next(
                (item for item in candidates if item.candidate_id == candidate_id),
                None,
            )
            if not candidate:
                return self._response(False, message="候选资源不存在")
            if candidate.task_id:
                return self._response(False, message="候选已关联任务，请在任务页处理")
            if candidate.status not in {CandidateStatus.DISCOVERED, CandidateStatus.IGNORED}:
                return self._response(False, message="当前候选状态不能修改")
            if "title" in values:
                candidate.title = title[:500]
                candidate.title_overridden = True
            if media_type is not None:
                candidate.media_type = media_type
                candidate.media_type_overridden = True
            candidate.updated_at = utc_now()
            self._save_candidates(candidates)
            return self._response(True, {"candidate": candidate.to_dict()})

    def api_download_candidate(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._enabled:
            return self._response(False, message="插件尚未启用")
        values = dict(payload or {})
        candidate_id = str(values.get("candidate_id") or "").strip()
        candidates = self._load_candidates()
        candidate = next(
            (item for item in candidates if item.candidate_id == candidate_id),
            None,
        )
        if not candidate:
            return self._response(False, message="候选资源不存在")
        media_type = values.get("media_type")
        if media_type:
            try:
                candidate.media_type = MediaKind(str(media_type).casefold())
            except ValueError:
                return self._response(False, message="媒体类型无效")
            self._save_candidates(candidates)
        try:
            reference = self._resolve_candidate_reference(
                candidate,
                str(values.get("download_reference") or "").strip() or None,
            )
        except InvalidReferenceError as error:
            return self._response(False, message=str(error))
        if not reference:
            return self._response(
                False,
                {"candidate": candidate.to_dict()},
                "私密下载引用未保留在内存中，请刷新 RSS 或重新粘贴引用",
            )
        success, task, message = self._submit_candidate_download(candidate, reference)
        return self._response(success, {"task": task.to_dict()}, message)

    def api_manual_download(self, payload: dict[str, Any]) -> dict[str, Any]:
        """一步提交手工链接；候选记录仅作为插件内部任务跟踪数据。"""

        imported = self.api_import_candidate(payload)
        if not imported.get("success"):
            return imported
        candidate = dict(imported.get("data") or {}).get("candidate") or {}
        candidate_id = str(candidate.get("candidate_id") or "")
        if not candidate_id:
            return self._response(False, message="手工下载任务初始化失败")
        return self.api_download_candidate({
            "candidate_id": candidate_id,
            "media_type": dict(payload or {}).get("media_type"),
        })

    def api_download_candidate_action(self, candidate_id: str = "") -> dict[str, Any]:
        return self.api_download_candidate({"candidate_id": candidate_id})

    def api_tasks(self) -> dict[str, Any]:
        return self._response(True, {
            "items": list(reversed(self._load_tasks())),
            "decisions": self._load_decisions(),
        })

    def api_retry_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        task = self._find_task(str((payload or {}).get("task_id") or ""))
        if not task:
            return self._response(False, message="任务不存在")
        if task.state == TaskState.READY_TO_TRANSFER:
            decision = self._load_decisions().get(task.task_id)
            if not decision:
                return self._response(False, {"task": task.to_dict()}, "任务缺少识别决策")
            success, message = self._start_host_transfer(task, decision)
            return self._response(success, {"task": task.to_dict()}, message)
        if task.state == TaskState.RETRYABLE_FAILED and task.relative_source_path:
            success, decision, message = self._scan_task(
                task,
                reason="user_retry",
                allow_auto_transfer=False,
            )
            if success and task.state == TaskState.READY_TO_TRANSFER and decision:
                success, message = self._start_host_transfer(task, decision)
            return self._response(success, {"task": task.to_dict()}, message)
        if task.state == TaskState.RETRYABLE_FAILED and task.candidate_id:
            return self.api_download_candidate({"candidate_id": task.candidate_id})
        return self._response(
            False,
            {"task": task.to_dict()},
            "当前任务状态没有可执行的重试动作",
        )

    def api_review_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        """人工处理冲突任务；批准后仍需显式执行整理。"""

        values = dict(payload or {})
        task_id = str(values.get("task_id") or "").strip()
        action = str(values.get("action") or "").strip().casefold()
        if action not in {"approve", "ignore"}:
            return self._response(False, message="审核动作必须是 approve 或 ignore")
        with self._state_lock:
            task = self._find_task(task_id)
            if not task:
                return self._response(False, message="任务不存在")
            if task.state != TaskState.NEEDS_REVIEW:
                return self._response(
                    False,
                    {"task": task.to_dict()},
                    "只有待人工审核任务可以执行此动作",
                )
            decision = self._load_decisions().get(task.task_id)
            if not decision or not isinstance(decision.get("item"), dict):
                return self._response(
                    False,
                    {"task": task.to_dict()},
                    "任务缺少可审核的识别决策",
                )
            decision["review"] = {"action": action, "reviewed_at": utc_now()}
            if action == "approve":
                decision["auto_eligible"] = True
                task.transition(TaskState.READY_TO_TRANSFER, "user_review_approved")
                item = decision["item"]
                if item.get("media_source") == "piggokids":
                    self._save_registry_item(item)
                self._update_candidate(
                    task.candidate_id,
                    status=CandidateStatus.SELECTED,
                    task_id=task.task_id,
                )
                message = "审核已批准，请确认后执行整理"
            else:
                decision["auto_eligible"] = False
                task.transition(TaskState.IGNORED, "user_review_ignored")
                self._update_candidate(
                    task.candidate_id,
                    status=CandidateStatus.IGNORED,
                    task_id=task.task_id,
                )
                message = "任务已忽略"
            self._save_decision(task.task_id, decision)
            self._save_task(task)
            return self._response(
                True,
                {"task": task.to_dict(), "decision": decision},
                message,
            )

    def api_retry_task_action(self, task_id: str = "") -> dict[str, Any]:
        return self.api_retry_task({"task_id": task_id})

    def api_scan(self, payload: dict[str, Any]) -> dict[str, Any]:
        """执行与 V3 相同的纯核心扫描；V2 暂不注册媒体来源合同。"""

        if not self._enabled:
            return self._response(False, message="插件尚未启用")
        if not self._scan_root:
            return self._response(False, message="尚未配置下载根目录")
        relative_path = str((payload or {}).get("relative_path") or "").strip()
        if not relative_path:
            return self._response(False, message="必须提供下载根目录内的相对路径")
        site_item_id = normalize_site_item_id((payload or {}).get("site_item_id"))
        download_hash = normalize_download_hash((payload or {}).get("download_hash"))
        task_key = download_hash or f"manual:{relative_path}"
        task_id = hashlib.sha256(task_key.encode("utf-8")).hexdigest()[:24]
        task = ImportTask(
            task_id=task_id,
            site_item_id=site_item_id,
            download_hash=download_hash,
            relative_source_path=relative_path,
        )
        try:
            task.transition(TaskState.SELECTED, "manual_scan")
            task.transition(TaskState.DOWNLOAD_SUBMITTED, "existing_payload")
            task.transition(TaskState.DOWNLOADED, "existing_payload")
            task.transition(TaskState.SCANNING, "manual_scan")
            decision = inspect_downloaded_payload(
                self._scan_root,
                relative_path,
                site_item_id=site_item_id,
                minimum_confidence=self._minimum_confidence,
                policy=ScanPolicy(max_files=self._max_files),
            )
            self._apply_public_match(decision)
            task.transition(TaskState.MATCHING, "payload_scanned")
            if decision.item:
                task.media_id = decision.item.media_id
            source_prefix = Path(relative_path)
            task.transfer_expected_files = sorted({
                (source_prefix / relative).as_posix()
                for relative in decision.payload.media_files
            })
            if (
                decision.item
                and decision.auto_eligible
                and decision.item.media_source == "piggokids"
            ):
                self._save_registry_item(decision.item.to_dict())
            task.transition(
                TaskState.READY_TO_TRANSFER if decision.auto_eligible else TaskState.NEEDS_REVIEW,
                "high_confidence" if decision.auto_eligible else "review_required",
            )
            self._save_decision(task.task_id, decision.to_dict())
            self._save_task(task)
            return self._response(True, {"task": task.to_dict(), "decision": decision.to_dict()})
        except PigGoCoreError as error:
            try:
                task.transition(TaskState.RETRYABLE_FAILED, error.__class__.__name__)
            except PigGoCoreError:
                pass
            task.last_error_code = error.__class__.__name__
            self._save_task(task)
            return self._response(False, {"task": task.to_dict()}, str(error))
        except Exception:
            logger.error("PigGoKidsMetadata V2 手工扫描失败：unexpected_error")
            try:
                task.transition(TaskState.RETRYABLE_FAILED, "unexpected_error")
            except PigGoCoreError:
                pass
            task.last_error_code = "unexpected_error"
            self._save_task(task)
            return self._response(
                False,
                {"task": task.to_dict()},
                "扫描失败，请检查 MoviePilot 日志中的脱敏错误摘要",
            )
