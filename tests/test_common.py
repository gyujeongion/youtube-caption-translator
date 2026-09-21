import os
import pathlib
import stat
import sys
import unittest

import support  # noqa: F401  (sets sys.path)
from support import temp_home

import _common as C


class ConfigTests(unittest.TestCase):
    def test_default_and_override(self):
        with temp_home() as d:
            self.assertEqual(C.config_dir(), d)
        env = {k: v for k, v in os.environ.items() if k != "YTCAPTION_HOME"}
        from unittest import mock
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(C.config_dir(), pathlib.Path.home() / ".claude" / "credentials")

    def test_env_file_and_real_env_override(self):
        with temp_home() as d:
            (d / "ytcaption.env").write_text(
                "# comment\nDEEPGRAM_API_KEY=file-dg\nexport OPENAI_API_KEY='file-oa'\nOTHER=x\n", encoding="utf-8")
            env = C.load_env()
            self.assertEqual(env["DEEPGRAM_API_KEY"], "file-dg")
            self.assertEqual(env["OPENAI_API_KEY"], "file-oa")
            self.assertEqual(C.get_key("soniox"), "")
            os.environ["DEEPGRAM_API_KEY"] = "real-dg"
            self.assertEqual(C.get_key("deepgram"), "real-dg")

    def test_set_env_key_preserves_and_locks(self):
        with temp_home() as d:
            (d / "ytcaption.env").write_text("# keep me\nOPENAI_API_KEY=old\n", encoding="utf-8")
            p = C.set_env_key("DEEPGRAM_API_KEY", "abc12345")
            C.set_env_key("OPENAI_API_KEY", "newvalue1")
            text = p.read_text(encoding="utf-8")
            self.assertIn("# keep me", text)
            self.assertIn("DEEPGRAM_API_KEY=abc12345", text)
            self.assertIn("OPENAI_API_KEY=newvalue1", text)
            self.assertNotIn("old", text)
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
            self.assertEqual([x.name for x in d.iterdir()], ["ytcaption.env"])  # no temp litter

    def test_prefs_roundtrip_and_validation(self):
        with temp_home():
            self.assertEqual(C.load_prefs(), {})
            C.save_prefs({"source_language": "ko", "target_languages": ["en", "ja"]})
            self.assertEqual(C.load_prefs()["target_languages"], ["en", "ja"])
        self.assertEqual(C.validate_pref("target_languages", "en, ja ,pt-BR"), ["en", "ja", "pt-BR"])
        self.assertEqual(C.validate_pref("publish_mode", "direct"), "direct")
        self.assertEqual(C.validate_pref("stt_engine", ""), "")
        for k, v in [("publish_mode", "yolo"), ("input_mode", "x"), ("source_language", "korean!!"),
                     ("target_languages", ""), ("nope", "1"), ("token_file", " ")]:
            with self.assertRaises(ValueError, msg=(k, v)):
                C.validate_pref(k, v)


class SrtTests(unittest.TestCase):
    def test_timestamps(self):
        self.assertEqual(C.format_ts(3661.5), "01:01:01,500")
        self.assertEqual(C.format_ts(0.0004), "00:00:00,000")
        self.assertAlmostEqual(C.parse_ts("01:01:01,500"), 3661.5)
        self.assertAlmostEqual(C.parse_ts("00:00:01.5"), 1.5)
        with self.assertRaises(ValueError):
            C.parse_ts("garbage")

    def test_roundtrip_bom_crlf(self):
        with temp_home() as d:
            p = d / "a.srt"
            p.write_bytes("﻿1\r\n00:00:01,000 --> 00:00:02,500\r\n안녕하세요\r\n두번째 줄\r\n\r\n"
                          "2\r\n00:00:03,000 --> 00:00:04,000\r\nHello\r\n".encode("utf-8"))
            blocks = C.read_srt(p)
            self.assertEqual(len(blocks), 2)
            self.assertEqual(blocks[0]["text"], "안녕하세요\n두번째 줄")
            self.assertAlmostEqual(blocks[1]["start"], 3.0)
            out = d / "b.srt"
            C.write_srt(out, blocks)
            raw = out.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
            self.assertNotIn(b"\r", raw)
            self.assertEqual([b["text"] for b in C.read_srt(out)], [b["text"] for b in blocks])


def kw(text_words, start=0.0, per=0.4, gap=0.05):
    """Synthetic word list: sequential words of `per` seconds."""
    words, t = [], start
    for w in text_words:
        words.append({"start": t, "end": t + per, "text": w})
        t += per + gap
    return words


class SegmenterTests(unittest.TestCase):
    def check_invariants(self, blocks, max_seconds=7.0):
        prev_end = -1
        for b in blocks:
            lines = b["text"].split("\n")
            self.assertLessEqual(len(lines), 2, b)
            for ln in lines:
                self.assertLessEqual(len(ln), 42, b)
            self.assertGreater(b["end"], b["start"])
            self.assertLessEqual(b["end"] - b["start"], max_seconds + 1e-6, b)
            self.assertGreaterEqual(b["start"], prev_end - 1e-9, "overlap")
            prev_end = b["end"]

    def test_breaks_on_sentence_punctuation(self):
        words = kw("안녕하세요 여러분 오늘은 자막을 만들어 봅니다. 그리고 번역도 합니다. 정말 쉽습니다!".split())
        blocks = C.segment_words(words, lang="ko")
        self.assertEqual(len(blocks), 3)
        self.assertTrue(blocks[0]["text"].endswith("봅니다."))
        self.assertTrue(blocks[1]["text"].startswith("그리고"))
        self.check_invariants(blocks)

    def test_breaks_on_gap(self):
        a = kw("첫 번째 문장이 이어집니다".split())
        b = kw("두 번째 문장은 한참 뒤".split(), start=a[-1]["end"] + 1.5)
        blocks = C.segment_words(a + b, lang="ko")
        self.assertEqual(len(blocks), 2)
        self.check_invariants(blocks)

    def test_gap_at_threshold_does_not_break(self):
        a = kw("가나 다라".split())
        b = kw("마바 사아".split(), start=a[-1]["end"] + 0.79)
        self.assertEqual(len(C.segment_words(a + b, lang="ko")), 1)

    def test_long_monologue_respects_limits(self):
        # 200 Korean words, no punctuation, no gaps: must still split on duration/length
        words = kw(["단어%d" % i for i in range(200)], per=0.3, gap=0.0)
        blocks = C.segment_words(words, lang="ko")
        self.assertGreater(len(blocks), 8)
        self.check_invariants(blocks)
        joined = " ".join(b["text"].replace("\n", " ") for b in blocks).split()
        self.assertEqual(joined, ["단어%d" % i for i in range(200)])  # nothing lost or reordered

    def test_two_lines_balanced_and_min_duration(self):
        long_sentence = "이것은 한 줄에 다 들어가기에는 조금 긴 문장이라서 두 줄로 나누어져야 합니다."
        blocks = C.segment_words(kw(long_sentence.split(), per=0.25, gap=0.0), lang="ko")
        self.check_invariants(blocks)
        two = [b for b in blocks if "\n" in b["text"]]
        self.assertTrue(two)
        short = C.segment_words([{"start": 0.0, "end": 0.2, "text": "네."}], lang="ko")
        self.assertGreaterEqual(short[0]["end"] - short[0]["start"], 1.0 - 1e-9)

    def test_min_duration_never_overlaps_next(self):
        w = [{"start": 0.0, "end": 0.2, "text": "네."}, {"start": 0.3, "end": 1.5, "text": "그렇습니다"}]
        blocks = C.segment_words(w, lang="ko")
        self.assertLessEqual(blocks[0]["end"], blocks[1]["start"] + 1e-9)

    def test_japanese_no_spaces_and_segments_to_words(self):
        seg = [{"start": 0.0, "end": 6.0, "text": "これは日本語のとても長い文章でスペースがありません"}]
        words = C.segments_to_words(seg)
        self.assertGreater(len(words), 1)
        blocks = C.segment_words(words, lang="ja")
        self.check_invariants(blocks)
        self.assertEqual("".join(b["text"].replace("\n", "") for b in blocks), seg[0]["text"])

    def test_segments_to_words_interpolates_and_is_monotonic(self):
        words = C.segments_to_words([{"start": 10.0, "end": 12.0, "text": "가나다 라마 바사아자"}])
        self.assertEqual([w["text"] for w in words], ["가나다", "라마", "바사아자"])
        self.assertAlmostEqual(words[0]["start"], 10.0)
        self.assertAlmostEqual(words[-1]["end"], 12.0)
        for x, y in zip(words, words[1:]):
            self.assertLessEqual(x["end"], y["start"] + 1e-9)

    def test_empty_and_blank_words(self):
        self.assertEqual(C.segment_words([]), [])
        self.assertEqual(C.segment_words([{"start": 0, "end": 1, "text": "  "}]), [])

    def test_oversized_single_token_never_exceeds_two_lines(self):
        blocks = C.segment_words([{"start": 0.0, "end": 3.0, "text": "가" * 120}], lang="ko")
        for b in blocks:
            self.assertLessEqual(len(b["text"].split("\n")), 2)
            self.assertTrue(all(len(x) <= 42 for x in b["text"].split("\n")))
        self.assertEqual("".join(b["text"].replace("\n", "") for b in blocks), "가" * 120)
        for x, y in zip(blocks, blocks[1:]):
            self.assertLessEqual(x["end"], y["start"] + 1e-9)


if __name__ == "__main__":
    unittest.main()
