"""PigGoKidsMetadata 的纯 Python 领域核心。

本模块不导入 MoviePilot，便于在宿主外测试下载内容包扫描、安全边界、
NFO 解析、稳定身份和识别决策。V2/V3 适配层应保持这份文件一致。
"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


MEDIA_SOURCE = "piggokids"
REDACTED = "***"

VIDEO_EXTENSIONS = {
    ".3gp", ".avi", ".flv", ".iso", ".m2ts", ".m4v", ".mkv",
    ".mov", ".mp4", ".mpeg", ".mpg", ".mts", ".rmvb", ".ts",
    ".vob", ".webm", ".wmv",
}
SUBTITLE_EXTENSIONS = {".ass", ".idx", ".smi", ".srt", ".ssa", ".sub", ".sup", ".vtt"}
IMAGE_EXTENSIONS = {".jpeg", ".jpg", ".png", ".webp"}
SECRET_QUERY_KEYS = {
    "access_token", "api_key", "apikey", "auth", "authorization", "cookie",
    "credential", "key", "passkey", "password", "session", "sessionid",
    "sid", "sign", "signature", "token", "uid_hash",
}

_URL_PATTERN = re.compile(r"(?P<url>(?:https?|magnet):[^\s<>'\"]+)", re.IGNORECASE)
_SECRET_SEGMENT = re.compile(
    r"^(?:(?=[A-Za-z0-9_-]{24,}$)(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9_-]+|[a-fA-F0-9]{32,})$"
)
_EPISODE_PATTERN = re.compile(
    r"(?i)(?:^|[. _\-\[\(])S(?P<season>\d{1,2})[. _\-]*E(?P<episode>\d{1,3})(?:$|[. _\-\]\)])"
)
_SEASON_PATTERN = re.compile(r"(?i)(?:^|[. _\-\[\(])S(?P<season>\d{1,2})(?:$|[. _\-\]\)])")
_SAMPLE_TOKENS = {
    "bonus", "extras", "extra", "preview", "sample", "samples", "trailer",
    "trailers", "behindthescenes", "花絮", "样片", "預告", "预告",
}


class PigGoCoreError(ValueError):
    """领域核心可安全展示给调用方的基础异常。"""


class UnsafePathError(PigGoCoreError):
    """扫描目标越界、使用符号链接或不满足目录约束。"""


class ScanLimitError(PigGoCoreError):
    """内容包超过扫描文件数或深度限制。"""


class NfoParseError(PigGoCoreError):
    """NFO 不安全、过大或不是可解析 XML。"""


class InvalidTaskTransition(PigGoCoreError):
    """任务状态迁移不符合状态机。"""


class MediaKind(str, Enum):
    MOVIE = "movie"
    TV = "tv"
    COLLECTION = "collection"
    UNKNOWN = "unknown"


class ConflictSeverity(str, Enum):
    WARNING = "warning"
    HARD = "hard"


class TaskState(str, Enum):
    DISCOVERED = "DISCOVERED"
    SELECTED = "SELECTED"
    DOWNLOAD_SUBMITTED = "DOWNLOAD_SUBMITTED"
    DOWNLOADING = "DOWNLOADING"
    DOWNLOADED = "DOWNLOADED"
    SCANNING = "SCANNING"
    MATCHING = "MATCHING"
    READY_TO_TRANSFER = "READY_TO_TRANSFER"
    TRANSFERRING = "TRANSFERRING"
    LIBRARY_REFRESHING = "LIBRARY_REFRESHING"
    COMPLETED = "COMPLETED"
    RETRYABLE_FAILED = "RETRYABLE_FAILED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    IGNORED = "IGNORED"


_NORMAL_TRANSITIONS = {
    TaskState.DISCOVERED: {TaskState.SELECTED, TaskState.IGNORED},
    TaskState.SELECTED: {TaskState.DOWNLOAD_SUBMITTED, TaskState.IGNORED},
    TaskState.DOWNLOAD_SUBMITTED: {TaskState.DOWNLOADING, TaskState.DOWNLOADED},
    TaskState.DOWNLOADING: {TaskState.DOWNLOADED},
    TaskState.DOWNLOADED: {TaskState.SCANNING},
    TaskState.SCANNING: {TaskState.MATCHING},
    TaskState.MATCHING: {TaskState.READY_TO_TRANSFER, TaskState.NEEDS_REVIEW},
    TaskState.READY_TO_TRANSFER: {TaskState.TRANSFERRING, TaskState.NEEDS_REVIEW},
    TaskState.TRANSFERRING: {TaskState.LIBRARY_REFRESHING},
    TaskState.LIBRARY_REFRESHING: {TaskState.COMPLETED},
    TaskState.RETRYABLE_FAILED: {
        TaskState.DOWNLOAD_SUBMITTED, TaskState.DOWNLOADING, TaskState.DOWNLOADED,
        TaskState.SCANNING, TaskState.MATCHING, TaskState.READY_TO_TRANSFER,
        TaskState.TRANSFERRING, TaskState.LIBRARY_REFRESHING,
    },
    TaskState.NEEDS_REVIEW: {TaskState.MATCHING, TaskState.READY_TO_TRANSFER, TaskState.IGNORED},
    TaskState.COMPLETED: set(),
    TaskState.IGNORED: set(),
}
_FAILABLE_STATES = set(TaskState) - {
    TaskState.COMPLETED, TaskState.IGNORED, TaskState.NEEDS_REVIEW,
    TaskState.RETRYABLE_FAILED,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_value(value: Any) -> Any:
    """把领域对象递归转换成可由插件数据接口保存的值。"""

    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    return value


@dataclass
class ImportTask:
    """跨抓取、下载、识别与整理阶段的可审计任务状态。"""

    task_id: str
    state: TaskState = TaskState.DISCOVERED
    site_item_id: Optional[str] = None
    candidate_id: Optional[str] = None
    downloader: Optional[str] = None
    download_id: Optional[str] = None
    download_hash: Optional[str] = None
    relative_source_path: Optional[str] = None
    torrent_files: list[str] = field(default_factory=list)
    media_id: Optional[str] = None
    last_error_code: Optional[str] = None
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)
    history: list[dict[str, str]] = field(default_factory=list)

    def transition(self, target: TaskState, reason: str = "") -> None:
        """执行幂等状态迁移并保留原因。"""

        if target == self.state:
            return
        allowed = set(_NORMAL_TRANSITIONS.get(self.state, set()))
        if self.state in _FAILABLE_STATES:
            allowed.add(TaskState.RETRYABLE_FAILED)
        if target not in allowed:
            raise InvalidTaskTransition(f"不允许从 {self.state.value} 迁移到 {target.value}")
        previous = self.state
        self.state = target
        self.updated_at = _utc_now()
        self.history.append({
            "from": previous.value,
            "to": target.value,
            "reason": str(reason or ""),
            "time": self.updated_at,
        })

    def to_dict(self) -> dict[str, Any]:
        return _json_value(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ImportTask":
        """从插件数据恢复任务，并忽略未来版本增加的未知字段。"""

        values = dict(payload or {})
        try:
            values["state"] = TaskState(values.get("state", TaskState.DISCOVERED.value))
        except ValueError:
            values["state"] = TaskState.RETRYABLE_FAILED
            values["last_error_code"] = "invalid_persisted_state"
        values["torrent_files"] = [
            str(item)[:1_000]
            for item in values.get("torrent_files") or []
            if isinstance(item, str) and item
        ][:10_000]
        values["history"] = [
            dict(item)
            for item in values.get("history") or []
            if isinstance(item, Mapping)
        ][-500:]
        allowed = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in values.items() if key in allowed})


@dataclass(frozen=True)
class ScanPolicy:
    """限制单次内容包扫描的资源使用和文件读取范围。"""

    max_files: int = 10_000
    max_depth: int = 12
    max_nfo_bytes: int = 2 * 1024 * 1024


@dataclass
class DownloadedPayload:
    """仅包含相对路径的下载内容包清单，避免向日志泄露绝对路径。"""

    root_name: str
    media_files: list[str] = field(default_factory=list)
    nfo_files: list[str] = field(default_factory=list)
    artwork_files: list[str] = field(default_factory=list)
    subtitle_files: list[str] = field(default_factory=list)
    ignored_files: list[str] = field(default_factory=list)
    skipped_symlinks: list[str] = field(default_factory=list)
    file_sizes: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _json_value(self)


@dataclass
class NfoMetadata:
    """从一个安全 NFO 中读取的常用本地元数据。"""

    path: str
    root_type: str
    title: Optional[str] = None
    original_title: Optional[str] = None
    show_title: Optional[str] = None
    year: Optional[str] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    plot: Optional[str] = None
    genres: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    studios: list[str] = field(default_factory=list)
    unique_ids: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _json_value(self)


@dataclass
class RecognitionConflict:
    """识别证据之间的显式冲突或安全警告。"""

    code: str
    message: str
    severity: ConflictSeverity = ConflictSeverity.HARD
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _json_value(self)


@dataclass
class LocalMediaItem:
    """可登记为 ``piggokids`` 来源的本地媒体条目。"""

    media_source: str
    media_id: str
    media_type: MediaKind
    title: str
    original_title: Optional[str] = None
    year: Optional[str] = None
    season: Optional[int] = None
    episode_count: Optional[int] = None
    overview: Optional[str] = None
    aliases: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    poster_file: Optional[str] = None
    fanart_file: Optional[str] = None
    source_fields: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _json_value(self)


@dataclass
class TransferPreview:
    """不触碰文件系统的整理目标相对路径预览。"""

    library_section: str
    media_directory: str
    file_mappings: list[dict[str, str]] = field(default_factory=list)
    nfo_target: Optional[str] = None
    poster_target: Optional[str] = None
    fanart_target: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return _json_value(self)


@dataclass
class RecognitionDecision:
    """一次下载后识别的完整可审计结果。"""

    item: Optional[LocalMediaItem]
    confidence: float
    auto_eligible: bool
    conflicts: list[RecognitionConflict]
    nfo_documents: list[NfoMetadata]
    payload: DownloadedPayload
    transfer_preview: Optional[TransferPreview]

    def to_dict(self) -> dict[str, Any]:
        return _json_value(self)


def redact_url(value: str) -> str:
    """脱敏 URL 中的用户信息、私密查询参数和路径型令牌。"""

    text = str(value or "")
    try:
        parts = urlsplit(text)
    except ValueError:
        return REDACTED
    if parts.scheme.lower() not in {"http", "https", "magnet"}:
        return text

    try:
        hostname = parts.hostname or ""
        port = parts.port
    except ValueError:
        return REDACTED
    if port:
        hostname = f"{hostname}:{port}"
    netloc = hostname
    if parts.username or parts.password:
        netloc = f"{REDACTED}@{hostname}"

    path_segments = []
    for segment in parts.path.split("/"):
        path_segments.append(REDACTED if _SECRET_SEGMENT.fullmatch(segment) else segment)
    path = "/".join(path_segments)

    query = []
    for key, item in parse_qsl(parts.query, keep_blank_values=True):
        sensitive = key.casefold() in SECRET_QUERY_KEYS or _SECRET_SEGMENT.fullmatch(item or "")
        query.append((key, REDACTED if sensitive else item))
    fragment = parts.fragment
    if "=" in fragment:
        fragment_items = []
        for key, item in parse_qsl(fragment, keep_blank_values=True):
            sensitive = key.casefold() in SECRET_QUERY_KEYS or _SECRET_SEGMENT.fullmatch(item or "")
            fragment_items.append((key, REDACTED if sensitive else item))
        fragment = urlencode(fragment_items, doseq=True, safe="*")
    elif _SECRET_SEGMENT.fullmatch(fragment or ""):
        fragment = REDACTED
    return urlunsplit((parts.scheme, netloc, path, urlencode(query, doseq=True, safe="*"), fragment))


def redact_text(value: str) -> str:
    """脱敏一段日志或错误文本中出现的 URL。"""

    return _URL_PATTERN.sub(lambda match: redact_url(match.group("url")), str(value or ""))


def normalize_title(value: str) -> str:
    """生成不依赖发布组标点风格的稳定标题键。"""

    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    text = re.sub(r"[\s._\-·•:：/\\]+", " ", text)
    text = re.sub(r"[^\w\u3400-\u9fff ]+", "", text, flags=re.UNICODE)
    return " ".join(text.split())


def normalize_site_item_id(value: Any) -> Optional[str]:
    """只接受不会携带 URL 参数或站点凭据的短站点条目标识。"""

    item_id = str(value or "").strip()
    return item_id if re.fullmatch(r"[A-Za-z0-9._-]{1,80}", item_id) else None


def normalize_download_hash(value: Any) -> Optional[str]:
    """只保留常见 BitTorrent infohash，避免把链接或令牌写入任务映射。"""

    download_hash = str(value or "").strip().casefold()
    return download_hash if re.fullmatch(r"[a-f0-9]{32,64}", download_hash) else None


def build_media_id(
    *,
    kind: MediaKind,
    title: str,
    year: Optional[str] = None,
    season: Optional[int] = None,
    site_item_id: Optional[str] = None,
    content_fingerprint: Optional[str] = None,
) -> str:
    """构造不包含私密 URL、passkey 或 token 的稳定来源内 ID。"""

    item_id = normalize_site_item_id(site_item_id)
    if item_id:
        return f"piggo:{kind.value}:item:{item_id}"
    canonical = "|".join([
        kind.value,
        normalize_title(title),
        str(year or ""),
        str(season if season is not None else ""),
        str(content_fingerprint or ""),
    ])
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return f"local:{kind.value}:{digest}"


def _relative_display(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix() or "."


def _is_sample_path(relative_path: str) -> bool:
    parts = re.split(r"[\\/._\-\s\[\]()]+", relative_path.casefold())
    return any(part in _SAMPLE_TOKENS for part in parts if part)


def _is_artwork(path: Path) -> bool:
    stem = path.stem.casefold()
    return bool(
        re.fullmatch(r"(?:movie|tvshow|poster|fanart|backdrop|cover|folder)(?:[-_].*)?", stem)
        or re.fullmatch(r"season(?:[-_ ]?\d+|[-_ ]?specials)?(?:[-_].*)?", stem)
    )


def _assert_safe_source(download_root: Path, source: Path) -> tuple[Path, Path]:
    root = download_root.expanduser().resolve(strict=True)
    candidate = source.expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=True)
    if resolved != root and root not in resolved.parents:
        raise UnsafePathError("扫描目标不在配置的下载根目录内")
    if not resolved.is_dir():
        raise UnsafePathError("扫描目标必须是目录")

    try:
        lexical_relative = candidate.absolute().relative_to(download_root.expanduser().absolute())
    except ValueError:
        lexical_relative = resolved.relative_to(root)
    current = download_root.expanduser().absolute()
    for part in lexical_relative.parts:
        current = current / part
        if current.is_symlink():
            raise UnsafePathError("扫描目标路径不能经过符号链接")
    return root, resolved


def scan_downloaded_payload(
    download_root: Path | str,
    source: Path | str,
    policy: ScanPolicy = ScanPolicy(),
) -> DownloadedPayload:
    """在任务所属目录内扫描真实内容包，不跟随任何符号链接。"""

    _, source_root = _assert_safe_source(Path(download_root), Path(source))
    payload = DownloadedPayload(root_name=source_root.name)
    visited = 0

    for current_text, directory_names, file_names in os.walk(source_root, followlinks=False):
        current = Path(current_text)
        depth = len(current.relative_to(source_root).parts)
        if depth > policy.max_depth:
            raise ScanLimitError("下载内容包目录层级超过安全限制")

        safe_directories = []
        for name in sorted(directory_names):
            child = current / name
            relative = _relative_display(child, source_root)
            if child.is_symlink():
                payload.skipped_symlinks.append(relative)
            else:
                safe_directories.append(name)
        directory_names[:] = safe_directories

        for name in sorted(file_names):
            visited += 1
            if visited > policy.max_files:
                raise ScanLimitError("下载内容包文件数量超过安全限制")
            path = current / name
            relative = _relative_display(path, source_root)
            if path.is_symlink():
                payload.skipped_symlinks.append(relative)
                continue
            try:
                stat = path.stat()
            except OSError:
                payload.ignored_files.append(relative)
                continue
            if not path.is_file():
                payload.ignored_files.append(relative)
                continue
            payload.file_sizes[relative] = int(stat.st_size)
            suffix = path.suffix.casefold()
            if suffix in VIDEO_EXTENSIONS:
                target = payload.ignored_files if _is_sample_path(relative) else payload.media_files
                target.append(relative)
            elif suffix == ".nfo":
                payload.nfo_files.append(relative)
            elif suffix in SUBTITLE_EXTENSIONS:
                payload.subtitle_files.append(relative)
            elif suffix in IMAGE_EXTENSIONS and _is_artwork(path):
                payload.artwork_files.append(relative)
            else:
                payload.ignored_files.append(relative)
    return payload


def _tag_name(element: ET.Element) -> str:
    return str(element.tag).rsplit("}", 1)[-1].casefold()


def _first_text(root: ET.Element, *names: str) -> Optional[str]:
    wanted = {name.casefold() for name in names}
    for element in root.iter():
        if _tag_name(element) in wanted and element.text and element.text.strip():
            return element.text.strip()[:20_000]
    return None


def _all_text(root: ET.Element, name: str) -> list[str]:
    values = []
    for element in root.iter():
        if _tag_name(element) == name.casefold() and element.text and element.text.strip():
            values.append(element.text.strip()[:500])
        if len(values) >= 100:
            break
    return list(dict.fromkeys(values))


def _optional_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_nfo(path: Path, relative_path: str, max_bytes: int = 2 * 1024 * 1024) -> NfoMetadata:
    """安全解析 Kodi/Emby/Jellyfin 常见 XML NFO 字段。"""

    if path.is_symlink() or not path.is_file():
        raise NfoParseError("NFO 必须是内容包内的普通文件")
    try:
        size = path.stat().st_size
    except OSError as error:
        raise NfoParseError("NFO 文件不可读取") from error
    if size > max_bytes:
        raise NfoParseError("NFO 文件超过安全大小限制")
    try:
        content = path.read_bytes()
    except OSError as error:
        raise NfoParseError("NFO 文件不可读取") from error
    if len(content) > max_bytes:
        raise NfoParseError("NFO 文件超过安全大小限制")
    upper = content[: min(len(content), 65_536)].upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise NfoParseError("NFO 包含不允许的实体或文档类型声明")
    try:
        root = ET.fromstring(content)
    except (ET.ParseError, ValueError) as error:
        raise NfoParseError("NFO 不是可安全解析的 XML") from error

    unique_ids: dict[str, str] = {}
    for element in root.iter():
        if _tag_name(element) != "uniqueid" or not element.text or not element.text.strip():
            continue
        source = str(element.attrib.get("type") or element.attrib.get("source") or "unknown").casefold()
        unique_ids[source] = element.text.strip()
    generic_id = _first_text(root, "id")
    if generic_id and "unknown" not in unique_ids:
        unique_ids["unknown"] = generic_id

    year_value = _first_text(root, "year") or _first_text(root, "premiered", "aired", "releasedate")
    year_match = re.search(r"(?:18|19|20|21)\d{2}", year_value or "")
    year = year_match.group(0) if year_match else None
    return NfoMetadata(
        path=relative_path,
        root_type=_tag_name(root),
        title=_first_text(root, "title"),
        original_title=_first_text(root, "originaltitle", "original_title"),
        show_title=_first_text(root, "showtitle", "show_title"),
        year=year,
        season=_optional_int(_first_text(root, "season")),
        episode=_optional_int(_first_text(root, "episode")),
        plot=_first_text(root, "plot", "outline"),
        genres=_all_text(root, "genre"),
        countries=_all_text(root, "country"),
        studios=_all_text(root, "studio"),
        unique_ids=unique_ids,
    )


def parse_payload_nfos(
    source_root: Path | str,
    payload: DownloadedPayload,
    policy: ScanPolicy = ScanPolicy(),
) -> tuple[list[NfoMetadata], list[RecognitionConflict]]:
    """解析扫描清单中的 NFO，并把失败转为可审计冲突。"""

    root = Path(source_root).resolve(strict=True)
    documents: list[NfoMetadata] = []
    conflicts: list[RecognitionConflict] = []
    for relative in payload.nfo_files:
        path = (root / relative).resolve(strict=True)
        if path != root and root not in path.parents:
            conflicts.append(RecognitionConflict(
                code="nfo_path_escape",
                message="NFO 路径越过内容包边界",
                evidence=[relative],
            ))
            continue
        try:
            documents.append(parse_nfo(path, relative, policy.max_nfo_bytes))
        except NfoParseError as error:
            conflicts.append(RecognitionConflict(
                code="nfo_parse_failed",
                message=str(error),
                severity=ConflictSeverity.WARNING,
                evidence=[relative],
            ))
    return documents, conflicts


def _clean_fallback_title(root_name: str) -> str:
    text = unicodedata.normalize("NFKC", root_name).strip()
    text = re.sub(r"[._]+", " ", text)
    text = re.sub(r"(?i)\b(?:19|20)\d{2}\b.*$", "", text).strip(" -._[]()")
    return " ".join(text.split()) or root_name


def _content_fingerprint(payload: DownloadedPayload) -> str:
    rows = [f"{path}:{payload.file_sizes.get(path, 0)}" for path in sorted(payload.media_files)]
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()[:24]


def _safe_path_component(value: str, fallback: str = "未命名媒体") -> str:
    """生成适用于常见本地媒体库的单个路径组件。"""

    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if text.casefold() in {"con", "prn", "aux", "nul", "com1", "lpt1"}:
        text = f"_{text}"
    return (text or fallback)[:120].rstrip(" .")


def build_transfer_preview(
    item: LocalMediaItem,
    payload: DownloadedPayload,
) -> tuple[Optional[TransferPreview], list[RecognitionConflict]]:
    """计算 MoviePilot 整理前可展示的相对目标路径，不执行任何写操作。"""

    conflicts: list[RecognitionConflict] = []
    title = _safe_path_component(item.title)
    title_year = f"{title} ({item.year})" if item.year else title

    def artwork_target(base: str, role: str, source: Optional[str]) -> Optional[str]:
        if not source:
            return None
        suffix = Path(source).suffix.casefold()
        return f"{base}/{role}{suffix if suffix in IMAGE_EXTENSIONS else '.jpg'}"

    if item.media_type == MediaKind.MOVIE:
        if len(payload.media_files) != 1:
            conflicts.append(RecognitionConflict(
                code="movie_file_count_conflict",
                message="电影内容包必须能确定唯一正片，当前不会自动合并多文件电影",
                evidence=payload.media_files[:10],
            ))
            return None, conflicts
        source = payload.media_files[0]
        extension = Path(source).suffix.casefold()
        base = f"儿童动画电影/{title_year}"
        return TransferPreview(
            library_section="儿童动画电影",
            media_directory=base,
            file_mappings=[{"source": source, "target": f"{base}/{title_year}{extension}"}],
            nfo_target=f"{base}/movie.nfo",
            poster_target=artwork_target(base, "poster", item.poster_file),
            fanart_target=artwork_target(base, "fanart", item.fanart_file),
        ), conflicts

    if item.media_type == MediaKind.TV:
        mappings = []
        for source in payload.media_files:
            match = _EPISODE_PATTERN.search(Path(source).name)
            if not match:
                conflicts.append(RecognitionConflict(
                    code="episode_number_missing",
                    message="剧集正片文件缺少可确认的 SxxExx 编号",
                    evidence=[source],
                ))
                continue
            season = int(match.group("season"))
            episode = int(match.group("episode"))
            extension = Path(source).suffix.casefold()
            base = f"儿童动画/{title_year}/Season {season:02d}"
            mappings.append({
                "source": source,
                "target": f"{base}/{title} - S{season:02d}E{episode:02d}{extension}",
            })
        if not mappings:
            return None, conflicts
        series_base = f"儿童动画/{title_year}"
        return TransferPreview(
            library_section="儿童动画",
            media_directory=series_base,
            file_mappings=mappings,
            nfo_target=f"{series_base}/tvshow.nfo",
            poster_target=artwork_target(series_base, "poster", item.poster_file),
            fanart_target=artwork_target(series_base, "fanart", item.fanart_file),
        ), conflicts

    return None, conflicts


def _artwork(payload: DownloadedPayload, kind: str) -> Optional[str]:
    patterns = {
        "poster": ("poster", "cover", "folder", "movie", "tvshow"),
        "fanart": ("fanart", "backdrop"),
    }[kind]
    for relative in payload.artwork_files:
        if Path(relative).stem.casefold().startswith(patterns):
            return relative
    return None


def decide_local_media(
    payload: DownloadedPayload,
    nfo_documents: Iterable[NfoMetadata],
    *,
    site_item_id: Optional[str] = None,
    minimum_confidence: float = 0.80,
    initial_conflicts: Optional[Iterable[RecognitionConflict]] = None,
) -> RecognitionDecision:
    """按随包元数据和文件结构生成本地身份或明确暂停原因。"""

    documents = list(nfo_documents)
    conflicts = list(initial_conflicts or [])
    movie_docs = [item for item in documents if item.root_type == "movie"]
    tv_docs = [item for item in documents if item.root_type in {"tvshow", "series"}]
    episode_docs = [item for item in documents if item.root_type in {"episodedetails", "episode"}]

    if movie_docs and (tv_docs or episode_docs):
        conflicts.append(RecognitionConflict(
            code="media_type_conflict",
            message="内容包同时包含电影和剧集 NFO",
            evidence=[item.path for item in movie_docs + tv_docs + episode_docs],
        ))

    top_titles = {
        normalize_title(item.title or ""): item.title
        for item in movie_docs + tv_docs
        if normalize_title(item.title or "")
    }
    if len(top_titles) > 1:
        conflicts.append(RecognitionConflict(
            code="multiple_top_level_titles",
            message="内容包包含多个不同的顶层媒体标题",
            evidence=sorted(str(value) for value in top_titles.values()),
        ))

    filename_seasons: set[int] = set()
    episode_numbers: set[tuple[int, int]] = set()
    for relative in payload.media_files:
        match = _EPISODE_PATTERN.search(Path(relative).name)
        if match:
            season = int(match.group("season"))
            episode = int(match.group("episode"))
            filename_seasons.add(season)
            episode_numbers.add((season, episode))
            continue
        for part in Path(relative).parts:
            season_match = _SEASON_PATTERN.search(part)
            if season_match:
                filename_seasons.add(int(season_match.group("season")))
    nfo_seasons = {item.season for item in episode_docs if item.season is not None}
    seasons = filename_seasons | nfo_seasons
    if len(seasons) > 1:
        conflicts.append(RecognitionConflict(
            code="multi_season_payload",
            message="内容包包含多个季，当前版本不会自动拆分整理",
            evidence=[str(value) for value in sorted(seasons)],
        ))

    if movie_docs and not tv_docs and not episode_docs:
        kind = MediaKind.MOVIE
        primary = movie_docs[0]
    elif tv_docs or episode_docs or episode_numbers or filename_seasons:
        kind = MediaKind.TV
        primary = (tv_docs or episode_docs)[0] if (tv_docs or episode_docs) else None
    elif len(payload.media_files) == 1:
        kind = MediaKind.MOVIE
        primary = None
    elif len(payload.media_files) > 1:
        kind = MediaKind.UNKNOWN
        primary = None
        conflicts.append(RecognitionConflict(
            code="unknown_multi_file_type",
            message="多个视频文件缺少可确认电影或剧集类型的证据",
            evidence=payload.media_files[:10],
        ))
    else:
        kind = MediaKind.UNKNOWN
        primary = None
        conflicts.append(RecognitionConflict(
            code="no_primary_media",
            message="内容包中没有可识别的正片文件",
        ))

    if len(seasons) > 1 or len(top_titles) > 1:
        kind = MediaKind.COLLECTION

    title = None
    title_source = None
    if primary:
        if primary.root_type in {"episodedetails", "episode"}:
            title = primary.show_title
        else:
            title = primary.title
        if title:
            title_source = f"nfo:{primary.path}"
    if not title:
        title = _clean_fallback_title(payload.root_name)
        title_source = "directory_name"
    if not normalize_title(title):
        conflicts.append(RecognitionConflict(
            code="missing_title",
            message="无法从内容包确定媒体标题",
        ))
        title = "未命名媒体"

    year = primary.year if primary else None
    season = min(seasons) if len(seasons) == 1 else (primary.season if primary else None)
    overview = primary.plot if primary else None
    aliases = []
    original_title = primary.original_title if primary else None
    if original_title and normalize_title(original_title) != normalize_title(title):
        aliases.append(original_title)
    genres = list(primary.genres) if primary else []

    source_fields = {"title": str(title_source), "media_type": "nfo_or_filename"}
    if year:
        source_fields["year"] = f"nfo:{primary.path}" if primary else "filename"
    if season is not None:
        source_fields["season"] = "nfo_or_filename"
    if overview:
        source_fields["overview"] = f"nfo:{primary.path}"

    confidence = 0.0
    confidence += 0.35 if primary and title_source.startswith("nfo:") else 0.15
    confidence += 0.20 if kind in {MediaKind.MOVIE, MediaKind.TV} and (movie_docs or tv_docs or episode_docs or episode_numbers) else 0.05
    confidence += 0.10 if year else 0.0
    confidence += 0.15 if payload.media_files else 0.0
    if kind == MediaKind.TV:
        confidence += 0.15 if episode_numbers or any(item.episode is not None for item in episode_docs) else 0.0
    elif kind == MediaKind.MOVIE:
        confidence += 0.15 if len(payload.media_files) == 1 else 0.0
    confidence += 0.05 if payload.artwork_files else 0.0
    confidence = round(min(1.0, confidence), 3)

    media_id = build_media_id(
        kind=kind,
        title=title,
        year=year,
        season=season,
        site_item_id=site_item_id,
        content_fingerprint=_content_fingerprint(payload),
    )
    item = LocalMediaItem(
        media_source=MEDIA_SOURCE,
        media_id=media_id,
        media_type=kind,
        title=title,
        original_title=original_title,
        year=year,
        season=season,
        episode_count=(len(episode_numbers) or len([item for item in episode_docs if item.episode is not None]) or None),
        overview=overview,
        aliases=aliases,
        genres=genres,
        poster_file=_artwork(payload, "poster"),
        fanart_file=_artwork(payload, "fanart"),
        source_fields=source_fields,
    )
    transfer_preview, preview_conflicts = build_transfer_preview(item, payload)
    conflicts.extend(preview_conflicts)
    hard_conflict = any(conflict.severity == ConflictSeverity.HARD for conflict in conflicts)
    auto_eligible = (
        confidence >= max(0.0, min(1.0, float(minimum_confidence)))
        and not hard_conflict
        and kind in {MediaKind.MOVIE, MediaKind.TV}
    )
    return RecognitionDecision(
        item=item,
        confidence=confidence,
        auto_eligible=auto_eligible,
        conflicts=conflicts,
        nfo_documents=documents,
        payload=payload,
        transfer_preview=transfer_preview,
    )


def inspect_downloaded_payload(
    download_root: Path | str,
    relative_source: Path | str,
    *,
    site_item_id: Optional[str] = None,
    minimum_confidence: float = 0.80,
    policy: ScanPolicy = ScanPolicy(),
) -> RecognitionDecision:
    """执行下载后识别链路：受限扫描、NFO 解析和识别决策。"""

    source = Path(relative_source)
    if source.is_absolute():
        raise UnsafePathError("调用方只能提交下载根目录内的相对路径")
    if ".." in source.parts:
        raise UnsafePathError("相对路径不能包含上级目录跳转")
    payload = scan_downloaded_payload(download_root, source, policy)
    source_root = (Path(download_root).expanduser().resolve(strict=True) / source).resolve(strict=True)
    documents, conflicts = parse_payload_nfos(source_root, payload, policy)
    return decide_local_media(
        payload,
        documents,
        site_item_id=site_item_id,
        minimum_confidence=minimum_confidence,
        initial_conflicts=conflicts,
    )
