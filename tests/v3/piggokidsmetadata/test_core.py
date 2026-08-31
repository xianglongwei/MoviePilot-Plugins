"""PigGoKidsMetadata 第一阶段纯领域核心测试。"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CORE_PATH = REPOSITORY_ROOT / "plugins.v3" / "piggokidsmetadata" / "core.py"
SPEC = importlib.util.spec_from_file_location("piggokids_core_v3", CORE_PATH)
assert SPEC and SPEC.loader
core = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = core
SPEC.loader.exec_module(core)


class RedactionAndIdentityTest(unittest.TestCase):
    def test_redacts_query_userinfo_and_path_tokens(self) -> None:
        raw = (
            "https://user:password@piggo.example/download/"
            "0123456789abcdef0123456789abcdef/file.torrent?passkey=secret&id=42"
        )
        redacted = core.redact_url(raw)
        self.assertNotIn("password", redacted)
        self.assertNotIn("secret", redacted)
        self.assertNotIn("0123456789abcdef0123456789abcdef", redacted)
        self.assertIn("id=42", redacted)
        self.assertGreaterEqual(redacted.count(core.REDACTED), 3)

    def test_redacts_urls_embedded_in_log_text(self) -> None:
        text = "抓取失败 https://piggo.example/rss?token=private-value&category=kids"
        redacted = core.redact_text(text)
        self.assertNotIn("private-value", redacted)
        self.assertIn("category=kids", redacted)

    def test_stable_identity_distinguishes_media_kind(self) -> None:
        movie = core.build_media_id(
            kind=core.MediaKind.MOVIE,
            title="帮帮龙出动",
            year="2015",
            site_item_id="30876",
        )
        series = core.build_media_id(
            kind=core.MediaKind.TV,
            title="帮帮龙出动",
            year="2015",
            site_item_id="30876",
        )
        self.assertEqual(movie, "piggo:movie:item:30876")
        self.assertEqual(series, "piggo:tv:item:30876")
        self.assertNotEqual(movie, series)

    def test_invalid_site_id_falls_back_without_leaking_secret(self) -> None:
        media_id = core.build_media_id(
            kind=core.MediaKind.TV,
            title="测试节目",
            site_item_id="passkey=do-not-store",
            content_fingerprint="abc",
        )
        self.assertTrue(media_id.startswith("local:tv:"))
        self.assertNotIn("do-not-store", media_id)


class TaskStateTest(unittest.TestCase):
    def test_state_transitions_are_idempotent_and_audited(self) -> None:
        task = core.ImportTask(task_id="task-1")
        task.transition(core.TaskState.SELECTED, "manual")
        task.transition(core.TaskState.SELECTED, "duplicate")
        task.transition(core.TaskState.DOWNLOAD_SUBMITTED)
        self.assertEqual(task.state, core.TaskState.DOWNLOAD_SUBMITTED)
        self.assertEqual(len(task.history), 2)

    def test_invalid_transition_is_rejected(self) -> None:
        task = core.ImportTask(task_id="task-2")
        with self.assertRaises(core.InvalidTaskTransition):
            task.transition(core.TaskState.COMPLETED)


class PublicMediaMatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.item = core.LocalMediaItem(
            media_source=core.MEDIA_SOURCE,
            media_id="piggo:movie:item:42",
            media_type=core.MediaKind.MOVIE,
            title="儿童电影",
            original_title="Kids Movie",
            year="2024",
            aliases=["儿童大电影"],
        )

    def test_exact_alias_type_and_year_can_reuse_public_identity(self) -> None:
        result = core.evaluate_public_media_match(self.item, {
            "media_source": "themoviedb",
            "media_id": "12345",
            "type": "电影",
            "title": "儿童大电影",
            "original_title": "Kids Movie Remastered",
            "year": "2024",
        })
        self.assertTrue(result["exact"])
        self.assertEqual(result["media_id"], "12345")
        self.assertGreaterEqual(result["confidence"], 0.75)

    def test_year_conflict_rejects_otherwise_matching_candidate(self) -> None:
        result = core.evaluate_public_media_match(self.item, {
            "media_source": "themoviedb",
            "media_id": "54321",
            "type": "电影",
            "title": "儿童电影",
            "year": "2014",
        })
        self.assertFalse(result["exact"])
        self.assertIn("year_conflict", result["reasons"])

    def test_v2_source_and_tmdb_id_are_normalized(self) -> None:
        candidate = {
            "source": "themoviedb",
            "tmdb_id": 24680,
            "title": self.item.title,
            "type": "电影",
            "year": self.item.year,
        }
        result = core.evaluate_public_media_match(self.item, candidate)
        self.assertTrue(result["exact"])
        self.assertEqual(result["media_id"], "24680")

    def test_contribution_draft_keeps_only_relative_evidence(self) -> None:
        draft = core.build_contribution_draft(
            core.ImportTask(task_id="draft-task", site_item_id="123"),
            {
                "item": self.item.to_dict(),
                "confidence": 0.91,
                "nfo_documents": [
                    {"path": "Movie/movie.nfo"},
                    {"path": "/private/downloads/movie.nfo"},
                ],
                "conflicts": [{
                    "code": "sample",
                    "message": "需要复核",
                    "evidence": ["Movie/movie.nfo", "../../secret"],
                }],
            },
        )
        self.assertIsNotNone(draft)
        serialized = str(draft)
        self.assertIn("Movie/movie.nfo", serialized)
        self.assertNotIn("/private/downloads", serialized)
        self.assertNotIn("../../secret", serialized)
        self.assertEqual(draft["submission"], "manual_only")


class PayloadFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, relative: str, content: bytes | str = b"") -> Path:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            target.write_text(content, encoding="utf-8")
        else:
            target.write_bytes(content)
        return target


class ScannerSecurityTest(PayloadFixture):
    def test_scanner_classifies_files_and_excludes_samples(self) -> None:
        self.write("Movie/Movie.mkv")
        self.write("Movie/sample.mp4")
        self.write("Movie/movie.nfo", "<movie><title>测试电影</title></movie>")
        self.write("Movie/poster.jpg")
        self.write("Movie/fanart.webp")
        self.write("Movie/Movie.zh-CN.ass")
        self.write("Movie/readme.txt")

        payload = core.scan_downloaded_payload(self.root, "Movie")
        self.assertEqual(payload.media_files, ["Movie.mkv"])
        self.assertEqual(payload.nfo_files, ["movie.nfo"])
        self.assertEqual(payload.artwork_files, ["fanart.webp", "poster.jpg"])
        self.assertEqual(payload.subtitle_files, ["Movie.zh-CN.ass"])
        self.assertIn("sample.mp4", payload.ignored_files)
        self.assertIn("readme.txt", payload.ignored_files)

    def test_absolute_and_parent_paths_are_rejected(self) -> None:
        self.write("Owned/video.mkv")
        with self.assertRaises(core.UnsafePathError):
            core.inspect_downloaded_payload(self.root, self.root / "Owned")
        with self.assertRaises(core.UnsafePathError):
            core.inspect_downloaded_payload(self.root, "../outside")

    def test_symlinks_are_not_followed(self) -> None:
        self.write("Owned/video.mkv")
        outside = self.write("Outside/secret.nfo", "<movie><title>秘密</title></movie>")
        link = self.root / "Owned" / "linked.nfo"
        try:
            link.symlink_to(outside)
        except (NotImplementedError, OSError):
            self.skipTest("当前文件系统不支持符号链接")
        payload = core.scan_downloaded_payload(self.root, "Owned")
        self.assertNotIn("linked.nfo", payload.nfo_files)
        self.assertIn("linked.nfo", payload.skipped_symlinks)

    def test_scan_limit_is_enforced(self) -> None:
        self.write("Owned/one.mkv")
        self.write("Owned/two.mkv")
        with self.assertRaises(core.ScanLimitError):
            core.scan_downloaded_payload(
                self.root,
                "Owned",
                core.ScanPolicy(max_files=1),
            )


class NfoParsingTest(PayloadFixture):
    def test_parses_common_movie_fields(self) -> None:
        nfo = self.write(
            "Movie/movie.nfo",
            """<?xml version="1.0" encoding="UTF-8"?>
            <movie>
              <title>神奇校车</title><originaltitle>The Magic School Bus</originaltitle>
              <year>1994</year><plot>跟随卷毛老师探索科学。</plot>
              <genre>动画</genre><genre>儿童</genre>
              <uniqueid type="imdb">tt0112069</uniqueid>
            </movie>""",
        )
        parsed = core.parse_nfo(nfo, "movie.nfo")
        self.assertEqual(parsed.root_type, "movie")
        self.assertEqual(parsed.title, "神奇校车")
        self.assertEqual(parsed.year, "1994")
        self.assertEqual(parsed.genres, ["动画", "儿童"])
        self.assertEqual(parsed.unique_ids["imdb"], "tt0112069")

    def test_rejects_doctype_and_external_entity(self) -> None:
        nfo = self.write(
            "Movie/movie.nfo",
            """<!DOCTYPE movie [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
            <movie><title>&xxe;</title></movie>""",
        )
        with self.assertRaises(core.NfoParseError):
            core.parse_nfo(nfo, "movie.nfo")


class RecognitionDecisionTest(PayloadFixture):
    def test_high_confidence_movie_is_ready(self) -> None:
        self.write("MagicBus/Magic.Bus.1994.mkv")
        self.write("MagicBus/poster.jpg")
        self.write(
            "MagicBus/movie.nfo",
            """<movie><title>神奇校车大电影</title><year>1994</year>
            <plot>儿童科学动画电影。</plot><genre>动画</genre></movie>""",
        )
        decision = core.inspect_downloaded_payload(
            self.root,
            "MagicBus",
            site_item_id="10001",
        )
        self.assertTrue(decision.auto_eligible)
        self.assertGreaterEqual(decision.confidence, 0.80)
        self.assertEqual(decision.item.media_type, core.MediaKind.MOVIE)
        self.assertEqual(decision.item.media_id, "piggo:movie:item:10001")
        self.assertEqual(decision.item.poster_file, "poster.jpg")
        self.assertEqual(
            decision.transfer_preview.file_mappings[0]["target"],
            "儿童动画电影/神奇校车大电影 (1994)/神奇校车大电影 (1994).mkv",
        )

    def test_high_confidence_series_reads_season_and_episodes(self) -> None:
        self.write("Dinosaur/Season 01/Dinosaur.S01E01.mkv")
        self.write("Dinosaur/Season 01/Dinosaur.S01E02.mkv")
        self.write("Dinosaur/poster.png")
        self.write("Dinosaur/fanart.jpg")
        self.write(
            "Dinosaur/tvshow.nfo",
            """<tvshow><title>恐龙列车</title><originaltitle>Dinosaur Train</originaltitle>
            <year>2009</year><plot>儿童科普动画。</plot><genre>儿童</genre></tvshow>""",
        )
        decision = core.inspect_downloaded_payload(self.root, "Dinosaur")
        self.assertTrue(decision.auto_eligible)
        self.assertEqual(decision.item.media_type, core.MediaKind.TV)
        self.assertEqual(decision.item.season, 1)
        self.assertEqual(decision.item.episode_count, 2)
        self.assertEqual(decision.item.fanart_file, "fanart.jpg")
        self.assertEqual(len(decision.transfer_preview.file_mappings), 2)
        self.assertTrue(
            decision.transfer_preview.file_mappings[0]["target"].endswith(
                "恐龙列车 - S01E01.mkv"
            )
        )

    def test_series_without_episode_numbers_requires_review(self) -> None:
        self.write("UnknownEpisodes/episode-one.mkv")
        self.write(
            "UnknownEpisodes/tvshow.nfo",
            "<tvshow><title>无编号剧集</title><year>2020</year></tvshow>",
        )
        decision = core.inspect_downloaded_payload(self.root, "UnknownEpisodes")
        self.assertFalse(decision.auto_eligible)
        self.assertIsNone(decision.transfer_preview)
        self.assertIn("episode_number_missing", {item.code for item in decision.conflicts})

    def test_multi_file_movie_requires_review(self) -> None:
        self.write("MovieDiscs/Movie.CD1.mkv")
        self.write("MovieDiscs/Movie.CD2.mkv")
        self.write(
            "MovieDiscs/movie.nfo",
            "<movie><title>上下集电影</title><year>2020</year></movie>",
        )
        decision = core.inspect_downloaded_payload(self.root, "MovieDiscs")
        self.assertFalse(decision.auto_eligible)
        self.assertIn("movie_file_count_conflict", {item.code for item in decision.conflicts})

    def test_multi_season_payload_requires_review(self) -> None:
        self.write("Collection/Season 01/Show.S01E01.mkv")
        self.write("Collection/Season 02/Show.S02E01.mkv")
        self.write("Collection/tvshow.nfo", "<tvshow><title>合集节目</title><year>2020</year></tvshow>")
        decision = core.inspect_downloaded_payload(self.root, "Collection")
        self.assertFalse(decision.auto_eligible)
        self.assertEqual(decision.item.media_type, core.MediaKind.COLLECTION)
        self.assertIn("multi_season_payload", {item.code for item in decision.conflicts})

    def test_movie_and_tv_nfo_conflict_requires_review(self) -> None:
        self.write("Mixed/Mixed.mkv")
        self.write("Mixed/movie.nfo", "<movie><title>混合包</title><year>2020</year></movie>")
        self.write("Mixed/tvshow.nfo", "<tvshow><title>混合包</title><year>2020</year></tvshow>")
        decision = core.inspect_downloaded_payload(self.root, "Mixed")
        self.assertFalse(decision.auto_eligible)
        self.assertIn("media_type_conflict", {item.code for item in decision.conflicts})

    def test_decision_contains_no_absolute_file_paths(self) -> None:
        self.write("Safe/Safe.mkv")
        decision = core.inspect_downloaded_payload(self.root, "Safe")
        serialized = str(decision.to_dict())
        self.assertNotIn(str(self.root), serialized)


class VersionParityTest(unittest.TestCase):
    def test_v2_and_v3_core_are_identical(self) -> None:
        v2 = REPOSITORY_ROOT / "plugins.v2" / "piggokidsmetadata" / "core.py"
        self.assertEqual(v2.read_bytes(), CORE_PATH.read_bytes())


if __name__ == "__main__":
    unittest.main()
