"""下载完成后为 qBittorrent / Transmission 种子设置单任务上传限速。"""

from typing import Any, Dict, List, Optional

from apscheduler.triggers.interval import IntervalTrigger

from app import schemas
from app.core.module import ModuleManager
from app.sdk.logging import logger
from app.sdk.plugin import _PluginBase
from app.schemas.types import DownloaderType


PLUGIN_ID = "QBUploadCap"


class QBUploadCap(_PluginBase):
    """对已完成的 qBittorrent 和 Transmission 任务逐个限速，避免关闭做种影响 tracker 汇报。"""

    plugin_name = "QB/TR完成后上传限速"
    plugin_desc = "下载完成后将 qBittorrent 或 Transmission 单任务上传速度限制为指定值，默认 100 KB/s。"
    plugin_icon = "speed.png"
    plugin_version = "0.1.0"
    plugin_label = "qBittorrent,下载管理,上传限速"
    plugin_author = "xianglongwei"
    plugin_config_prefix = "qbuploadcap_"
    plugin_order = 60
    auth_level = 1

    _enabled = False
    _limit_kb = 100
    _interval = 60

    def init_plugin(self, config: Optional[dict] = None) -> None:
        config = config or {}
        self._enabled = bool(config.get("enabled"))
        self._limit_kb = self._bounded_int(config.get("limit_kb"), 100, 1, 102400)
        self._interval = self._bounded_int(config.get("interval"), 60, 10, 3600)

    @staticmethod
    def _bounded_int(value: Any, default: int, low: int, high: int) -> int:
        try:
            return max(low, min(high, int(value)))
        except (TypeError, ValueError):
            return default

    def get_state(self) -> bool:
        return self._enabled

    def get_service(self) -> List[Dict[str, Any]]:
        if not self._enabled:
            return []
        return [{
            "id": f"{PLUGIN_ID}CheckCompleted",
            "name": "QB/TR完成任务上传限速",
            "trigger": IntervalTrigger(seconds=self._interval),
            "func": self.apply_limits,
            "kwargs": {},
        }]

    def stop_service(self) -> None:
        return None

    def get_form(self):
        return [
            {"component": "VForm", "content": [
                {"component": "VSwitch", "props": {"model": "enabled", "label": "启用插件"}},
                {"component": "VRow", "content": [
                    {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                        {"component": "VTextField", "props": {
                            "model": "limit_kb", "label": "上传限速（KB/s）", "type": "number",
                            "placeholder": "100", "hint": "默认 100 KB/s，按单个种子设置",
                            "persistent-hint": True,
                        }}
                    ]},
                    {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                        {"component": "VTextField", "props": {
                            "model": "interval", "label": "检查间隔（秒）", "type": "number",
                            "placeholder": "60", "hint": "最短 10 秒",
                            "persistent-hint": True,
                        }}
                    ]},
                ]},
            ]}
        ], {"enabled": False, "limit_kb": 100, "interval": 60}

    def get_page(self) -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return [{
            "path": "/run", "endpoint": self.api_run, "methods": ["POST"],
            "auth": "bear", "summary": "立即检查已完成任务", "response_model": schemas.Response,
        }]

    def api_run(self) -> schemas.Response:
        count = self.apply_limits()
        return schemas.Response(success=True, message=f"已处理 {count} 个 QB/TR 任务")

    def apply_limits(self) -> int:
        """扫描已启用的 qBittorrent 与 Transmission 实例，设置已完成任务的单任务上传上限。"""
        if not self._enabled:
            return 0
        try:
            module_manager = ModuleManager()
            total = 0
            for downloader_type in (DownloaderType.Qbittorrent, DownloaderType.Transmission):
                for module in module_manager.get_running_subtype_module(downloader_type) or []:
                    for name, server in (module.get_instances() or {}).items():
                        torrents = server.get_completed_torrents()
                        if torrents is None:
                            logger.warning(f"QB/TR完成后上传限速：读取下载器 {name} 任务失败")
                            continue
                        for torrent in torrents:
                            if not self._is_complete(torrent):
                                continue
                            torrent_hash = self._value(torrent, "hash", "hashString", "hash_string")
                            if not torrent_hash:
                                continue
                            try:
                                if downloader_type == DownloaderType.Qbittorrent:
                                    server.qbc.torrents_set_upload_limit(
                                        torrent_hashes=torrent_hash,
                                        upload_limit=self._limit_kb * 1024,
                                    )
                                else:
                                    server.change_torrent(
                                        hash_string=torrent_hash,
                                        upload_limit=self._limit_kb,
                                    )
                                total += 1
                                logger.info(
                                    f"QB/TR完成后上传限速：{name} / "
                                    f"{self._value(torrent, 'name') or torrent_hash} "
                                    f"上传上限设为 {self._limit_kb} KB/s"
                                )
                            except Exception as err:
                                logger.warning(f"QB/TR完成后上传限速：设置任务限速失败 {torrent_hash}: {err}")
            return total
        except Exception as err:
            logger.error(f"QB/TR完成后上传限速：检查任务失败：{err}")
            return 0

    @staticmethod
    def _value(obj: Any, *names: str) -> Any:
        """兼容 qB 字典与 Transmission RPC 对象的字段名。"""
        for name in names:
            if isinstance(obj, dict):
                value = obj.get(name)
            else:
                value = getattr(obj, name, None)
            if value is not None:
                return value
        return None

    @staticmethod
    def _is_complete(torrent: Any) -> bool:
        """过滤仍在下载、校验或缺失文件的任务。"""
        try:
            progress = float(QBUploadCap._value(torrent, "progress", "percent_done", "percentDone") or 0)
            amount_left = int(QBUploadCap._value(torrent, "amount_left", "left_until_done", "leftUntilDone") or 0)
        except (TypeError, ValueError):
            return False
        state = str(QBUploadCap._value(torrent, "state", "status") or "").casefold()
        return progress >= 1 and amount_left <= 0 and state not in {
            "checkingdl", "checkingup", "error", "missingfiles", "moving", "unknown",
            "downloading", "download_pending",
        }
