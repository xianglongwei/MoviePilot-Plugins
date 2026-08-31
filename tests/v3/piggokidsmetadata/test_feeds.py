from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


PLUGIN_DIR = Path(__file__).resolve().parents[3] / "plugins.v3" / "piggokidsmetadata"
PACKAGE_NAME = "piggokidsmetadata_feed_tests"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader
    spec.loader.exec_module(module)
    return module


package_spec = importlib.util.spec_from_loader(PACKAGE_NAME, loader=None, is_package=True)
package = importlib.util.module_from_spec(package_spec)
package.__path__ = [str(PLUGIN_DIR)]
sys.modules[PACKAGE_NAME] = package
core = _load_module(f"{PACKAGE_NAME}.core", PLUGIN_DIR / "core.py")
feeds = _load_module(f"{PACKAGE_NAME}.feeds", PLUGIN_DIR / "feeds.py")


class FeedParsingTest(unittest.TestCase):
    def test_public_url_validation_blocks_local_and_private_targets(self):
        public = lambda *_args, **_kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 443)),
        ]
        private = lambda *_args, **_kwargs: [
            (2, 1, 6, "", ("192.168.1.20", 443)),
        ]
        self.assertEqual(
            feeds.validate_public_http_url("https://piggo.example/rss", resolver=public),
            "https://piggo.example/rss",
        )
        for url in ("http://127.0.0.1/rss", "http://localhost/rss"):
            with self.assertRaises(feeds.InvalidReferenceError):
                feeds.validate_public_http_url(url, resolver=public)
        with self.assertRaises(feeds.InvalidReferenceError):
            feeds.validate_public_http_url("https://piggo.example/rss", resolver=private)

    def test_rss_is_parsed_without_persisting_private_reference(self):
        xml = b"""<?xml version="1.0"?>
        <rss><channel><item>
          <title>Kids Show S01E01</title>
          <guid>https://piggo.example/details.php?id=123&amp;passkey=very-secret-value-123456</guid>
          <link>https://piggo.example/details.php?id=123&amp;passkey=very-secret-value-123456</link>
          <enclosure url="https://piggo.example/download.php?id=123&amp;passkey=very-secret-value-123456" length="1024"/>
          <pubDate>Tue, 01 Jul 2025 10:00:00 GMT</pubDate>
          <category>TV</category>
        </item></channel></rss>"""
        parsed = feeds.parse_feed_document(xml, source_feed_id="feed:test")
        self.assertEqual(len(parsed), 1)
        item = parsed[0]
        self.assertEqual(item.candidate.site_item_id, "123")
        self.assertEqual(item.candidate.media_type, core.MediaKind.TV)
        self.assertEqual(item.candidate.size_bytes, 1024)
        self.assertIn("passkey=***", item.candidate.download_url)
        self.assertNotIn("very-secret", str(item.candidate.to_dict()))
        self.assertIn("very-secret", item.download_reference)

    def test_atom_enclosure_is_supported(self):
        xml = """<feed xmlns="http://www.w3.org/2005/Atom">
          <entry><title>儿童电影 Movie</title><id>tag:example,2025:abc</id>
          <link rel="alternate" href="https://example.test/details/88" />
          <link rel="enclosure" href="https://example.test/download/88?token=abcdefghijklmnopqrstuvwxyz123456" length="2048" />
          <updated>2025-07-01T10:00:00Z</updated></entry></feed>"""
        item = feeds.parse_feed_document(xml, source_feed_id="feed:atom")[0]
        self.assertEqual(item.candidate.site_item_id, "88")
        self.assertEqual(item.candidate.media_type, core.MediaKind.MOVIE)
        self.assertEqual(item.candidate.published_at, "2025-07-01T10:00:00+00:00")

    def test_doctype_and_oversize_are_rejected(self):
        with self.assertRaises(feeds.FeedParseError):
            feeds.parse_feed_document("<!DOCTYPE rss><rss/>", source_feed_id="feed:test")
        with self.assertRaises(feeds.FeedParseError):
            feeds.parse_feed_document(b"<rss>" + b"x" * 2_000 + b"</rss>", source_feed_id="feed:test", max_bytes=1_024)

    def test_duplicate_candidates_preserve_workflow_state(self):
        first = feeds.candidate_from_reference(
            "https://example.test/download.php?id=7&passkey=abcdefghijklmnopqrstuvwxyz123456",
            title="Example",
        ).candidate
        first.status = feeds.CandidateStatus.DOWNLOADING
        first.task_id = "task-1"
        updated = feeds.candidate_from_reference(
            "https://example.test/download.php?id=7&passkey=different-secret-1234567890",
            title="Example Updated",
        ).candidate
        merged = feeds.upsert_candidates([first], [updated])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].status, feeds.CandidateStatus.DOWNLOADING)
        self.assertEqual(merged[0].task_id, "task-1")
        self.assertEqual(merged[0].title, "Example Updated")

    def test_pasted_magnet_is_validated_and_redacted(self):
        raw = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=Kids%20Movie&token=abcdefghijklmnopqrstuvwxyz123456"
        parsed = feeds.candidate_from_reference(raw)
        self.assertEqual(parsed.candidate.title, "Kids Movie")
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", parsed.candidate.download_url)
        with self.assertRaises(feeds.InvalidReferenceError):
            feeds.candidate_from_reference("magnet:?dn=missing-hash")

    def test_feed_url_config_deduplicates_and_rejects_userinfo(self):
        urls = feeds.parse_feed_urls_config("https://example.test/rss?passkey=abc\nhttps://example.test/rss?passkey=abc")
        self.assertEqual(urls, ["https://example.test/rss?passkey=abc"])
        with self.assertRaises(feeds.InvalidReferenceError):
            feeds.parse_feed_urls_config("https://user:password@example.test/rss")


class TaskRestoreTest(unittest.TestCase):
    def test_task_round_trip_restores_enum_and_tracking_fields(self):
        task = core.ImportTask(
            task_id="abc",
            candidate_id="candidate:1",
            downloader="qb",
            download_id="download-1",
            download_hash="a" * 40,
            torrent_files=["Show/S01E01.mkv"],
        )
        task.transition(core.TaskState.SELECTED, "selected")
        restored = core.ImportTask.from_dict(task.to_dict())
        self.assertEqual(restored.state, core.TaskState.SELECTED)
        self.assertEqual(restored.candidate_id, "candidate:1")
        self.assertEqual(restored.torrent_files, ["Show/S01E01.mkv"])


if __name__ == "__main__":
    unittest.main()
