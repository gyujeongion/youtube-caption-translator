import contextlib
import io
import json
import pathlib
import shutil
import os
import subprocess
import tempfile
import unittest
from unittest import mock

import support  # noqa: F401  (sets sys.path)
from support import quiet

import _common as C
import context_pack as CP


def blocks_from(rows):
    """rows: [(start, end, text)] -> block dicts like C.read_srt."""
    return [{"index": str(i + 1), "start": s, "end": e, "text": t} for i, (s, e, t) in enumerate(rows)]


def write_srt(path, rows):
    C.write_srt(path, [{"start": s, "end": e, "text": t} for s, e, t in rows])


KO_ROWS = [
    (0.0, 2.0, "안녕하세요 오늘은 요리를 합니다 여러분"),   # 1 plain
    (2.2, 4.5, "먼저 재료를 손질해 볼게요 천천히요"),        # 2 plain
    (6.0, 6.8, "여기 봐"),                                   # 3 deictic + short after gap + imperative
    (7.0, 9.0, "형이 자주 플레이하는 거 중에"),              # 4 truncated
    (9.1, 11.0, "이건 좀 매워요 조심하세요 그래서"),         # 5 deictic
]
JA_ROWS = [
    (0.0, 2.0, "今日は料理を作ります皆さん見てください"),
    (3.5, 4.0, "ここ"),
    (4.2, 6.0, "それは危ないよ"),
]
EN_ROWS = [
    (0.0, 3.0, "Welcome back to the kitchen everyone today we make soup"),
    (3.1, 5.0, "Grab that one, over there"),
    (7.0, 7.5, "Yeah."),
    (7.6, 9.0, "The onions go in first and then the carrots"),
]


class BlockSpecTests(unittest.TestCase):
    def test_ranges_and_lists(self):
        self.assertEqual(CP.parse_block_spec("12,45,50-53", 100), [12, 45, 50, 51, 52, 53])
        self.assertEqual(CP.parse_block_spec(" 3 , 1-2, 2 ", 10), [1, 2, 3])

    def test_errors(self):
        for bad in ("", "a", "5-3", "1--2", "0"):
            with self.assertRaises(ValueError, msg=bad):
                CP.parse_block_spec(bad, 10)
        with self.assertRaises(ValueError):
            CP.parse_block_spec("11", 10)


class ScoringTests(unittest.TestCase):
    def test_korean(self):
        s = CP.score_blocks(blocks_from(KO_ROWS), "ko")
        by = {x["id"]: x for x in s}
        self.assertEqual(by[1]["score"], 0)
        self.assertEqual(by[2]["score"], 0)
        self.assertTrue(any("deictic" in r for r in by[3]["reasons"]))
        self.assertTrue(any("very short" in r and "gap" in r for r in by[3]["reasons"]))
        self.assertTrue(any("imperative" in r for r in by[3]["reasons"]))
        self.assertTrue(any("truncated" in r for r in by[4]["reasons"]))
        self.assertTrue(any("이건" in r for r in by[5]["reasons"]))
        chosen = CP.select_blocks(s, None, True, 30)
        self.assertEqual([e["id"] for e in chosen], [3, 4, 5])
        self.assertTrue(all(e["reasons"] for e in chosen))

    def test_japanese(self):
        s = CP.score_blocks(blocks_from(JA_ROWS), "ja")
        by = {x["id"]: x for x in s}
        self.assertTrue(any("ここ" in r for r in by[2]["reasons"]))
        self.assertTrue(any("very short" in r for r in by[2]["reasons"]))
        self.assertTrue(any("それ" in r for r in by[3]["reasons"]))
        self.assertTrue(any("imperative" in r for r in by[1]["reasons"]))  # ください

    def test_english(self):
        s = CP.score_blocks(blocks_from(EN_ROWS), "en")
        by = {x["id"]: x for x in s}
        self.assertTrue(any("that one" in r for r in by[2]["reasons"]))
        self.assertTrue(any("over there" in r for r in by[2]["reasons"]))
        self.assertTrue(any("very short" in r for r in by[3]["reasons"]))
        self.assertEqual(by[1]["score"], 0)
        self.assertEqual(by[4]["score"], 0)

    def test_language_detection(self):
        self.assertEqual(CP.detect_lang(blocks_from(KO_ROWS)), "ko")
        self.assertEqual(CP.detect_lang(blocks_from(JA_ROWS)), "ja")
        self.assertEqual(CP.detect_lang(blocks_from(EN_ROWS)), "en")

    def test_max_blocks_and_tie_break(self):
        rows = [(i * 3.0, i * 3.0 + 1.0, "여기") for i in range(10)]
        s = CP.score_blocks(blocks_from(rows), "ko")
        chosen = CP.select_blocks(s, None, True, 4)
        self.assertEqual(len(chosen), 4)
        # equal scores -> the highest scores first, ties by time; block 1 has no gap, so it scores lower
        self.assertEqual([e["id"] for e in chosen], [2, 3, 4, 5])

    def test_explicit_plus_auto(self):
        s = CP.score_blocks(blocks_from(KO_ROWS), "ko")
        chosen = CP.select_blocks(s, [1], True, 2)
        self.assertEqual([e["id"] for e in chosen], [1, 3])   # explicit kept, auto fills the remaining slot
        self.assertEqual(chosen[0]["reasons"][0], "requested explicitly")
        only = CP.select_blocks(s, [1, 2], False, 30)
        self.assertEqual([e["id"] for e in only], [1, 2])

    def test_speaker_change_scores(self):
        rows = [(0.0, 2.0, "그래서 이제 시작할게요 여러분"), (2.1, 4.0, "네 알겠어요 그럼요 그렇죠")]
        s = CP.score_blocks(blocks_from(rows), "ko", ["S1", "S2"])
        self.assertTrue(any("speaker change" in r for r in s[1]["reasons"]))
        self.assertGreaterEqual(s[1]["score"], CP.AUTO_MIN_SCORE)
        s2 = CP.score_blocks(blocks_from(rows), "ko", ["S1", "S1"])
        self.assertFalse(any("speaker change" in r for r in s2[1]["reasons"]))

    def test_korean_truncation_skipped_for_non_korean(self):
        s = CP.score_blocks(blocks_from(EN_ROWS), "en")
        self.assertFalse(any("truncated" in r for x in s for r in x["reasons"]))


class SidecarTests(unittest.TestCase):
    def test_overlap_mapping(self):
        segs = [{"start": 0, "end": 5, "speaker": "S1"}, {"start": 5, "end": 12, "speaker": "S2"}]
        self.assertEqual(CP.speaker_for({"start": 4.0, "end": 9.0}, segs), "S2")   # 1s vs 4s
        self.assertEqual(CP.speaker_for({"start": 1.0, "end": 3.0}, segs), "S1")
        self.assertEqual(CP.speaker_for({"start": 20.0, "end": 21.0}, segs), "")
        self.assertEqual(CP.speaker_for({"start": 1.0, "end": 2.0}, []), "")

    def test_tolerates_bad_files(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            with quiet():
                self.assertEqual(CP.load_sidecar(d / "missing.json"), [])
                (d / "a.json").write_text("not json", encoding="utf-8")
                self.assertEqual(CP.load_sidecar(d / "a.json"), [])
                (d / "b.json").write_text('{"x": 1}', encoding="utf-8")
                self.assertEqual(CP.load_sidecar(d / "b.json"), [])
                (d / "c.json").write_text(json.dumps(
                    [{"start": 0, "end": 1, "speaker": "S1"}, {"start": "x"}, {"start": 2, "end": 1, "speaker": "S2"}]),
                    encoding="utf-8")
                self.assertEqual(CP.load_sidecar(d / "c.json"), [{"start": 0.0, "end": 1.0, "speaker": "S1"}])
            self.assertEqual(CP.load_sidecar(None), [])


class LayoutTests(unittest.TestCase):
    def test_frame_times(self):
        self.assertEqual(CP.frame_times(10.0, 20.0, 3, None), [11.5, 15.0, 18.5])
        self.assertEqual(CP.frame_times(10.0, 20.0, 1, None), [15.0])
        clamped = CP.frame_times(4.0, 10.0, 3, 5.0)
        self.assertTrue(all(t <= 4.9 + 1e-9 for t in clamped))

    def test_crops(self):
        self.assertEqual(CP.parse_crops("none"), [])
        self.assertEqual(CP.parse_crops("right,left"), ["left", "right"])
        with self.assertRaises(ValueError):
            CP.parse_crops("top")

    def test_layout_no_crop(self):
        lay = CP.layout(320, 240, [], 3, 320)
        self.assertEqual((lay["width"], lay["height"]), (320, 720))


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.srt = self.dir / "s.srt"
        write_srt(self.srt, KO_ROWS)
        entries = [{"id": 3}, {"id": 4}]
        self.template = CP.make_template(entries)
        CP.write_json(self.dir / "pack.json", {"blocks": [{"id": 3}, {"id": 4}]})
        self.map = self.dir / "speaker_map.json"

    def tearDown(self):
        self.tmp.cleanup()

    def run_check(self):
        args = CP.build_parser().parse_args(["check", "--map", str(self.map), "--srt", str(self.srt)])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), quiet():
            rc = args.func(args)
        return rc, buf.getvalue()

    def test_template_shape(self):
        self.assertEqual(set(self.template), {"people", "cuts", "blocks"})
        self.assertEqual(set(self.template["blocks"]["3"]),
                         {"speaker", "addressee", "referent", "real_or_hypothetical", "confidence", "note"})

    def test_untouched_template_is_unresolved(self):
        CP.write_json(self.map, self.template)
        rc, out = self.run_check()
        self.assertEqual(rc, 1)
        self.assertIn("1. Block 3", out)
        self.assertIn("2. Block 4", out)
        self.assertIn("여기 봐", out)

    def test_filled_passes_and_low_confidence_fails(self):
        m = json.loads(json.dumps(self.template))
        m["blocks"]["3"].update(speaker="A", addressee="B", referent="the pan", confidence="high")
        m["blocks"]["4"].update(speaker="B", addressee="A", confidence="medium")
        CP.write_json(self.map, m)
        rc, out = self.run_check()
        self.assertEqual(rc, 0, out)
        m["blocks"]["4"]["confidence"] = "low"
        CP.write_json(self.map, m)
        rc, out = self.run_check()
        self.assertEqual(rc, 1)
        self.assertIn("Block 4", out)
        self.assertIn("confidence is low", out)
        self.assertNotIn("Block 3", out)

    def test_missing_speaker_or_addressee(self):
        m = json.loads(json.dumps(self.template))
        m["blocks"]["3"].update(speaker="A", addressee="", confidence="high")
        m["blocks"]["4"].update(speaker="", addressee="A", confidence="high")
        CP.write_json(self.map, m)
        rc, out = self.run_check()
        self.assertEqual(rc, 1)
        self.assertIn("addressee is empty", out)
        self.assertIn("speaker is empty", out)

    def test_block_missing_from_map(self):
        m = json.loads(json.dumps(self.template))
        del m["blocks"]["4"]
        m["blocks"]["3"].update(speaker="A", addressee="B", confidence="high")
        CP.write_json(self.map, m)
        rc, out = self.run_check()
        self.assertEqual(rc, 1)
        self.assertIn("no entry in speaker_map.json", out)

    def test_broken_map_file(self):
        self.map.write_text("{oops", encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.run_check()

    def test_bom_map_and_manifest_tolerated(self):
        m = json.loads(json.dumps(self.template))
        m["blocks"]["3"].update(speaker="A", addressee="B", confidence="high")
        m["blocks"]["4"].update(speaker="B", addressee="A", confidence="high")
        self.map.write_bytes(b"\xef\xbb\xbf" + json.dumps(m, ensure_ascii=False).encode("utf-8"))
        (self.dir / "pack.json").write_bytes(b"\xef\xbb\xbf" + json.dumps({"blocks": [{"id": 3}, {"id": 4}]}).encode("utf-8"))
        rc, out = self.run_check()
        self.assertEqual(rc, 0, out)

    def test_paths_with_stray_quotes(self):
        CP.write_json(self.map, self.template)
        with mock.patch.object(C, "is_windows", return_value=True):
            args = CP.build_parser().parse_args(
                ["check", "--map", f'"{self.map}"', "--srt", f'"{self.srt}"'])
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), quiet():
                rc = args.func(args)
        self.assertEqual(rc, 1)
        self.assertIn("Block 3", buf.getvalue())

    def test_cp949_srt_is_a_clean_error(self):
        CP.write_json(self.map, self.template)
        self.srt.write_bytes("1\n00:00:00,000 --> 00:00:01,000\n안녕하세요\n".encode("cp949"))
        args = CP.build_parser().parse_args(["check", "--map", str(self.map), "--srt", str(self.srt)])
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = args.func(args)
        self.assertEqual(rc, 2)
        self.assertIn("cannot read", err.getvalue())

    def test_build_cp949_srt_returns_2(self):
        video = self.dir / "dummy.mp4"
        video.write_bytes(b"x")
        self.srt.write_bytes("1\n00:00:00,000 --> 00:00:01,000\n안녕하세요\n".encode("cp949"))
        args = CP.build_parser().parse_args(
            ["build", "--video", str(video), "--srt", str(self.srt), "--out", str(self.dir / "o")])
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = args.func(args)
        self.assertEqual(rc, 2)
        self.assertIn("cannot read", err.getvalue())

    def test_bom_sidecar_and_write_lf(self):
        side = self.dir / "spk.json"
        side.write_bytes(b"\xef\xbb\xbf" + json.dumps([{"start": 0, "end": 1, "speaker": "S1"}]).encode("utf-8"))
        self.assertEqual(len(CP.load_sidecar(side)), 1)
        CP.write_json(self.dir / "x.json", {"a": 1})
        self.assertNotIn(b"\r\n", (self.dir / "x.json").read_bytes())

    def test_make_sheet_uses_media_arg_and_resolved_ffmpeg(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, "", "")

        lay = CP.layout(320, 240, [], 1, 320)
        cwd = os.getcwd()
        os.chdir(self.dir)
        try:
            with mock.patch.object(CP.subprocess, "run", fake_run), \
                    mock.patch.object(CP.shutil, "which", return_value="/opt/fake/ffmpeg.exe"):
                CP.make_sheet("-weird.mp4", [1.0], [], lay, 320, 240, pathlib.Path("-out.jpg"))
        finally:
            os.chdir(cwd)
        cmd = seen["cmd"]
        self.assertEqual(cmd[0], "/opt/fake/ffmpeg.exe")
        self.assertTrue(os.path.isabs(cmd[cmd.index("-i") + 1]))
        self.assertTrue(cmd[cmd.index("-i") + 1].endswith("-weird.mp4"))
        self.assertTrue(os.path.isabs(cmd[-1]))
        self.assertFalse(cmd[-1].startswith("-"))


HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def probe_size(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height", "-of", "json", str(path)],
                       capture_output=True, text=True, encoding="utf-8", check=True)
    st = json.loads(r.stdout)["streams"][0]
    return st["width"], st["height"]


class BuildTests(unittest.TestCase):
    """Real end-to-end builds; skipped cleanly when ffmpeg is absent."""

    @classmethod
    def setUpClass(cls):
        if not HAVE_FFMPEG:
            raise unittest.SkipTest("ffmpeg/ffprobe not installed")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = pathlib.Path(cls.tmp.name)
        cls.video = cls.dir / "v.mp4"
        r = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=5",
             "-pix_fmt", "yuv420p", "-y", str(cls.video)], capture_output=True, text=True)
        if r.returncode != 0 or not cls.video.is_file():
            cls.tmp.cleanup()
            raise unittest.SkipTest("ffmpeg cannot make the synthetic test video: " + r.stderr[-200:])
        cls.srt = cls.dir / "s.srt"
        write_srt(cls.srt, [
            (0.0, 1.0, "안녕하세요 오늘은 요리를 합니다"),
            (2.5, 3.0, "여기 봐"),
            (3.2, 4.8, "형이 자주 플레이하는 거 중에"),
            (4.8, 5.0, "터진다 그냥"),
        ])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def build(self, out, *extra):
        args = CP.build_parser().parse_args(
            ["build", "--video", str(self.video), "--srt", str(self.srt), "--out", str(out), *extra])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), quiet():
            rc = args.func(args)
        return rc, buf.getvalue()

    def test_auto_build_full_frame(self):
        out = self.dir / "pack_full"
        rc, _ = self.build(out, "--auto", "--width", "320")
        self.assertEqual(rc, 0)
        pack = json.loads((out / "pack.json").read_text(encoding="utf-8"))
        ids = [b["id"] for b in pack["blocks"]]
        self.assertEqual(ids, [2, 3])
        for i in ids:
            sheet = out / "sheets" / f"block_{i:04d}.jpg"
            self.assertTrue(sheet.is_file())
            self.assertEqual(probe_size(sheet), (320, 240 * 3))          # 3 rows, 1 column
        index = (out / "index.md").read_text(encoding="utf-8")
        for needle in ("## Block 2", "## Block 3", "sheets/block_0002.jpg", "00:00:02,500 --> 00:00:03,000",
                       "여기 봐", "Selected because", "deictic", "Who is speaking", "point at", "real (happening now)",
                       "cut from another point in time", "row 1:", "#3 "):
            self.assertIn(needle, index)
        self.assertNotIn("## Block 1 ", index)
        self.assertTrue((out / "speaker_map.template.json").is_file())
        self.assertTrue((out / "speaker_map.json").is_file())

    def test_crops_dimensions_and_frames(self):
        out = self.dir / "pack_crops"
        rc, _ = self.build(out, "--blocks", "2", "--crops", "left,center,right", "--frames", "2", "--width", "480")
        self.assertEqual(rc, 0)
        lay = CP.layout(320, 240, ["left", "center", "right"], 2, 480)
        self.assertEqual(lay["cols"], 3)
        self.assertEqual(probe_size(out / "sheets" / "block_0002.jpg"), (lay["width"], lay["height"]))
        self.assertEqual(lay["width"], 480)

    def test_sidecar_label_in_index(self):
        out = self.dir / "pack_spk"
        side = self.dir / "spk.json"
        side.write_text(json.dumps([{"start": 0, "end": 2.9, "speaker": "S1"}, {"start": 2.9, "end": 6, "speaker": "S2"}]),
                        encoding="utf-8")
        rc, _ = self.build(out, "--blocks", "2,3", "--frames", "1", "--speakers", str(side), "--width", "160")
        self.assertEqual(rc, 0)
        index = (out / "index.md").read_text(encoding="utf-8")
        self.assertIn("Speaker label from sidecar: S1", index)
        self.assertIn("Speaker label from sidecar: S2", index)
        self.assertIn("[S2]", index)

    def test_never_overwrites_speaker_map(self):
        out = self.dir / "pack_keep"
        self.build(out, "--blocks", "2", "--frames", "1", "--width", "160")
        mp = out / "speaker_map.json"
        m = json.loads(mp.read_text(encoding="utf-8"))
        m["blocks"]["2"].update(speaker="A", addressee="B", confidence="high")
        CP.write_json(mp, m)
        before = mp.read_text(encoding="utf-8")
        rc, stdout = self.build(out, "--blocks", "2,3", "--frames", "1", "--width", "160")
        self.assertEqual(rc, 0)
        self.assertEqual(mp.read_text(encoding="utf-8"), before)
        self.assertIn("not overwritten", stdout)
        self.assertIn("3", stdout.split("no entry for:")[1])
        self.assertIn('"3"', (out / "speaker_map.template.json").read_text(encoding="utf-8"))
        rc, _ = self.build(out, "--blocks", "2,3", "--frames", "1", "--width", "160", "--force")
        self.assertEqual(rc, 0)
        self.assertNotEqual(mp.read_text(encoding="utf-8"), before)

    def test_quoted_paths_and_lf_output(self):
        out = self.dir / "pack_q"
        with mock.patch.object(C, "is_windows", return_value=True):
            args = CP.build_parser().parse_args(
                ["build", "--video", f'"{self.video}"', "--srt", f'"{self.srt}"', "--out", f'"{out}"',
                 "--blocks", "2", "--frames", "1", "--width", "160"])
            with contextlib.redirect_stdout(io.StringIO()), quiet():
                rc = args.func(args)
        self.assertEqual(rc, 0)
        for name in ("index.md", "pack.json", "speaker_map.json", "speaker_map.template.json"):
            raw = (out / name).read_bytes()
            self.assertNotIn(b"\r\n", raw, name)
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), name)

    def test_bad_arguments(self):
        out = self.dir / "pack_bad"
        rc, _ = self.build(out, "--blocks", "99")
        self.assertEqual(rc, 2)
        rc, _ = self.build(out, "--crops", "top")
        self.assertEqual(rc, 2)
        self.assertEqual(CP.build_parser().parse_args(
            ["build", "--video", "x", "--srt", "y", "--out", "z"]).frames, 3)


if __name__ == "__main__":
    unittest.main()
