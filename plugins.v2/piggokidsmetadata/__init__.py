"""PigGo 儿童内容下载后元数据识别插件（MoviePilot V2 兼容层）。"""

from __future__ import annotations

import hashlib
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
    feed_id_for_url,
    parse_feed_document,
    parse_feed_urls_config,
    reference_fingerprint,
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
DOWNLOAD_SOURCE = "PigGoKidsMetadata"


class PigGoKidsMetadata(_PluginBase):
    """为 V2 提供 RSS 下载、任务恢复和本地元数据识别兼容能力。"""

    plugin_name = "PigGo 儿童动画增强识别"
    plugin_desc = "从 PigGo RSS 或粘贴链接发起下载，并用本地 NFO、图片和文件名增强识别"
    plugin_icon = "https://raw.githubusercontent.com/xianglongwei/MoviePilot-Plugins/main/icons/emby.png"
    plugin_version = "0.3.0"
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
    _rss_interval_minutes = 30
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
        self._active_submission_task_id: Optional[str] = None
        try:
            self._rss_urls = parse_feed_urls_config(values.get("rss_urls"))
        except InvalidReferenceError:
            self._rss_urls = []
            self._config_error = "invalid_rss_url"
            logger.error("PigGoKidsMetadata V2 RSS 配置无效：invalid_rss_url")
        try:
            self._rss_interval_minutes = max(
                10, min(1_440, int(values.get("rss_interval_minutes", 30)))
            )
        except (TypeError, ValueError):
            self._rss_interval_minutes = 30
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

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> list[dict[str, Any]]:
        return []

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
                            "text": "阶段三开始支持只读 TMDb 精确匹配。完整私密链接不会写入候选记录；自动整理默认关闭。",
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
                        "component": "VTextField",
                        "props": {
                            "model": "rss_interval_minutes",
                            "label": "RSS 刷新间隔（分钟）",
                            "type": "number",
                            "min": 10,
                            "max": 1440,
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
                            "label": "高置信度识别后自动整理（谨慎开启）",
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
            "rss_interval_minutes": 30,
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
                        f"V2 阶段三已启用：{len(candidates)} 个候选，{len(tasks)} 个任务，"
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

        services = [{
            "id": "PigGoKidsMetadata.DownloadTracking",
            "name": "PigGo 儿童下载状态恢复",
            "trigger": IntervalTrigger(minutes=5),
            "func": self.reconcile_downloads,
            "kwargs": {},
        }]
        if self._rss_urls:
            services.append({
                "id": "PigGoKidsMetadata.RssRefresh",
                "name": "PigGo 儿童 RSS 刷新",
                "trigger": IntervalTrigger(minutes=self._rss_interval_minutes),
                "func": self.refresh_candidates,
                "kwargs": {},
            })
        return services

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
        if candidate.source_feed_id != "manual":
            self.refresh_candidates()
            return self._candidate_download_references.get(candidate.candidate_id)
        return None

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
        media = MediaInfo(type=mtype, title=candidate.title)
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
            label="piggokids",
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

    def _transfer_event_file_key(self, task: ImportTask, data: dict[str, Any]) -> Optional[str]:
        """把宿主绝对文件路径映射为任务预期的安全相对文件键。"""

        fileitem = data.get("fileitem")
        raw_path = getattr(fileitem, "path", None)
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
            if decision.auto_eligible and allow_auto_transfer and self._auto_transfer:
                self._start_host_transfer(task, decision.to_dict())
            return True, decision.to_dict(), "内容包识别完成"
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
        )
        return TransferChain().do_transfer(
            fileitem=fileitem,
            mediainfo=media,
            media_source=media_source,
            season=item.get("season"),
            scrape=False,
            background=True,
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

    @eventmanager.register(EventType.TransferComplete)
    def on_transfer_complete(self, event: Event) -> None:
        with self._state_lock:
            if not self._enabled:
                return
            data = self._event_data(event)
            task = self._find_task_by_hash(data.get("download_hash"))
            if task and task.state not in {TaskState.COMPLETED, TaskState.IGNORED}:
                if self._record_transfer_result(task, data, success=True):
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

    def reconcile_downloads(self) -> dict[str, Any]:
        if not self._enabled:
            return self._response(False, message="插件尚未启用")
        tasks = [
            ImportTask.from_dict(raw)
            for raw in self._load_tasks()
            if raw.get("state") not in {TaskState.COMPLETED.value, TaskState.IGNORED.value}
        ]
        hashes = sorted({
            value
            for value in (normalize_download_hash(task.download_hash) for task in tasks)
            if value
        })
        try:
            from app.chain.download import DownloadChain

            chain = DownloadChain()
            try:
                torrents = (
                    chain.list_torrents(
                        downloader=self._downloader or None,
                        hashs=hashes,
                    )
                    if hashes else []
                ) or []
            except (AttributeError, TypeError):
                torrents = chain.downloading(self._downloader or None) or []
        except Exception:
            logger.error("PigGoKidsMetadata V2 下载状态恢复失败：unexpected_error")
            return self._response(False, message="下载状态查询失败")
        by_hash = {}
        for torrent in torrents:
            if isinstance(torrent, dict):
                value = torrent
            elif callable(getattr(torrent, "model_dump", None)):
                value = torrent.model_dump()
            else:
                value = vars(torrent)
            download_hash = normalize_download_hash(value.get("hash"))
            if download_hash:
                by_hash[download_hash] = value
        tracked = 0
        scanned = 0
        for task in tasks:
            if task.state == TaskState.READY_TO_TRANSFER and self._auto_transfer:
                decision = self._load_decisions().get(task.task_id)
                if decision and self._start_host_transfer(task, decision)[0]:
                    tracked += 1
                continue
            torrent = by_hash.get(normalize_download_hash(task.download_hash) or "")
            if not torrent:
                continue
            tracked += 1
            task.downloader = str(torrent.get("downloader") or task.downloader or "") or None
            try:
                completed = float(torrent.get("progress") or 0) >= 100
            except (TypeError, ValueError):
                completed = False
            if not completed:
                self._save_task(task)
                continue
            relative = self._relative_source_from_host_path(torrent.get("path"))
            if relative:
                task.relative_source_path = relative
            self._save_task(task)
            if task.relative_source_path:
                success, _, _ = self._scan_task(task, reason="download_poll_completed")
                scanned += int(success)
        return self._response(True, {"tracked": tracked, "scanned": scanned})

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
            "rss_interval_minutes": self._rss_interval_minutes,
            "downloader_configured": bool(self._downloader),
            "download_save_path_configured": bool(self._download_save_path),
            "auto_transfer": self._auto_transfer,
            "public_match_enabled": self._public_match_enabled,
            "config_error": self._config_error,
            "phase": 3,
            "v2_media_source_adapter": False,
        })

    def api_registry(self) -> dict[str, Any]:
        return self._response(True, {
            "items": list(self._load_registry().values()),
            "decisions": list(self._load_decisions().values()),
        })

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
            safe_limit = max(1, min(500, int(limit or 100)))
        except (TypeError, ValueError):
            safe_limit = 100
        items = []
        total = 0
        for item in reversed(self._load_candidates()):
            if normalized_query and normalized_query not in normalize_title(item.title):
                continue
            if status_value and item.status.value != status_value:
                continue
            if type_value and item.media_type.value != type_value:
                continue
            total += 1
            if len(items) < safe_limit:
                items.append(item.to_dict())
        return self._response(True, {"items": items, "total": total})

    def api_refresh_candidates(self, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        del payload
        return self.refresh_candidates()

    def api_import_candidate(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._enabled:
            return self._response(False, message="插件尚未启用")
        values = dict(payload or {})
        try:
            kind = MediaKind(str(values.get("media_type") or MediaKind.UNKNOWN.value).casefold())
            parsed = candidate_from_reference(
                str(values.get("download_reference") or ""),
                title=str(values.get("title") or "").strip() or None,
                media_type=kind,
            )
            self._candidate_download_references[parsed.candidate.candidate_id] = parsed.download_reference
            merged = upsert_candidates(self._load_candidates(), [parsed.candidate])
            self._save_candidates(merged)
            candidate = next(item for item in merged if item.candidate_id == parsed.candidate.candidate_id)
            return self._response(True, {"candidate": candidate.to_dict()})
        except (InvalidReferenceError, ValueError) as error:
            return self._response(False, message=str(error))

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
        return self._response(
            False,
            {"task": task.to_dict()},
            "当前任务状态没有可执行的重试动作",
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
