"""Multi-language expansion: check_parallel, download_caption, set_localization --locs/--description,
and the quota-exhausted message."""
import io
import json
import pathlib
import tempfile
import unittest
import urllib.error

import support  # noqa: F401  (puts scripts/ on sys.path)
from support import quiet, temp_home

import check_parallel as P
import download_caption as D
import set_localization as L
import upload_caption as U

PIVOT = """1
00:00:00,000 --> 00:00:02,000
Put your phone down.

2
00:00:02,000 --> 00:00:04,000
♪ Phone down, phone down ♪

3
00:00:04,000 --> 00:00:05,500
[Acme Theme]

4
00:00:05,500 --> 00:00:07,000
Nasty!

5
00:00:07,000 --> 00:00:09,000
I met DJ Jordan at AcmeFest.
"""


def blocks(text):
    return P.parse_srt(text)


def srt(items):
    return "\n\n".join(f"{i}\n{s} --> {e}\n" + "\n".join(t) for i, (s, e, t) in enumerate(items, 1)) + "\n"


class CheckParallelTests(unittest.TestCase):
    def setUp(self):
        self.pivot = blocks(PIVOT)

    def translated(self, over=None):
        texts = {1: ["Pose ton téléphone."], 2: ["♪ Phone down, phone down ♪"], 3: ["[Acme Theme]"],
                 4: ["Nasty!"], 5: ["J'ai rencontré DJ Jordan à AcmeFest."]}
        texts.update(over or {})
        return [(s, e, texts[n]) for n, (s, e, _) in enumerate(self.pivot, 1)]

    def test_clean_translation_passes(self):
        err, warn = P.check(self.pivot, self.translated(), "fr", "ko", keep={4})
        self.assertEqual(err, [])
        self.assertEqual(warn, [])

    def test_identical_block_is_an_error_unless_kept(self):
        err, _ = P.check(self.pivot, self.translated(), "fr", "ko")
        self.assertEqual(len(err), 1)
        self.assertIn("#4", err[0])
        self.assertIn("--keep-same 4", err[0])

    def test_lyrics_and_bracket_titles_are_exempt(self):
        err, _ = P.check(self.pivot, self.translated(), "fr", "ko", keep={4})
        self.assertFalse(any("#2" in e or "#3" in e for e in err))

    def test_timecode_and_count_mismatch(self):
        t = self.translated()
        t[0] = ("00:00:00,100", t[0][1], t[0][2])
        err, _ = P.check(self.pivot, t[:-1], "fr", "ko", keep={4})
        self.assertTrue(any("block count" in e for e in err))
        self.assertTrue(any("#1 timecode" in e for e in err))

    def test_source_script_leftover(self):
        err, _ = P.check(self.pivot, self.translated({1: ["폰 내려놔"]}), "fr", "ko", keep={4})
        self.assertTrue(any("source-language" in e for e in err))
        # translating INTO the source language is not "leftover"
        err, _ = P.check(self.pivot, self.translated({1: ["폰 내려놔"]}), "ko", "ko", keep={4})
        self.assertFalse(any("source-language" in e for e in err))

    def test_cjk_line_limit_and_three_lines(self):
        long_ja = "あ" * 19
        err, warn = P.check(self.pivot, self.translated({1: [long_ja]}), "ja", "ko", keep={4})
        self.assertTrue(any("line length 19 > 18" in w for w in warn))
        err, _ = P.check(self.pivot, self.translated({1: ["a", "b", "c"]}), "de", "ko", keep={4})
        self.assertTrue(any("3 lines" in e for e in err))

    def test_proper_noun_warning(self):
        _, warn = P.check(self.pivot, self.translated({5: ["Je l'ai rencontré."]}), "fr", "ko", keep={4})
        self.assertIn("#5 proper noun missing? AcmeFest", warn)
        self.assertIn("#5 proper noun missing? DJ", warn)

    def test_parse_keep_and_lang_from_name(self):
        self.assertEqual(P.parse_keep("95, 144,"), {95, 144})
        with self.assertRaises(ValueError):
            P.parse_keep("95,x")
        self.assertEqual(P.lang_from_name(pathlib.Path("abc.zh-TW.srt")), "zh-TW")
        self.assertEqual(P.line_limit("zh-TW"), 18)

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as d, temp_home():
            pv = pathlib.Path(d, "v.en.srt")
            pv.write_text(PIVOT, encoding="utf-8")
            tg = pathlib.Path(d, "v.fr.srt")
            tg.write_text(srt(self.translated()), encoding="utf-8")
            with quiet():
                self.assertEqual(P.main([str(pv), str(tg), "--source-lang", "ko"]), 1)
                self.assertEqual(P.main([str(pv), str(tg), "--source-lang", "ko", "--keep-same", "4"]), 0)


class DownloadCaptionTests(unittest.TestCase):
    TRACKS = [{"id": "a", "snippet": {"language": "en", "trackKind": "standard"}},
              {"id": "b", "snippet": {"language": "ko", "trackKind": "asr"}},
              {"id": "c", "snippet": {"language": "ja", "trackKind": "standard"}}]

    def test_pick_tracks(self):
        self.assertEqual([t["id"] for t in D.pick_tracks(self.TRACKS, None, False)], ["a", "c"])
        self.assertEqual([t["id"] for t in D.pick_tracks(self.TRACKS, None, True)], ["a", "b", "c"])
        self.assertEqual([t["id"] for t in D.pick_tracks(self.TRACKS, "en", False)], ["a"])

    def test_needs_exactly_one_mode(self):
        with self.assertRaises(SystemExit), quiet():
            D.main(["--video", "x"])
        with self.assertRaises(SystemExit), quiet():
            D.main(["--video", "x", "--lang", "en", "--all"])


class LocsTests(unittest.TestCase):
    SN = {"title": "Default", "description": "Base desc", "categoryId": "10", "defaultLanguage": "ko"}

    def write(self, data):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(data, f, ensure_ascii=False)
        f.close()
        return f.name

    def test_load_locs(self):
        titles, descs = L.load_locs(self.write({"ja": {"title": "タイトル【日本語字幕】", "description": "説明"},
                                                  "es": {"title": "Título (Sub Español)"}}))
        self.assertEqual(titles, {"ja": "タイトル【日本語字幕】", "es": "Título (Sub Español)"})
        self.assertEqual(descs, {"ja": "説明"})

    def test_load_locs_rejects_bad_input(self):
        for bad in ({"ja": {"description": "x"}}, {"ja": {"title": "x" * 101}}, ["ja"], {"not a lang!": {"title": "x"}}):
            with self.assertRaises(ValueError):
                L.load_locs(self.write(bad))

    def test_parse_descriptions(self):
        self.assertEqual(L.parse_descriptions(["de=Zeile 1\\nZeile 2"]), {"de": "Zeile 1\nZeile 2"})
        with self.assertRaises(ValueError):
            L.parse_descriptions(["de"])

    def test_build_body_keeps_other_locales_and_sets_descriptions(self):
        loc = {"en": {"title": "Old EN (ENG SUB)", "description": "EN desc"}}
        body = L.build_body("vid", self.SN, loc, {"ja": "JA"}, None, "ko", False, {"ja": "JA desc", "fr": "FR desc"})
        new = body["localizations"]
        self.assertEqual(new["en"], loc["en"])
        self.assertEqual(new["ja"], {"title": "JA", "description": "JA desc"})
        self.assertEqual(new["fr"], {"title": "Default", "description": "FR desc"})
        self.assertEqual(body["snippet"]["title"], "Default")


class QuotaMessageTests(unittest.TestCase):
    def test_quota_exceeded_is_one_readable_line(self):
        e = urllib.error.HTTPError("u", 403, "Forbidden", {},
                                   io.BytesIO(b'{"error":{"errors":[{"reason":"quotaExceeded"}]}}'))
        with self.assertRaises(SystemExit) as cm:
            U.raise_for_http(e, "Upload")
        self.assertIn("quota exhausted", str(cm.exception))
        e2 = urllib.error.HTTPError("u", 404, "Not Found", {}, io.BytesIO(b"videoNotFound"))
        with self.assertRaises(SystemExit) as cm:
            U.raise_for_http(e2, "Upload")
        self.assertIn("404", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
