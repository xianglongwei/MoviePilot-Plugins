"""PigGo RSS 候选资源的安全解析和去重核心。

本模块只使用 Python 标准库。持久化对象只包含脱敏 URL 和不可逆指纹；完整
RSS、种子或磁力链接只存在于一次请求的内存中，由宿主适配层即时提交下载。
"""

from __future__ import annotations

import hashlib
import html
import ipaddress
import re
import socket
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
from typing import Any, Iterable, Optional
from urllib.parse import parse_qs, unquote, urlsplit

from .core import MediaKind, PigGoCoreError, normalize_site_item_id, redact_text, redact_url


MAX_FEED_BYTES = 5 * 1024 * 1024
MAX_FEED_ITEMS = 500
MAX_CANDIDATES = 1_000
MAX_REFERENCE_LENGTH = 8_192
_SITE_ID_QUERY_KEYS = ("id", "torrent_id", "torrentid", "tid")
_SITE_ID_PATH = re.compile(
    r"(?i)/(?:details?|torrent|download)(?:\.php)?/(?:id/)?([A-Za-z0-9._-]{1,80})(?:/|$)"
)
_TV_HINT = re.compile(r"(?i)(?:^|[. _\-\[(])S\d{1,2}(?:E\d{1,3})?(?:$|[. _\-\])])")
_BTIH = re.compile(r"(?i)(?:^|&)xt=urn:btih:([a-z0-9]{32,40})(?:&|$)")
_IMAGE_SOURCE = re.compile(
    r'''(?is)<img\b[^>]*?\bsrc\s*=\s*(?:"([^"]+)"|'([^']+)'|([^\s"'<>`]+))'''
)


class FeedParseError(PigGoCoreError):
    """RSS/Atom 内容过大、不安全或格式无效。"""


class InvalidReferenceError(PigGoCoreError):
    """RSS 或下载引用不符合允许的 URL/磁力链接边界。"""


class CandidateStatus(str, Enum):
    DISCOVERED = "discovered"
    SELECTED = "selected"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    IGNORED = "ignored"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def reference_fingerprint(value: str) -> str:
    """为私密引用生成不可逆、可稳定比较的 SHA-256 指纹。"""

    return hashlib.sha256(str(value or "").strip().encode("utf-8")).hexdigest()


def feed_id_for_url(value: str) -> str:
    return f"feed:{reference_fingerprint(value)[:20]}"


def validate_feed_url(value: str) -> str:
    """校验 RSS 地址；凭据只能放在查询参数，禁止 URL userinfo。"""

    text = str(value or "").strip()
    if not text or len(text) > MAX_REFERENCE_LENGTH:
        raise InvalidReferenceError("RSS 地址为空或过长")
    try:
        parts = urlsplit(text)
    except ValueError as error:
        raise InvalidReferenceError("RSS 地址格式无效") from error
    if parts.scheme.casefold() not in {"http", "https"} or not parts.hostname:
        raise InvalidReferenceError("RSS 地址必须是有效的 HTTP 或 HTTPS URL")
    if parts.username or parts.password:
        raise InvalidReferenceError("RSS 地址不允许在 URL 中嵌入用户名或密码")
    return text


def validate_public_http_url(value: str, *, resolver: Any = None) -> str:
    """拒绝会把宿主网络客户端引向本机、内网或保留地址的 URL。"""

    text = validate_feed_url(value)
    parts = urlsplit(text)
    hostname = str(parts.hostname or "").casefold().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise InvalidReferenceError("URL 不允许访问本机或内网地址")
    addresses: set[str] = set()
    try:
        addresses.add(str(ipaddress.ip_address(hostname)))
    except ValueError:
        lookup = resolver or socket.getaddrinfo
        try:
            for record in lookup(hostname, parts.port or None, type=socket.SOCK_STREAM):
                if len(record) >= 5 and record[4]:
                    addresses.add(str(record[4][0]))
        except (OSError, socket.gaierror) as error:
            raise InvalidReferenceError("URL 主机名无法安全解析") from error
    if not addresses:
        raise InvalidReferenceError("URL 主机名无法安全解析")
    for address in addresses:
        try:
            if not ipaddress.ip_address(address).is_global:
                raise InvalidReferenceError("URL 不允许访问本机、内网或保留地址")
        except ValueError as error:
            raise InvalidReferenceError("URL 主机名解析结果无效") from error
    return text


def parse_feed_urls_config(value: Any, *, maximum: int = 10) -> list[str]:
    """把插件配置中的多行 RSS 地址转换为去重列表。"""

    if isinstance(value, (list, tuple, set)):
        rows = [str(item or "").strip() for item in value]
    else:
        rows = [item.strip() for item in str(value or "").splitlines()]
    urls: list[str] = []
    for row in rows:
        if not row:
            continue
        normalized = validate_feed_url(row)
        if normalized not in urls:
            urls.append(normalized)
        if len(urls) >= maximum:
            break
    return urls


def validate_download_reference(value: str) -> str:
    """仅接受可由 MoviePilot 下载链处理的 HTTP(S) 或 BTIH 磁力引用。"""

    text = str(value or "").strip()
    if not text or len(text) > MAX_REFERENCE_LENGTH:
        raise InvalidReferenceError("下载引用为空或过长")
    try:
        parts = urlsplit(text)
    except ValueError as error:
        raise InvalidReferenceError("下载引用格式无效") from error
    scheme = parts.scheme.casefold()
    if scheme == "magnet":
        if not _BTIH.search(parts.query):
            raise InvalidReferenceError("磁力链接缺少有效 BTIH")
        return text
    if scheme not in {"http", "https"} or not parts.hostname:
        raise InvalidReferenceError("下载引用必须是 HTTP、HTTPS 或 BTIH 磁力链接")
    if parts.username or parts.password:
        raise InvalidReferenceError("下载引用不允许在 URL 中嵌入用户名或密码")
    return text


def _local_name(tag: Any) -> str:
    return str(tag or "").rsplit("}", 1)[-1].casefold()


def _direct_text(element: ET.Element, *names: str) -> str:
    wanted = {name.casefold() for name in names}
    for child in element:
        if _local_name(child.tag) in wanted and child.text and child.text.strip():
            return child.text.strip()
    return ""


def _bounded_text(value: Any, maximum: int) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:maximum]


def _parse_date(value: str) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    parsed: Optional[datetime] = None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _optional_size(value: Any) -> Optional[int]:
    try:
        size = int(str(value or "0"))
    except (TypeError, ValueError, OverflowError):
        return None
    return size if 0 <= size <= 100 * 1024**4 else None


def extract_site_item_id(*references: str) -> Optional[str]:
    """从 NexusPHP 常见详情/下载 URL 中提取非敏感条目 ID。"""

    for reference in references:
        if not reference:
            continue
        try:
            parts = urlsplit(reference)
        except ValueError:
            continue
        query = parse_qs(parts.query, keep_blank_values=False)
        for key in _SITE_ID_QUERY_KEYS:
            for value in query.get(key, []):
                if item_id := normalize_site_item_id(value):
                    return item_id
        if match := _SITE_ID_PATH.search(parts.path):
            if item_id := normalize_site_item_id(match.group(1)):
                return item_id
    return None


def _media_kind_hint(title: str, category: str) -> MediaKind:
    text = f"{category} {title}".casefold()
    if _TV_HINT.search(title) or any(token in text for token in ("tv", "series", "电视剧", "动画剧集")):
        return MediaKind.TV
    if any(token in text for token in ("movie", "film", "电影", "剧场版")):
        return MediaKind.MOVIE
    return MediaKind.UNKNOWN


def _candidate_id(
    *, site_item_id: Optional[str], guid: str, title: str, published_at: Optional[str], size_bytes: Optional[int]
) -> str:
    if site_item_id:
        canonical = f"site:{site_item_id}"
    elif guid:
        canonical = f"guid:{guid}"
    else:
        canonical = f"fallback:{title}|{published_at or ''}|{size_bytes or 0}"
    return f"candidate:{reference_fingerprint(canonical)[:24]}"


@dataclass
class FeedCandidate:
    """可安全持久化和展示的候选；不包含完整私密下载引用。"""

    candidate_id: str
    source_feed_id: str
    item_fingerprint: str
    reference_fingerprint: str
    title: str
    status: CandidateStatus = CandidateStatus.DISCOVERED
    site_item_id: Optional[str] = None
    summary: Optional[str] = None
    category: Optional[str] = None
    media_type: MediaKind = MediaKind.UNKNOWN
    published_at: Optional[str] = None
    size_bytes: Optional[int] = None
    detail_url: Optional[str] = None
    download_url: Optional[str] = None
    task_id: Optional[str] = None
    title_overridden: bool = False
    media_type_overridden: bool = False
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["media_type"] = self.media_type.value
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FeedCandidate":
        values = dict(payload or {})
        try:
            values["status"] = CandidateStatus(values.get("status", CandidateStatus.DISCOVERED.value))
        except ValueError:
            values["status"] = CandidateStatus.DISCOVERED
        try:
            values["media_type"] = MediaKind(values.get("media_type", MediaKind.UNKNOWN.value))
        except ValueError:
            values["media_type"] = MediaKind.UNKNOWN
        allowed = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in values.items() if key in allowed})


@dataclass
class ParsedCandidate:
    """一次 RSS 解析的瞬态结果；完整下载和图片引用绝不持久化。"""

    candidate: FeedCandidate
    download_reference: str = field(repr=False)
    artwork_references: tuple[str, ...] = field(default_factory=tuple, repr=False)


def _extract_artwork_references(description: str, *, maximum: int = 4) -> tuple[str, ...]:
    """从 RSS HTML 摘要提取瞬态公网图片 URL，不在解析阶段触发 DNS。"""

    references: list[str] = []
    for match in _IMAGE_SOURCE.finditer(html.unescape(str(description or ""))):
        value = next((item for item in match.groups() if item), "").strip()
        if not value or len(value) > MAX_REFERENCE_LENGTH:
            continue
        try:
            value = validate_feed_url(value)
        except InvalidReferenceError:
            continue
        hostname = str(urlsplit(value).hostname or "").casefold().rstrip(".")
        if hostname == "localhost" or hostname.endswith(".localhost"):
            continue
        try:
            if not ipaddress.ip_address(hostname).is_global:
                continue
        except ValueError:
            pass
        if value not in references:
            references.append(value)
        if len(references) >= max(1, min(10, int(maximum))):
            break
    return tuple(references)


def _entry_links(entry: ET.Element) -> tuple[str, str, Optional[int]]:
    detail = ""
    enclosure = ""
    size: Optional[int] = None
    for child in entry:
        if _local_name(child.tag) == "enclosure":
            enclosure = str(child.attrib.get("url") or child.attrib.get("href") or child.text or "").strip()
            size = _optional_size(child.attrib.get("length"))
            continue
        if _local_name(child.tag) != "link":
            continue
        href = str(child.attrib.get("href") or child.text or "").strip()
        rel = str(child.attrib.get("rel") or "alternate").casefold()
        if rel == "enclosure":
            enclosure = enclosure or href
            size = size if size is not None else _optional_size(child.attrib.get("length"))
        elif not detail:
            detail = href
    return detail, enclosure or detail, size


def parse_feed_document(
    content: bytes | str,
    *,
    source_feed_id: str,
    max_bytes: int = MAX_FEED_BYTES,
    max_items: int = MAX_FEED_ITEMS,
) -> list[ParsedCandidate]:
    """安全解析 RSS 2.0/Atom，并返回安全候选和瞬态下载引用。"""

    raw = content.encode("utf-8") if isinstance(content, str) else bytes(content or b"")
    if not raw:
        raise FeedParseError("RSS 内容为空")
    if len(raw) > max(1_024, min(MAX_FEED_BYTES, int(max_bytes))):
        raise FeedParseError("RSS 内容超过安全大小限制")
    markup = raw.upper()
    if b"<!DOCTYPE" in markup or b"<!ENTITY" in markup:
        raise FeedParseError("RSS 包含不允许的实体或文档类型声明")
    try:
        root = ET.fromstring(raw)
    except (ET.ParseError, ValueError) as error:
        raise FeedParseError("RSS 不是可安全解析的 XML") from error

    entries = [element for element in root.iter() if _local_name(element.tag) in {"item", "entry"}]
    if len(entries) > max_items:
        entries = entries[:max_items]
    results: list[ParsedCandidate] = []
    seen: set[str] = set()
    for entry in entries:
        title = _bounded_text(_direct_text(entry, "title"), 500)
        if not title:
            continue
        detail, download, enclosure_size = _entry_links(entry)
        if detail:
            try:
                detail = validate_feed_url(detail)
            except InvalidReferenceError:
                detail = ""
        try:
            download = validate_download_reference(download)
        except InvalidReferenceError:
            continue
        guid = _bounded_text(_direct_text(entry, "guid", "id"), 2_048)
        description = _bounded_text(_direct_text(entry, "description", "summary", "content"), 4_000)
        category = _bounded_text(_direct_text(entry, "category"), 200)
        published = _parse_date(_direct_text(entry, "pubdate", "published", "updated"))
        site_item_id = extract_site_item_id(detail, guid, download)
        size = enclosure_size or _optional_size(_direct_text(entry, "size"))
        candidate_id = _candidate_id(
            site_item_id=site_item_id,
            guid=guid,
            title=title,
            published_at=published,
            size_bytes=size,
        )
        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        candidate = FeedCandidate(
            candidate_id=candidate_id,
            source_feed_id=str(source_feed_id),
            item_fingerprint=reference_fingerprint(guid or f"{title}|{published or ''}|{size or 0}"),
            reference_fingerprint=reference_fingerprint(download),
            title=title,
            site_item_id=site_item_id,
            summary=redact_text(description) or None,
            category=category or None,
            media_type=_media_kind_hint(title, category),
            published_at=published,
            size_bytes=size,
            detail_url=redact_url(detail) if detail else None,
            download_url=redact_url(download),
        )
        results.append(ParsedCandidate(
            candidate=candidate,
            download_reference=download,
            artwork_references=_extract_artwork_references(description),
        ))
    return results


def candidate_from_reference(
    reference: str,
    *,
    title: Optional[str] = None,
    media_type: MediaKind = MediaKind.UNKNOWN,
) -> ParsedCandidate:
    """把用户粘贴的下载引用转换为不泄密的本地候选。"""

    download = validate_download_reference(reference)
    parts = urlsplit(download)
    inferred_title = str(title or "").strip()
    if not inferred_title and parts.scheme.casefold() == "magnet":
        inferred_title = unquote((parse_qs(parts.query).get("dn") or [""])[0]).strip()
    if not inferred_title:
        inferred_title = unquote(parts.path.rsplit("/", 1)[-1]).strip() or "手工粘贴资源"
    inferred_title = _bounded_text(inferred_title, 500)
    fingerprint = reference_fingerprint(download)
    site_item_id = extract_site_item_id(download)
    candidate_id = _candidate_id(
        site_item_id=site_item_id,
        guid=f"manual:{fingerprint}",
        title=inferred_title,
        published_at=None,
        size_bytes=None,
    )
    return ParsedCandidate(
        candidate=FeedCandidate(
            candidate_id=candidate_id,
            source_feed_id="manual",
            item_fingerprint=fingerprint,
            reference_fingerprint=fingerprint,
            title=inferred_title,
            site_item_id=site_item_id,
            media_type=media_type,
            download_url=redact_url(download),
        ),
        download_reference=download,
    )


def upsert_candidates(
    existing: Iterable[FeedCandidate],
    incoming: Iterable[FeedCandidate],
    *,
    maximum: int = MAX_CANDIDATES,
) -> list[FeedCandidate]:
    """按稳定候选 ID 幂等合并，保留已有状态和任务关联。"""

    merged = {item.candidate_id: item for item in existing}
    for item in incoming:
        previous = merged.get(item.candidate_id)
        if previous:
            item.status = previous.status
            item.task_id = previous.task_id
            item.title_overridden = previous.title_overridden
            item.media_type_overridden = previous.media_type_overridden
            if previous.title_overridden:
                item.title = previous.title
            if previous.media_type_overridden:
                item.media_type = previous.media_type
            item.created_at = previous.created_at
        item.updated_at = utc_now()
        merged[item.candidate_id] = item
    values = sorted(merged.values(), key=lambda item: (item.published_at or item.created_at, item.candidate_id))
    return values[-max(1, min(MAX_CANDIDATES, int(maximum))):]
