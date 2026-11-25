"""Regression coverage for completed torrent upload limits."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from enum import Enum
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[3]


class DownloaderType(Enum):
    Qbittorrent = "qbittorrent"
    Transmission = "transmission"


def load_plugin():
    names = (
        "apscheduler", "apscheduler.triggers", "apscheduler.triggers.interval",
        "app", "app.schemas", "app.schemas.types", "app.core", "app.core.module",
        "app.sdk", "app.sdk.plugin", "app.sdk.logging",
    )
    modules = {name: types.ModuleType(name) for name in names}
    for name in ("apscheduler", "apscheduler.triggers", "app", "app.core", "app.sdk"):
        modules[name].__path__ = []
    modules["apscheduler.triggers.interval"].IntervalTrigger = lambda **kwargs: kwargs
    modules["app.schemas.types"].DownloaderType = DownloaderType
    modules["app.schemas"].Response = type("Response", (), {})
    modules["app.core.module"].ModuleManager = object
    modules["app.sdk.plugin"]._PluginBase = object
    modules["app.sdk.logging"].logger = Mock()
    spec = importlib.util.spec_from_file_location(
        "qbuploadcap_regression", ROOT / "plugins.v3/qbuploadcap/__init__.py",
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


plugin_module = load_plugin()


class FakeServer:
    def __init__(self, torrent_hash):
        self.torrents = [{
            "hash": torrent_hash,
            "name": torrent_hash,
            "progress": 1.0,
            "amount_left": 0,
            "state": "uploading",
        }]
        self.change_torrent = Mock(return_value=True)

    def get_completed_torrents(self):
        return self.torrents


class FakeModule:
    def __init__(self, name, server):
        self.name = name
        self.server = server

    def get_instances(self):
        return {self.name: self.server}


class UploadLimitTest(unittest.TestCase):
    def test_completed_qbittorrent_and_transmission_tasks_use_host_adapter(self):
        qb = FakeServer("qb-hash")
        transmission = FakeServer("tr-hash")
        by_type = {
            DownloaderType.Qbittorrent: [FakeModule("QB", qb)],
            DownloaderType.Transmission: [FakeModule("TR", transmission)],
        }
        manager = Mock()
        manager.get_running_subtype_module.side_effect = lambda kind: by_type[kind]
        plugin = plugin_module.QBUploadCap()
        plugin._enabled = True
        plugin._limit_kb = 100

        with patch.object(plugin_module, "ModuleManager", return_value=manager):
            changed = plugin.apply_limits()

        self.assertEqual(changed, 2)
        qb.change_torrent.assert_called_once_with(hash_string="qb-hash", upload_limit=100)
        transmission.change_torrent.assert_called_once_with(
            hash_string="tr-hash", upload_limit=100,
        )


if __name__ == "__main__":
    unittest.main()
