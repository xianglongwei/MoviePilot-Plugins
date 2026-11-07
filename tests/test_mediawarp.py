"""Host-independent regression tests; real YAML/psutil/APScheduler dependencies."""
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ruamel.yaml import YAML


for name in ("app", "app.core", "app.core.config", "app.helper",
             "app.helper.mediaserver", "app.log", "app.plugins"):
    sys.modules[name] = ModuleType(name)
settings = SimpleNamespace(PLUGIN_DATA_PATH=None, TZ="Asia/Shanghai", PROXY=None)
sys.modules["app.core.config"].settings = settings
sys.modules["app.helper.mediaserver"].MediaServerHelper = Mock
sys.modules["app.log"].logger = Mock()
sys.modules["app.plugins"]._PluginBase = type("_PluginBase", (), {})
path = Path(__file__).resolve().parents[1] / "plugins.v2/mediawarp/__init__.py"
spec = importlib.util.spec_from_file_location("mediawarp_test_plugin", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
MediaWarp = module.MediaWarp


class Scheduler:
    def __init__(self, **kwargs):
        self.running = False
        self.jobs = []

    def add_job(self, *args, **kwargs):
        self.jobs.append((args, kwargs))

    def start(self):
        self.running = True

    def remove_all_jobs(self):
        self.jobs.clear()

    def shutdown(self, **kwargs):
        self.running = False


def process():
    child = Mock()
    child.poll.return_value = None
    return child


class MediaWarpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        settings.PLUGIN_DATA_PATH = Path(self.temp.name)
        self.helper = Mock()
        self.names = ["主服务器", "../第二台"]
        self.servers = {
            name: SimpleNamespace(type="emby", config=SimpleNamespace(config={
                "host": f"http://emby-{i}:8096/", "apikey": f"secret-{i}"}))
            for i, name in enumerate(self.names)
        }
        self.helper.get_services.side_effect = lambda name_filters: {
            name: self.servers[name] for name in name_filters if name in self.servers}
        self.helper.get_configs.return_value = {
            n: SimpleNamespace(name=n, type="emby") for n in self.names}
        self.patches = [
            patch.object(module, "MediaServerHelper", return_value=self.helper),
            patch.object(module, "BackgroundScheduler", Scheduler),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.plugin = MediaWarp()
        self.addCleanup(self.plugin.stop_service)
        root = settings.PLUGIN_DATA_PATH / "mediawarp"
        root.mkdir()
        (root / "MediaWarp").write_bytes(b"fake executable")
        (root / "version.txt").write_text("0.1.12")

    def config(self, **extra):
        result = {"enabled": True, "mediaservers": self.names}
        for i, name in enumerate(self.names):
            result[self.plugin._field(name, "port")] = str(9001 + i)
            result[self.plugin._field(name, "media_strm_path")] = f"/media/strm-{i}"
        result.update(extra)
        return result

    def read_config(self, plan):
        return YAML().load(plan["root"] / "config/config.yaml")

    def test_two_servers_have_separate_config_keys_and_directories(self):
        config = self.config()
        config[self.plugin._field(self.names[1], "crx")] = True
        self.plugin.init_plugin(config)
        with patch.object(module.psutil, "Popen", side_effect=[process(), process()]) as popen:
            self.plugin._MediaWarp__run_service()
        plans = list(self.plugin._instances.values())
        self.assertEqual(popen.call_count, 2)
        self.assertNotEqual(plans[0]["root"], plans[1]["root"])
        for i, plan in enumerate(plans):
            generated = self.read_config(plan)
            self.assertEqual(generated["Port"], 9001 + i)
            self.assertEqual(generated["MediaServer"]["AUTH"], f"secret-{i}")
            self.assertEqual(generated["MediaServer"]["ADDR"], f"http://emby-{i}:8096")
            self.assertEqual(generated["HTTPStrm"]["PrefixList"], [f"/media/strm-{i}"])
            self.assertEqual(generated["Web"]["Crx"], i == 1)
            self.assertTrue(generated["Web"]["Enable"])
            self.assertTrue(generated["HTTPStrm"]["TransCode"])
            self.assertFalse(generated["AlistStrm"]["Enable"])
            self.assertEqual(popen.call_args_list[i].args[0][1], "--config")
            self.assertFalse((plan["root"] / "MediaWarp").is_symlink())
        self.assertNotIn("secret-", str(self.plugin.get_page()))

    def test_duplicate_port_rejects_all_before_launch(self):
        config = self.config()
        config[self.plugin._field(self.names[1], "port")] = "9001"
        with patch.object(module.psutil, "Popen") as popen:
            self.plugin.init_plugin(config)
        self.assertFalse(self.plugin._instances)
        self.assertTrue(self.plugin._last_error)
        popen.assert_not_called()

    def test_invalid_ports_and_missing_key(self):
        for bad in ("", 80, 65536, "9001.5", True):
            config = self.config()
            config[self.plugin._field(self.names[0], "port")] = bad
            self.plugin.init_plugin(config)
            self.assertFalse(self.plugin._instances)
        self.servers[self.names[1]].config.config["apikey"] = ""
        self.plugin.init_plugin(self.config())
        self.assertFalse(self.plugin._instances)

    def test_legacy_first_server_and_advanced_config_preserved(self):
        root = settings.PLUGIN_DATA_PATH / "mediawarp/config"
        root.mkdir()
        (root / "config.yaml").write_text(
            "Web:\n  Head: custom-head\nAlistStrm:\n  Enable: true\n  RawURL: true\n",
            encoding="utf-8")
        self.plugin.init_plugin({"enabled": True, "mediaservers": self.names,
                                 "port": "9001", "crx": True,
                                 self.plugin._field(self.names[1], "port"): "9002"})
        with patch.object(module.psutil, "Popen", side_effect=[process(), process()]):
            self.plugin._MediaWarp__run_service()
        first, second = list(self.plugin._instances.values())
        self.assertTrue(self.read_config(first)["Web"]["Crx"])
        self.assertEqual(self.read_config(first)["Web"]["Head"], "custom-head")
        self.assertTrue(self.read_config(first)["AlistStrm"]["RawURL"])
        self.assertFalse(self.read_config(second)["Web"]["Crx"])
        self.assertFalse(self.read_config(second)["HTTPStrm"]["Enable"])

    def test_example_demo_configuration_is_disabled_for_new_servers(self):
        root = settings.PLUGIN_DATA_PATH / "mediawarp/config"
        root.mkdir()
        (root / "config.yaml.example").write_text(
            "Web:\n  Head: demo-script\nAlistStrm:\n  Enable: true\n",
            encoding="utf-8")
        self.plugin.init_plugin(self.config())
        with patch.object(module.psutil, "Popen", side_effect=[process(), process()]):
            self.plugin._MediaWarp__run_service()
        for plan in self.plugin._instances.values():
            self.assertEqual(self.read_config(plan)["Web"]["Head"], "")
            self.assertFalse(self.read_config(plan)["AlistStrm"]["Enable"])

    def test_form_uses_stable_unique_models_and_saved_values(self):
        self.plugin.init_plugin(self.config())
        form, defaults = self.plugin.get_form()
        for i, name in enumerate(self.names):
            key = self.plugin._field(name, "port")
            self.assertEqual(defaults[key], str(9001 + i))
        self.assertNotIn("secret-", str(form))
        self.assertNotIn("../", self.plugin._field("../第二台", "instance"))

    def test_restart_only_failed_server_and_throttle_retries(self):
        self.plugin.init_plugin(self.config())
        children = [process(), process()]
        with patch.object(module.psutil, "Popen", side_effect=children):
            self.plugin._MediaWarp__run_service()
        first, second = list(self.plugin._instances.values())
        children[0].poll.return_value = 0  # Upstream exits 0 even on some errors.
        with patch.object(module.psutil, "Popen", return_value=process()) as spawn:
            self.plugin._monitor()
            spawn.assert_not_called()
            first["last_start"] = module.time.monotonic() - 61
            self.plugin._monitor()
            self.assertEqual(spawn.call_count, 1)
            self.assertIs(second["process"], children[1])

    def test_stop_and_disable_terminate_and_wait_for_all_processes(self):
        self.plugin.init_plugin(self.config())
        children = [process(), process()]
        with patch.object(module.psutil, "Popen", side_effect=children):
            self.plugin._MediaWarp__run_service()
        self.plugin.init_plugin({"enabled": False})
        for child in children:
            child.terminate.assert_called_once()
            child.wait.assert_called_once()
        self.assertFalse(self.plugin._instances)
        self.assertIsNone(self.plugin._scheduler)
        self.plugin._monitor()  # No restart after disabling.

    def test_corrupt_yaml_is_not_overwritten_and_other_server_still_starts(self):
        self.plugin.init_plugin(self.config())
        first = next(iter(self.plugin._instances.values()))
        target = first["root"] / "config/config.yaml"
        target.parent.mkdir(parents=True)
        target.write_text("Web: [unclosed", encoding="utf-8")
        with patch.object(module.psutil, "Popen", return_value=process()) as spawn:
            self.plugin._MediaWarp__run_service()
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual(target.read_text(), "Web: [unclosed")
        self.assertIsNone(first["process"])

    def test_reload_stops_old_children_before_replacing_instances(self):
        self.plugin.init_plugin(self.config())
        children = [process(), process()]
        with patch.object(module.psutil, "Popen", side_effect=children):
            self.plugin._MediaWarp__run_service()
        self.plugin.init_plugin(self.config())
        for child in children:
            child.terminate.assert_called_once()
        self.assertEqual(len(self.plugin._instances), 2)
        self.assertTrue(all(p["process"] is None for p in self.plugin._instances.values()))

    def test_stop_waits_for_real_child(self):
        # Actual OS process: exercise psutil wait/kill, not just mock assertions.
        child = module.psutil.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.plugin._instances = {"test": {"process": child}}
        self.plugin.stop_service()
        self.assertIsNotNone(child.poll())

    def test_archive_paths_cannot_write_outside_expected_files(self):
        memory = io.BytesIO()
        with tarfile.open(fileobj=memory, mode="w:gz") as archive:
            for name, data in (("../../escape", b"unsafe"), ("../../MediaWarp", b"binary"),
                               ("config/config.yaml.example", b"Port: 9000")):
                item = tarfile.TarInfo(name)
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.return_value = [memory.getvalue()]
        with patch.object(module.platform, "system", return_value="Linux"), \
                patch.object(module.platform, "machine", return_value="x86_64"), \
                patch.object(module.requests, "get", return_value=response):
            self.plugin._MediaWarp__download_and_extract()
        root = settings.PLUGIN_DATA_PATH / "mediawarp"
        self.assertEqual((root / "MediaWarp").read_bytes(), b"binary")
        self.assertFalse((settings.PLUGIN_DATA_PATH / "escape").exists())
        self.assertEqual((root / "version.txt").read_text(), "0.1.12")


if __name__ == "__main__":
    unittest.main()
