# Derived from DDSRem-Dev/MoviePilot-Plugins MediaWarp 1.0.8.
# Multi-server support maintained by xianglongwei.
import hashlib
import os
import platform
import shutil
import socket
import subprocess
import tarfile
import tempfile
import threading
import time
from collections.abc import MutableMapping
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any, Dict, Generator, List, Tuple
from urllib.parse import urlsplit

import psutil
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from ruamel.yaml import YAML

from app.core.config import settings
from app.helper.mediaserver import MediaServerHelper
from app.log import logger
from app.plugins import _PluginBase


class MediaWarp(_PluginBase):
    """每台 Emby/Jellyfin 启动独立的 MediaWarp 反向代理。"""

    plugin_name = "MediaWarp 多服务器"
    plugin_desc = "同时反代多台 Emby/Jellyfin，独立端口、STRM 直链、美化与进程守护。"
    plugin_icon = "https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/refs/heads/main/icons/cloud.png"
    plugin_version = "1.1.0"
    plugin_author = "DDSRem, xianglongwei"
    author_url = "https://github.com/xianglongwei/MoviePilot-Plugins"
    plugin_config_prefix = "mediawarp_"
    plugin_order = 15
    auth_level = 1

    FEATURES = {
        "crx": ("CRX美化", "Web.Crx"),
        "actor_plus": ("头像过滤", "Web.ActorPlus"),
        "fanart_show": ("显示同人图", "Web.FanartShow"),
        "external_player_url": ("外置播放器", "Web.ExternalPlayerUrl"),
        "danmaku": ("Web弹幕", "Web.Danmaku"),
        "video_together": ("共同观影", "Web.VideoTogether"),
        "srt2ass": ("SRT转ASS", "Subtitle.SRT2ASS"),
    }

    def __init__(self):
        super().__init__()
        root = settings.PLUGIN_DATA_PATH / "mediawarp"
        self.__mediawarp_path = root / "MediaWarp"
        self.__config_path = root / "config"
        self.__mediawarp_version = "0.1.12"
        self.__mediawarp_version_path = root / "version.txt"
        self._scheduler = None
        self._lock = threading.RLock()
        self._config = {}
        self._enabled = False
        self._mediaservers = []
        self._mediaserver_helper = MediaServerHelper()
        self._instances = {}
        self._last_error = ""
        self._stopping = True

    @staticmethod
    def _field(name, option):
        # Stable, safe UI models and directory names, even for Chinese server names.
        key = hashlib.sha256(name.encode("utf-8")).hexdigest()[:20]
        return f"server_{key}_{option}"

    def _value(self, name, option, fallback=None):
        key = self._field(name, option)
        if key in self._config:
            return self._config[key]
        # Legacy globals apply to the first selected server only.
        if self._mediaservers and name == self._mediaservers[0]:
            return self._config.get(option, fallback)
        return fallback

    def init_plugin(self, config: dict = None):
        self.stop_service()
        self._config = dict(config or {})
        self._enabled = bool(self._config.get("enabled", False))
        selected = self._config.get("mediaservers") or []
        self._mediaservers = list(dict.fromkeys(selected)) if isinstance(selected, list) else []
        self._mediaserver_helper = MediaServerHelper()
        self._last_error = ""
        if not self._enabled:
            return
        try:
            plans = self._build_plans()
        except (ValueError, TypeError):
            # The reason contains only our own validation messages, never API keys.
            self._last_error = "配置无效：请检查已选服务器、端口、服务器地址和 API Key；各服务器端口不能重复。"
            logger.error(self._last_error)
            return
        with self._lock:
            self._stopping = False
            self._instances = {plan["name"]: plan for plan in plans}
        self._scheduler = BackgroundScheduler(timezone=settings.TZ)
        self._scheduler.add_job(self.__run_service, "date", name="MediaWarp多服务器启动")
        self._scheduler.add_job(self._monitor, "interval", seconds=15,
                                max_instances=1, coalesce=True, name="MediaWarp进程守护")
        self._scheduler.start()

    def _build_plans(self):
        if not self._mediaservers or any(not isinstance(n, str) for n in self._mediaservers):
            raise ValueError("请选择媒体服务器")
        plans, ports = [], set()
        for index, name in enumerate(self._mediaservers):
            services = self._mediaserver_helper.get_services(name_filters=[name])
            if len(services) != 1:
                raise ValueError("媒体服务器不存在、未启用或名称不唯一")
            server = next(iter(services.values()))
            if server.type not in ("emby", "jellyfin"):
                raise ValueError("仅支持 Emby/Jellyfin")
            raw_port = self._value(name, "port", "")
            if isinstance(raw_port, bool) or not str(raw_port).isascii() or not str(raw_port).isdigit():
                raise ValueError("端口必须为整数")
            port = int(raw_port)
            if not 1024 <= port <= 65535 or port in ports:
                raise ValueError("端口必须在1024–65535之间且互不重复")
            ports.add(port)
            connection = server.config.config
            host = str(connection.get("host") or "").strip().rstrip("/")
            if "://" not in host:
                host = "http://" + host
            parsed = urlsplit(host)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("媒体服务器地址无效")
            parsed.port
            apikey = connection.get("apikey")
            if not isinstance(apikey, str) or not apikey.strip():
                raise ValueError("媒体服务器缺少 API Key")
            paths = self._value(name, "media_strm_path", "") or ""
            if not isinstance(paths, str):
                raise ValueError("STRM路径必须为文本")
            root = self.__mediawarp_path.parent / "servers" / self._field(name, "instance")
            changes = {
                "Port": port,
                "MediaServer.Type": "Jellyfin" if server.type == "jellyfin" else "Emby",
                "MediaServer.ADDR": host,
                "MediaServer.AUTH": apikey,
                "Web.Enable": True,
                "Logger.AccessLogger.File": True,
                "Logger.AccessLogger.Console": False,
                "Logger.ServiceLogger.File": True,
                "Logger.ServiceLogger.Console": False,
                "HTTPStrm.Enable": bool(paths.strip()),
                "HTTPStrm.FinalURL": True,
                "HTTPStrm.TransCode": bool(self._value(name, "transcode", True)),
                "HTTPStrm.PrefixList": [p.strip() for p in paths.splitlines() if p.strip()],
            }
            for option, (_, field) in self.FEATURES.items():
                changes[field] = bool(self._value(name, option, False))
            if changes["Subtitle.SRT2ASS"]:
                changes["Subtitle.Enable"] = True
            plans.append(dict(name=name, port=port, root=root, changes=changes,
                              legacy=index == 0 and (self.__config_path / "config.yaml").is_file(),
                              process=None, last_start=0.0,
                              ready=False, error="等待启动"))
        return plans

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        self._mediaserver_helper = MediaServerHelper()
        configs = [c for c in self._mediaserver_helper.get_configs().values()
                   if c.type in ("emby", "jellyfin")]
        data = {"enabled": False, "mediaservers": []}

        def field(component, model, label, **props):
            return {"component": component, "props": dict(model=model, label=label, **props)}

        def row(fields):
            return {"component": "VRow", "content": [
                {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [f]}
                for f in fields]}

        content = [
            row([field("VSwitch", "enabled", "启用插件"),
                 field("VSelect", "mediaservers", "启用的媒体服务器",
                       multiple=True, chips=True, clearable=True,
                       items=[{"title": c.name, "value": c.name} for c in configs])]),
            {"component": "VAlert", "props": {"type": "info", "variant": "tonal"},
             "text": "先在 MoviePilot 设置中添加 Emby/Jellyfin 并填写 API Key，再勾选需要反代的服务器。下方每台服务器需填写独立端口。bridge 网络需逐个映射端口；host 网络无需映射。"},
        ]
        for config in configs:
            name = config.name
            port_model = self._field(name, "port")
            path_model = self._field(name, "media_strm_path")
            data[port_model] = self._value(name, "port", "")
            data[path_model] = self._value(name, "media_strm_path", "")
            switches = []
            for option, (label, _) in self.FEATURES.items():
                model = self._field(name, option)
                data[model] = self._value(name, option, False)
                switches.append(field("VSwitch", model, label))
            transcode = self._field(name, "transcode")
            data[transcode] = self._value(name, "transcode", True)
            switches.append(field("VSwitch", transcode, "保留原服务器转码设置"))
            sections = [
                {"component": "VCardTitle", "text": name},
                {"component": "VCardText", "content": [
                    row([field("VTextField", port_model, "反代端口", type="number",
                               hint="必填，1024–65535；各服务器不可重复", **{"persistent-hint": True})]),
                    field("VTextarea", path_model, "Emby/Jellyfin 中的 STRM 媒体库路径",
                          rows=3, placeholder="一行一个，例如 /media/strm",
                          hint="填写服务器中看到的路径，留空关闭 HTTP STRM 优化",
                          **{"persistent-hint": True}),
                    *[row(switches[i:i + 3]) for i in range(0, len(switches), 3)],
                ]},
            ]
            content.append({"component": "VCard",
                            "props": {"variant": "outlined", "class": "mt-3"}, "content": sections})
        content.append({"component": "VAlert", "props": {"type": "info", "variant": "tonal", "class": "mt-3"},
                        "text": "每台服务器独立配置、静态文件和日志保存在插件数据目录 mediawarp/servers/ 下；可在详情页查看目录和运行状态。保存后重启所有代理，异常退出每60秒重试。保留转码设置默认开启，避免影响原有客户端播放。"})
        return [{"component": "VForm", "content": content}], data

    def get_page(self) -> List[dict]:
        rows = []
        with self._lock:
            for plan in self._instances.values():
                process = plan["process"]
                running = process is not None and process.poll() is None and plan["ready"]
                rows.append({"component": "VCard", "props": {"variant": "outlined", "class": "mb-2"},
                             "content": [
                                 {"component": "VCardTitle", "text": plan["name"]},
                                 {"component": "VCardText", "text": f"端口：{plan['port']} ｜ 状态：{'运行中' if running else plan['error']} ｜ 目录：{plan['root']}"},
                             ]})
            if self._last_error:
                rows.insert(0, {"component": "VAlert", "props": {"type": "error"}, "text": self._last_error})
        if not rows:
            rows.append({"component": "VAlert", "props": {"type": "info"}, "text": "插件未启用或尚未选择媒体服务器。"})
        return rows

    def __run_service(self):
        with self._lock:
            if self._stopping:
                return
            try:
                self._ensure_binary()
            except Exception:
                self._last_error = "MediaWarp 下载或安装失败，请检查网络、架构、权限及磁盘空间。"
                logger.error(self._last_error)
                return
            self._last_error = ""
            for plan in self._instances.values():
                self._start_instance(plan)

    def _ensure_binary(self):
        version = self.__mediawarp_version_path
        if (not self.__mediawarp_path.exists() or not version.exists()
                or version.read_text(encoding="utf-8").strip() != self.__mediawarp_version):
            self.__download_and_extract()

    def _start_instance(self, plan):
        plan["last_start"] = time.monotonic()
        plan["ready"] = False
        plan["error"] = "启动中"
        try:
            root = plan["root"]
            root.mkdir(parents=True, exist_ok=True)
            config_dir = root / "config"
            config_dir.mkdir(exist_ok=True)
            target = config_dir / "config.yaml"
            new_config = not target.exists()
            # Copy the legacy advanced config once, for the first selected server.
            if not target.exists():
                template = self.__config_path / "config.yaml"
                if not plan["legacy"]:
                    template = self.__config_path / "config.yaml.example"
                if template.exists():
                    self.__atomic_copy(template, target)
                static = self.__config_path / "static"
                if plan["legacy"] and static.is_dir() and not (root / "static").exists():
                    shutil.copytree(static, root / "static")
            executable = root / "MediaWarp"
            # Do not symlink: upstream resolves os.Executable() for config/log/static.
            self.__atomic_copy(self.__mediawarp_path, executable)
            changes = dict(plan["changes"])
            if new_config and not plan["legacy"]:
                # Upstream example includes demo Alist credentials and injected
                # scripts. Do not activate those for newly configured servers.
                changes.update({"AlistStrm.Enable": False, "ClientFilter.Enable": False,
                                "Web.Head": "", "Subtitle.Enable": False})
                if changes["Subtitle.SRT2ASS"]:
                    changes["Subtitle.Enable"] = True
            changes["Web.Index"] = (root / "static" / "index.html").is_file()
            self.__modify_config(target, changes)
            target.chmod(0o600)
            # Preserve a small previous startup log, truncate the new one.
            console = root / "console.log"
            if console.exists():
                console.replace(root / "console.previous.log")
            with console.open("wb") as output:
                plan["process"] = psutil.Popen(
                    [str(executable), "--config", str(target)], cwd=str(root),
                    stdout=output, stderr=subprocess.STDOUT)
            logger.info(f"MediaWarp [{plan['name']}] 启动，监听端口 {plan['port']}")
        except Exception:
            plan["error"] = "启动失败，请检查实例配置、权限或端口"
            logger.error(f"MediaWarp [{plan['name']}] {plan['error']}")

    def _monitor(self):
        with self._lock:
            if self._stopping:
                return
            if not self.__mediawarp_path.exists():
                return
            for plan in self._instances.values():
                process = plan["process"]
                if process is None or process.poll() is not None:
                    plan["ready"] = False
                    plan["error"] = "进程已退出，等待重试；请检查 console.log"
                    if time.monotonic() - plan["last_start"] >= 60:
                        self._start_instance(plan)
                else:
                    try:
                        with socket.create_connection(("127.0.0.1", plan["port"]), timeout=1):
                            plan["ready"] = True
                    except OSError:
                        plan["ready"] = False
                        plan["error"] = "端口尚未就绪，请检查 console.log"

    @staticmethod
    @contextmanager
    def __atomic_write(
        target: Path, binary: bool = False
    ) -> Generator[IO[Any], None, None]:
        """
        写入同目录临时文件，落盘成功后原子替换目标文件

        :param target (Path): 目标文件路径
        :param binary (bool): 是否以二进制模式写入

        :yields IO: 临时文件对象
        """
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb" if binary else "w",
            encoding=None if binary else "utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temp_path = Path(file.name)
            try:
                if target.exists():
                    shutil.copymode(target, temp_path)
                yield file
                file.flush()
                os.fsync(file.fileno())
                file.close()
                os.replace(temp_path, target)
            finally:
                temp_path.unlink(missing_ok=True)

    def __atomic_copy(self, source: Path, target: Path) -> None:
        """
        原子复制文件并保留源文件的权限和时间戳
        """
        with (
            source.open("rb") as source_file,
            self.__atomic_write(target, binary=True) as target_file,
        ):
            shutil.copyfileobj(source_file, target_file)
            target_file.flush()
            shutil.copystat(source, target_file.name)

    def __modify_config(self, config_path: Path, modifications: Dict[str, Any]) -> None:
        """
        修改配置文件

        空文件或缺失文件按插件设置重建，解析或结构错误保留原文件并报错

        :param config_path (Path): 配置文件路径
        :param modifications (Dict): 要修改的配置项字典
        """
        yaml = YAML()
        yaml.preserve_quotes = True
        yaml.indent(mapping=2, sequence=4, offset=2)

        config = None
        if config_path.exists():
            with config_path.open("r", encoding="utf-8") as file:
                config = yaml.load(file)
        if config is None:
            logger.warning("MediaWarp 配置文件为空或不存在，将按插件设置重建")
            config = {}
        if not isinstance(config, MutableMapping):
            raise TypeError("MediaWarp 配置文件根节点必须是映射")

        for key, value in modifications.items():
            keys = key.split(".")
            current = config
            for k in keys[:-1]:
                if current.get(k) is None:
                    current[k] = {}
                current = current[k]
                if not isinstance(current, MutableMapping):
                    raise TypeError(f"MediaWarp 配置项 {key} 的父节点必须是映射")
            current[keys[-1]] = value

        with self.__atomic_write(config_path) as file:
            yaml.dump(config, file)

    def __download_and_extract(self):
        machine = platform.machine().lower()
        architectures = {"x86_64": "amd64", "amd64": "amd64",
                         "aarch64": "arm64", "arm64": "arm64"}
        if platform.system().lower() != "linux" or machine not in architectures:
            raise RuntimeError("仅支持 Linux amd64/arm64 MoviePilot 容器")
        version = self.__mediawarp_version
        url = (f"https://github.com/DDSRem-Dev/MediaWarp/releases/download/v{version}/"
               f"MediaWarp_{version}_linux_{architectures[machine]}.tar.gz")
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "MediaWarp.tar.gz"
            with requests.get(url, stream=True, proxies=settings.PROXY,
                              timeout=(15, 120)) as response:
                response.raise_for_status()
                with archive.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=65536):
                        output.write(chunk)
            self.__config_path.mkdir(parents=True, exist_ok=True)
            with tarfile.open(archive, "r:gz") as tar:
                # Read only regular files; never extract paths or symbolic links.
                binaries = [m for m in tar.getmembers()
                            if Path(m.name).name == "MediaWarp" and m.isfile()]
                if len(binaries) != 1:
                    raise ValueError("压缩包缺少唯一的 MediaWarp 可执行文件")
                binary = Path(temp) / "MediaWarp"
                with tar.extractfile(binaries[0]) as source, binary.open("wb") as output:
                    shutil.copyfileobj(source, output)
                binary.chmod(0o755)
                examples = [m for m in tar.getmembers()
                            if Path(m.name).name == "config.yaml.example" and m.isfile()]
                if examples:
                    example = self.__config_path / "config.yaml.example"
                    with tar.extractfile(examples[0]) as source, self.__atomic_write(example, binary=True) as output:
                        shutil.copyfileobj(source, output)
                    legacy = self.__config_path / "config.yaml"
                    if not legacy.exists():
                        self.__atomic_copy(example, legacy)
                self.__atomic_copy(binary, self.__mediawarp_path)
            with self.__atomic_write(self.__mediawarp_version_path) as output:
                output.write(version)

    def stop_service(self):
        # Wait for in-flight scheduler jobs before mutating process ownership.
        self._stopping = True
        scheduler, self._scheduler = self._scheduler, None
        if scheduler:
            scheduler.remove_all_jobs()
            if scheduler.running:
                scheduler.shutdown(wait=True)
        with self._lock:
            processes = [p["process"] for p in self._instances.values()
                         if p["process"] is not None]
            for process in processes:
                try:
                    if process.poll() is None:
                        process.terminate()
                except psutil.NoSuchProcess:
                    pass
            deadline = time.monotonic() + 10
            for process in processes:
                try:
                    process.wait(timeout=max(0.01, deadline - time.monotonic()))
                except (psutil.TimeoutExpired, subprocess.TimeoutExpired):
                    process.kill()
                    process.wait(timeout=3)
                except psutil.NoSuchProcess:
                    pass
            self._instances = {}
