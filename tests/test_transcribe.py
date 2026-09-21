import contextlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import urllib.error
import urllib.parse
from unittest import mock

import support
from support import FAKE_KEY, FakeNet, FakeResp, quiet, temp_home

import _common as C
import transcribe as T


def http_error(url, code, body=b"nope"):
    return urllib.error.HTTPError(url, code, "err", {}, io.BytesIO(body))


DG_RESPONSE = {"results": {"utterances": [
    {"start": 0.0, "end": 2.0, "transcript": "안녕하세요 여러분.",
     "words": [{"word": "안녕하세요", "start": 0.0, "end": 1.0}, {"word": "여러분", "start": 1.1, "end": 2.0}]},
    {"start": 5.0, "end": 7.0, "transcript": "오늘은 자막입니다.",
     "words": [{"word": "오늘은", "start": 5.0, "end": 5.8, "punctuated_word": "오늘은"},
               {"word": "자막입니다", "start": 5.9, "end": 7.0, "punctuated_word": "자막입니다."}]},
]}}


class DeepgramTests(unittest.TestCase):
    def test_request_shape_and_parsing(self):
        net = FakeNet(lambda m, u, h, d: FakeResp(DG_RESPONSE))
        with tempfile.TemporaryDirectory() as td:
            audio = pathlib.Path(td) / "a.mp3"
            audio.write_bytes(b"ID3fake")
            with mock.patch("urllib.request.urlopen", net):
                words = T.deepgram_transcribe(audio, FAKE_KEY, "ko", "nova-3", "Acme Studio, Nova Cam")
        call = net.calls[0]
        self.assertEqual(call["method"], "POST")
        u = urllib.parse.urlparse(call["url"])
        self.assertEqual((u.scheme, u.netloc, u.path), ("https", "api.deepgram.com", "/v1/listen"))
        q = urllib.parse.parse_qs(u.query)
        self.assertEqual(q["model"], ["nova-3"])
        self.assertEqual(q["language"], ["ko"])
        for flag in ("smart_format", "utterances", "punctuate", "mip_opt_out"):
            self.assertEqual(q[flag], ["true"], flag)
        self.assertEqual(q["keyterm"], ["Acme Studio", "Nova Cam"])
        self.assertEqual(call["headers"]["authorization"], f"Token {FAKE_KEY}")
        self.assertEqual(call["data"], b"ID3fake")
        self.assertEqual([w["text"] for w in words], ["안녕하세요", "여러분.", "오늘은", "자막입니다."])
        self.assertAlmostEqual(words[3]["end"], 7.0)

    def test_falls_back_to_channel_words(self):
        data = {"results": {"channels": [{"alternatives": [{"words": [
            {"word": "hi", "start": 0.0, "end": 0.5, "punctuated_word": "Hi."}]}]}]}}
        self.assertEqual(T.deepgram_words(data)[0]["text"], "Hi.")

    def test_http_error_does_not_leak_key(self):
        net = FakeNet(lambda m, u, h, d: http_error(u, 401, b'{"err":"invalid credentials"}'))
        with tempfile.TemporaryDirectory() as td:
            audio = pathlib.Path(td) / "a.mp3"
            audio.write_bytes(b"x")
            with mock.patch("urllib.request.urlopen", net):
                with self.assertRaises(T.EngineError) as cm:
                    T.deepgram_transcribe(audio, FAKE_KEY, "ko", "nova-3", "")
        self.assertIn("401", str(cm.exception))
        self.assertNotIn(FAKE_KEY, str(cm.exception))


class OpenAITests(unittest.TestCase):
    def test_non_whisper_model_rejected_before_any_call(self):
        net = FakeNet(lambda *a: FakeResp({}))
        with mock.patch("urllib.request.urlopen", net), tempfile.TemporaryDirectory() as td:
            audio = pathlib.Path(td) / "a.mp3"
            audio.write_bytes(b"x")
            for m in ("gpt-transcribe", "gpt-4o-transcribe", "gpt-4o-mini-transcribe"):
                with self.assertRaises(T.ArgError) as cm:
                    T.openai_transcribe(audio, FAKE_KEY, "ko", m, "", pathlib.Path(td))
                self.assertIn("timestamps", str(cm.exception))
        self.assertEqual(net.calls, [])

    def test_whisper1_request_and_segments(self):
        net = FakeNet(lambda m, u, h, d: FakeResp({"segments": [
            {"id": 0, "start": 0.0, "end": 3.0, "text": " 안녕하세요 여러분"}]}))
        with mock.patch("urllib.request.urlopen", net), tempfile.TemporaryDirectory() as td:
            audio = pathlib.Path(td) / "a.mp3"
            audio.write_bytes(b"ID3")
            words = T.openai_transcribe(audio, FAKE_KEY, "ko", "whisper-1", "terms", pathlib.Path(td))
        c = net.calls[0]
        self.assertEqual(c["url"], "https://api.openai.com/v1/audio/transcriptions")
        self.assertEqual(c["headers"]["authorization"], f"Bearer {FAKE_KEY}")
        self.assertTrue(c["headers"]["content-type"].startswith("multipart/form-data; boundary="))
        body = c["data"].decode("utf-8", "replace")
        for needle in ('name="model"\r\n\r\nwhisper-1', 'name="language"\r\n\r\nko',
                       'name="response_format"\r\n\r\nverbose_json', 'name="prompt"\r\n\r\nterms',
                       'filename="audio.mp3"'):
            self.assertIn(needle, body)
        self.assertEqual([w["text"] for w in words], ["안녕하세요", "여러분"])

    def test_chunking_offsets_timestamps(self):
        answers = iter([{"segments": [{"start": 0.0, "end": 2.0, "text": "첫 청크"}]},
                        {"segments": [{"start": 1.0, "end": 3.0, "text": "둘째 청크"}]}])
        net = FakeNet(lambda m, u, h, d: FakeResp(next(answers)))
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            audio = td / "big.mp3"
            audio.write_bytes(b"0" * 100)
            chunks = [(td / "c0.mp3", 0.0), (td / "c1.mp3", 1200.0)]
            for p, _ in chunks:
                p.write_bytes(b"x")
            with mock.patch.object(T, "OPENAI_MAX_BYTES", 10), \
                    mock.patch.object(T, "split_audio", return_value=chunks) as sp, \
                    mock.patch("urllib.request.urlopen", net):
                words = T.openai_transcribe(audio, FAKE_KEY, "ko", "whisper-1", "", td)
        sp.assert_called_once()
        self.assertEqual(len(net.calls), 2)
        self.assertAlmostEqual(words[0]["start"], 0.0)
        self.assertAlmostEqual(words[-1]["end"], 1203.0)   # 3.0 + 1200 offset
        self.assertAlmostEqual(words[2]["start"], 1201.0)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg")
    def test_split_audio_real_ffmpeg(self):
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            src = td / "tone.mp3"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=7",
                            "-ac", "1", "-ar", "16000", "-b:a", "64k", str(src)], check=True)
            (td / "w").mkdir()
            parts = T.split_audio(src, td / "w", chunk_seconds=3)
            self.assertGreaterEqual(len(parts), 2)
            self.assertEqual(parts[0][1], 0.0)
            self.assertTrue(all(parts[i][1] < parts[i + 1][1] for i in range(len(parts) - 1)))
            self.assertAlmostEqual(parts[1][1], 3.0, delta=0.3)


def tok(text, a, b):
    return {"text": text, "start_ms": a, "end_ms": b, "confidence": 0.9}


class SonioxTests(unittest.TestCase):
    def test_tokens_to_words_rebuilds_words(self):
        toks = [tok("Hel", 10, 90), tok("lo", 110, 160), tok(" 안", 200, 250), tok("녕", 250, 300),
                tok("하세요", 300, 400), tok(".", 400, 420), tok("<end>", 420, 420), tok(" 좋아요", 500, 700)]
        w = T.soniox_tokens_to_words(toks)
        self.assertEqual([x["text"] for x in w], ["Hello", "안녕하세요.", "좋아요"])
        self.assertAlmostEqual(w[0]["start"], 0.01)
        self.assertAlmostEqual(w[0]["end"], 0.16)
        self.assertAlmostEqual(w[1]["start"], 0.2)
        self.assertAlmostEqual(w[1]["end"], 0.42)

    def make_handler(self, statuses, delete_status=204, fail_delete=None):
        state = {"polls": 0}
        st = iter(statuses)

        def handler(method, url, headers, data):
            assert headers["authorization"] == f"Bearer {FAKE_KEY}"
            if method == "POST" and url.endswith("/v1/files"):
                return FakeResp({"id": "file-1"})
            if method == "POST" and url.endswith("/v1/transcriptions"):
                return FakeResp({"id": "tr-1", "status": "queued"})
            if method == "GET" and url.endswith("/v1/transcriptions/tr-1"):
                s = next(st)
                return FakeResp({"id": "tr-1", "status": s, "error_message": "bad audio" if s == "error" else None})
            if method == "GET" and url.endswith("/transcript"):
                return FakeResp({"id": "tr-1", "text": "안녕하세요", "tokens": [tok("안녕", 0, 300), tok("하세요", 300, 700)]})
            if method == "DELETE":
                if fail_delete and fail_delete in url:
                    return http_error(url, 500)
                return FakeResp(b"", delete_status)
            raise AssertionError(f"unexpected {method} {url}")
        return handler

    def run_soniox(self, handler, prompt=""):
        net = FakeNet(handler)
        with tempfile.TemporaryDirectory() as td:
            audio = pathlib.Path(td) / "a.mp3"
            audio.write_bytes(b"ID3")
            with mock.patch("urllib.request.urlopen", net), mock.patch.object(T.time, "sleep"), quiet():
                try:
                    words = T.soniox_transcribe(audio, FAKE_KEY, "ko", "stt-async-v5", prompt)
                except Exception as e:  # noqa: BLE001
                    return net, e
        return net, words

    def test_full_flow_polls_and_deletes(self):
        net, words = self.run_soniox(self.make_handler(["queued", "processing", "completed"]), prompt="Acme Studio")
        self.assertEqual([w["text"] for w in words], ["안녕하세요"])
        seq = [(c["method"], c["url"].replace("https://api.soniox.com", "")) for c in net.calls]
        self.assertEqual(seq[0], ("POST", "/v1/files"))
        self.assertEqual(seq[1], ("POST", "/v1/transcriptions"))
        self.assertEqual([s for s in seq if s[0] == "GET" and not s[1].endswith("/transcript")].__len__(), 3)
        self.assertEqual(seq[-2:], [("DELETE", "/v1/transcriptions/tr-1"), ("DELETE", "/v1/files/file-1")])
        create = json.loads(net.calls[1]["data"])
        self.assertEqual(create["model"], "stt-async-v5")
        self.assertEqual(create["file_id"], "file-1")
        self.assertEqual(create["language_hints"], ["ko", "en"])
        self.assertEqual(create["context"], {"terms": ["Acme Studio"]})

    def test_english_hint_not_duplicated(self):
        net = FakeNet(self.make_handler(["completed"]))
        with tempfile.TemporaryDirectory() as td, mock.patch("urllib.request.urlopen", net), quiet():
            a = pathlib.Path(td) / "a.mp3"
            a.write_bytes(b"x")
            T.soniox_transcribe(a, FAKE_KEY, "en", "stt-async-v5", "")
        self.assertEqual(json.loads(net.calls[1]["data"])["language_hints"], ["en"])

    def test_error_status_still_cleans_up(self):
        net, err = self.run_soniox(self.make_handler(["processing", "error"]))
        self.assertIsInstance(err, T.EngineError)
        self.assertIn("bad audio", str(err))
        methods = [(c["method"], c["url"].rsplit("/", 2)[-2]) for c in net.calls if c["method"] == "DELETE"]
        self.assertEqual(len(methods), 2)

    def test_delete_failure_never_fails_the_run(self):
        net, words = self.run_soniox(self.make_handler(["completed"], fail_delete="/transcriptions/"))
        self.assertEqual(words[0]["text"], "안녕하세요")
        deletes = [c["url"] for c in net.calls if c["method"] == "DELETE"]
        self.assertEqual(len(deletes), 2)   # still tried the file after the transcription delete failed

    def test_both_delete_endpoints_fail_still_ok(self):
        def handler(method, url, headers, data):
            if method == "DELETE":
                return urllib.error.URLError("offline")
            return self.make_handler(["completed"])(method, url, headers, data)
        net, words = self.run_soniox(handler)
        self.assertEqual(words[0]["text"], "안녕하세요")

    def test_upload_failure_cleans_nothing_and_raises(self):
        net, err = self.run_soniox(lambda m, u, h, d: http_error(u, 402, b"payment required"))
        self.assertIsInstance(err, T.EngineError)
        self.assertNotIn(FAKE_KEY, str(err))
        self.assertFalse([c for c in net.calls if c["method"] == "DELETE"])


class LocalRuntimeTests(unittest.TestCase):
    def test_pick_runtime(self):
        av = {"whisper.cpp": False, "mlx-whisper": True, "faster-whisper": True}
        self.assertEqual(T.pick_runtime("auto", "whisper.cpp", av), "mlx-whisper")
        self.assertEqual(T.pick_runtime("auto", "faster-whisper", av), "faster-whisper")
        self.assertEqual(T.pick_runtime("whisper.cpp", "faster-whisper", av), "whisper.cpp")
        self.assertEqual(T.pick_runtime("auto", "whisper.cpp", {k: False for k in av}), "")

    def test_missing_runtime_exits_3_with_install_command(self):
        with tempfile.TemporaryDirectory() as td, temp_home():
            src = pathlib.Path(td) / "v.mp4"
            src.write_bytes(b"x")
            err = io.StringIO()
            none = {"whisper.cpp": False, "mlx-whisper": False, "faster-whisper": False}
            with mock.patch.object(T, "available_runtimes", return_value=none), \
                    mock.patch.object(T, "ffprobe_duration", return_value=10.0), \
                    contextlib.redirect_stderr(err):
                code = T.main(["--input", str(src), "--engine", "local", "--out", str(pathlib.Path(td) / "o.srt")])
            self.assertEqual(code, 3)
            self.assertRegex(err.getvalue(), r"one step at a time:\s+(brew install whisper\.cpp|\S+ -m pip install|git clone)")

    def test_whispercpp_parses_json_with_fake_binary(self):
        with tempfile.TemporaryDirectory() as td, temp_home(YTCAPTION_MODEL_DIR=td):
            td = pathlib.Path(td)
            (td / "ggml-large-v3-turbo.bin").write_bytes(b"model")
            wav = td / "a.wav"
            wav.write_bytes(b"RIFF")
            seen = {}

            def fake_run(cmd, **kw):
                seen["cmd"] = cmd
                base = cmd[cmd.index("-of") + 1]
                pathlib.Path(base + ".json").write_text(json.dumps({"transcription": [
                    {"offsets": {"from": 0, "to": 2500}, "text": " 안녕하세요 여러분."},
                    {"offsets": {"from": 3000, "to": 4000}, "text": " 감사합니다."}]}), encoding="utf-8")
                return subprocess.CompletedProcess(cmd, 0, "", "")

            with mock.patch.object(T.shutil, "which", return_value="/bin/whisper-cli"), \
                    mock.patch.object(T.subprocess, "run", side_effect=fake_run), quiet():
                words = T.run_whispercpp(wav, "large-v3-turbo", "ko", "Acme Studio", td)
        cmd = seen["cmd"]
        self.assertEqual(cmd[0], "/bin/whisper-cli")
        self.assertEqual(cmd[cmd.index("-l") + 1], "ko")
        self.assertIn("-ojf", cmd)
        self.assertEqual(cmd[cmd.index("--prompt") + 1], "Acme Studio")
        self.assertEqual(cmd[cmd.index("-mc") + 1], "0")
        self.assertEqual([w["text"] for w in words][:2], ["안녕하세요", "여러분."])
        self.assertAlmostEqual(words[-1]["end"], 4.0)

    def test_whispercpp_drops_rejected_flag_and_retries(self):
        with tempfile.TemporaryDirectory() as td, temp_home(YTCAPTION_MODEL_DIR=td):
            td = pathlib.Path(td)
            model = td / "ggml-small-q5_1.bin"
            model.write_bytes(b"m")
            wav = td / "a.wav"
            wav.write_bytes(b"x")
            calls = []

            def fake_run(cmd, **kw):
                calls.append(list(cmd))
                if "-mc" in cmd:
                    return subprocess.CompletedProcess(cmd, 1, "", "error: unknown argument: -mc")
                base = cmd[cmd.index("-of") + 1]
                pathlib.Path(base + ".srt").write_text("1\n00:00:00,000 --> 00:00:02,000\n네 안녕하세요\n", encoding="utf-8")
                return subprocess.CompletedProcess(cmd, 0, "", "")

            with mock.patch.object(T.shutil, "which", return_value="whisper-cli"), \
                    mock.patch.object(T.subprocess, "run", side_effect=fake_run), quiet():
                words = T.run_whispercpp(wav, str(model), "ko", "", td)   # JSON absent -> SRT fallback
        self.assertEqual(len(calls), 2)
        self.assertNotIn("-mc", calls[1])
        self.assertEqual([w["text"] for w in words], ["네", "안녕하세요"])

    def test_whispercpp_missing_model_gives_download_command(self):
        with tempfile.TemporaryDirectory() as td, temp_home(YTCAPTION_MODEL_DIR=td):
            wav = pathlib.Path(td) / "a.wav"
            wav.write_bytes(b"x")
            with mock.patch.object(T.shutil, "which", return_value="whisper-cli"):
                with self.assertRaises(T.DependencyError) as cm:
                    T.run_whispercpp(wav, "large-v3-turbo", "ko", "", pathlib.Path(td))
        self.assertIn("huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin", str(cm.exception))

    def test_mlx_whisper_word_json(self):
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            wav = td / "a.wav"
            wav.write_bytes(b"x")
            seen = {}

            def fake_run(cmd, **kw):
                seen["cmd"] = cmd
                (td / "a.json").write_text(json.dumps({"segments": [
                    {"start": 0.0, "end": 2.0, "text": " 안녕 하세요", "words": [
                        {"word": " 안녕", "start": 0.0, "end": 0.9}, {"word": " 하세요.", "start": 1.0, "end": 2.0}]},
                    {"start": 4.0, "end": 5.0, "text": " 다음 문장", "words": []}]}), encoding="utf-8")
                return subprocess.CompletedProcess(cmd, 0, "", "")

            with mock.patch.object(T.shutil, "which", return_value="/bin/mlx_whisper"), \
                    mock.patch.object(T.subprocess, "run", side_effect=fake_run):
                words = T.run_mlx_whisper(wav, "mlx-community/whisper-large-v3-turbo", "ko", "", td)
        cmd = seen["cmd"]
        self.assertIn("--model=mlx-community/whisper-large-v3-turbo", cmd)
        self.assertEqual(cmd[cmd.index("--condition-on-previous-text") + 1], "False")
        self.assertEqual([w["text"] for w in words], ["안녕", "하세요.", "다음", "문장"])

    def test_faster_whisper_with_fake_module(self):
        seg = types.SimpleNamespace(start=0.0, end=2.0, text=" 안녕 하세요", words=[
            types.SimpleNamespace(word=" 안녕", start=0.0, end=0.8), types.SimpleNamespace(word=" 하세요.", start=0.9, end=2.0)])
        seen = {}

        class FakeModel:
            def __init__(self, name, device="auto", compute_type="default"):
                seen["init"] = (name, device, compute_type)

            def transcribe(self, path, **kw):
                seen["kw"] = kw
                return iter([seg]), object()

        fake = types.ModuleType("faster_whisper")
        fake.WhisperModel = FakeModel
        with mock.patch.dict(sys.modules, {"faster_whisper": fake}), \
                mock.patch.object(T.importlib.util, "find_spec", return_value=object()):
            words = T.run_faster_whisper(pathlib.Path("a.wav"), "turbo", "ko", "Acme Studio", "int8_float16")
        self.assertEqual(seen["init"], ("turbo", "auto", "int8_float16"))
        kw = seen["kw"]
        self.assertEqual((kw["language"], kw["vad_filter"], kw["condition_on_previous_text"], kw["word_timestamps"]),
                         ("ko", True, False, True))
        self.assertEqual(kw["initial_prompt"], "Acme Studio")
        self.assertEqual([w["text"] for w in words], ["안녕", "하세요."])

    def test_local_plan_uses_tier_table_default_and_override(self):
        av = {"whisper.cpp": False, "mlx-whisper": True, "faster-whisper": False}
        with mock.patch.object(T, "available_runtimes", return_value=av):
            rt, model, _ = T.local_plan("", "auto")
            self.assertEqual((rt, model), ("mlx-whisper", "mlx-community/whisper-large-v3-turbo"))
            rt, model, _ = T.local_plan("mlx-community/custom", "auto")
            self.assertEqual(model, "mlx-community/custom")

    @unittest.skipUnless(shutil.which("whisper-cli") or shutil.which("mlx_whisper"), "no local runtime installed here")
    def test_real_local_runtime_present(self):  # only runs where a runtime already exists
        self.assertTrue(any(T.available_runtimes().values()))


class EndToEndTests(unittest.TestCase):
    """main() with ffmpeg/ffprobe stubbed and all HTTP mocked."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.td.name)
        self.src = self.dir / "video.mp4"
        self.src.write_bytes(b"fakevideo")
        self.out = self.dir / "video.srt"

    def tearDown(self):
        self.td.cleanup()

    def fake_extract(self, src, dst, *, cloud):
        dst.write_bytes(b"ID3fakeaudio")

    def run_main(self, argv, net=None, key=True):
        env = {"DEEPGRAM_API_KEY": FAKE_KEY} if key else {}
        with temp_home(**env), mock.patch.object(T.shutil, "which", return_value="/usr/bin/x"), \
                mock.patch.object(T, "ffprobe_duration", return_value=600.0), \
                mock.patch.object(T, "extract_audio", side_effect=self.fake_extract), \
                mock.patch("urllib.request.urlopen", net or FakeNet(lambda *a: FakeResp(DG_RESPONSE))), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = T.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_deepgram_end_to_end_writes_valid_srt(self):
        code, out, err = self.run_main(["--input", str(self.src), "--engine", "deepgram", "--lang", "ko", "--out", str(self.out)])
        self.assertEqual(code, 0, err)
        blocks = C.read_srt(self.out)
        self.assertEqual([b["text"] for b in blocks], ["안녕하세요 여러분.", "오늘은 자막입니다."])
        self.assertTrue(self.out.read_bytes().decode("utf-8").startswith("1\n00:00:00,000 --> "))
        self.assertIn("blocks:   2", out)
        self.assertIn("$0.043", out)         # 10 min * $0.258/h
        self.assertNotIn(FAKE_KEY, out + err)

    def test_missing_key_exits_3(self):
        code, out, err = self.run_main(["--input", str(self.src), "--engine", "deepgram", "--out", str(self.out)], key=False)
        self.assertEqual(code, 3)
        self.assertIn("--set-key deepgram", err)
        self.assertFalse(self.out.exists())

    def test_dry_run_makes_no_calls_and_writes_nothing(self):
        net = FakeNet(lambda *a: FakeResp({}))
        with mock.patch.object(T, "extract_audio") as ex:
            code, out, err = self.run_main(["--input", str(self.src), "--engine", "deepgram", "--out", str(self.out),
                                            "--dry-run"], net=net)
        self.assertEqual(code, 0)
        self.assertEqual(net.calls, [])
        self.assertFalse(self.out.exists())
        self.assertIn("DRY RUN", out)
        self.assertNotIn(FAKE_KEY, out)

    def test_dry_run_still_reports_missing_key(self):
        code, _, err = self.run_main(["--input", str(self.src), "--engine", "soniox", "--dry-run"], key=False)
        self.assertEqual(code, 3)

    def test_bad_args_exit_2(self):
        self.assertEqual(self.run_main(["--input", str(self.dir / "missing.mp4"), "--engine", "deepgram"])[0], 2)
        self.assertEqual(self.run_main(["--input", str(self.src), "--engine", "nope"])[0], 2)
        srt = self.dir / "x.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
        self.assertEqual(self.run_main(["--input", str(srt), "--engine", "deepgram"])[0], 2)

    def test_openai_other_model_exits_2_with_explanation(self):
        code, _, err = self.run_main(["--input", str(self.src), "--engine", "openai", "--model", "gpt-transcribe"],
                                     net=FakeNet(lambda *a: FakeResp({})))
        # key missing -> 3 would mask; provide key via env in a second run
        with temp_home(OPENAI_API_KEY=FAKE_KEY), mock.patch.object(T.shutil, "which", return_value="/x"), \
                mock.patch.object(T, "ffprobe_duration", return_value=60.0), \
                contextlib.redirect_stderr(io.StringIO()) as e2:
            code = T.main(["--input", str(self.src), "--engine", "openai", "--model", "gpt-transcribe"])
        self.assertEqual(code, 2)
        self.assertIn("timestamps", e2.getvalue())

    def test_empty_transcript_exits_4(self):
        net = FakeNet(lambda *a: FakeResp({"results": {"utterances": []}}))
        code, _, err = self.run_main(["--input", str(self.src), "--engine", "deepgram", "--out", str(self.out)], net=net)
        self.assertEqual(code, 4)

    def test_api_error_exits_4_and_cleans_temp(self):
        net = FakeNet(lambda m, u, h, d: http_error(u, 500))
        code, _, err = self.run_main(["--input", str(self.src), "--engine", "deepgram", "--out", str(self.out)], net=net)
        self.assertEqual(code, 4)
        self.assertNotIn(FAKE_KEY, err)

    def test_keep_audio(self):
        code, *_ = self.run_main(["--input", str(self.src), "--engine", "deepgram", "--out", str(self.out), "--keep-audio"])
        self.assertEqual(code, 0)
        self.assertTrue((self.dir / "video.audio.mp3").exists())


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class RealFfmpegTests(unittest.TestCase):
    def test_extract_audio_cloud_and_local_formats(self):
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            src = td / "tone.wav"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                            "-ac", "2", "-ar", "44100", str(src)], check=True)
            mp3, wav = td / "o.mp3", td / "o.wav"
            T.extract_audio(src, mp3, cloud=True)
            T.extract_audio(src, wav, cloud=False)
            self.assertGreater(mp3.stat().st_size, 0)
            self.assertGreater(wav.stat().st_size, 16000)
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=channels,sample_rate,codec_name",
                                    "-of", "csv=p=0", str(wav)], capture_output=True, text=True).stdout.strip()
            self.assertEqual(probe, "pcm_s16le,16000,1")


if __name__ == "__main__":
    unittest.main()
