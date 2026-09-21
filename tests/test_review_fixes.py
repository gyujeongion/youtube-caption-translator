"""Regression tests for the review-fix batches (items 1-16, S1-S10)."""
import contextlib
import http.client
import io
import json
import os
import pathlib
import re
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.parse
from unittest import mock

import support
from support import FAKE_KEY, FakeNet, FakeResp, quiet, temp_home

import _common as C
import check_setup as S
import set_localization as L
import transcribe as T
import upload_caption as U

SCRIPTS = support.SCRIPTS


def http_error(url, code, body=b"nope"):
    return urllib.error.HTTPError(url, code, "err", {}, io.BytesIO(body))


def tok(text, a, b):
    return {"text": text, "start_ms": a, "end_ms": b, "confidence": 0.9}


# ---------------------------------------------------------------- item 1
class SonioxTokenWordTests(unittest.TestCase):
    def test_japanese_tokens_with_pauses_give_several_blocks(self):
        # no leading whitespace anywhere; 1.5 s silence between phrases
        toks = [tok("こ", 0, 200), tok("れ", 200, 400), tok("は", 400, 600), tok("テ", 600, 800), tok("スト", 800, 1200),
                tok("です", 1200, 1600), tok("。", 1600, 1700),
                tok("次", 3200, 3400), tok("の", 3400, 3600), tok("文", 3600, 3900), tok("章", 3900, 4200), tok("。", 4200, 4300),
                tok("最", 5800, 6000), tok("後", 6000, 6400)]
        words = T.soniox_tokens_to_words(toks, "ja")
        self.assertEqual(len(words), len(toks))                      # one word per token
        blocks = C.segment_words(words, lang="ja")
        self.assertEqual([b["text"] for b in blocks], ["これはテストです。", "次の文章。", "最後"])
        self.assertAlmostEqual(blocks[0]["start"], 0.0)
        self.assertAlmostEqual(blocks[0]["end"], 1.7)
        self.assertAlmostEqual(blocks[1]["start"], 3.2)
        self.assertAlmostEqual(blocks[2]["end"], 6.8)     # 0.6 s of speech, padded to the 1 s minimum

    def test_korean_splits_on_punctuation_and_gaps_without_leading_spaces(self):
        toks = [tok("안", 0, 100), tok("녕", 100, 200), tok(".", 200, 250), tok("반", 260, 360), tok("갑", 360, 460),
                tok("습", 1500, 1600), tok("니", 1600, 1700)]
        words = T.soniox_tokens_to_words(toks, "ko")
        self.assertEqual([w["text"] for w in words], ["안녕.", "반갑", "습니"])   # punct split, then a >0.3 s gap split
        self.assertAlmostEqual(words[1]["end"], 0.46)

    def test_no_new_word_inside_a_contiguous_subword_run(self):
        toks = [tok(" Hel", 0, 100), tok("lo", 110, 200), tok(" world", 250, 400)]
        self.assertEqual([w["text"] for w in T.soniox_tokens_to_words(toks, "en")], ["Hello", "world"])

    def test_whitespace_only_token_separates_words(self):
        toks = [tok("a", 0, 100), tok(" ", 100, 110), tok("b", 120, 200)]
        self.assertEqual([w["text"] for w in T.soniox_tokens_to_words(toks, "ko")], ["a", "b"])


# ---------------------------------------------------------------- item 4 / 10 / 12
class HttpRobustnessTests(unittest.TestCase):
    def call(self, exc):
        net = FakeNet(lambda *a: exc)
        with mock.patch("urllib.request.urlopen", net):
            return T.http_request("https://api.example.com/x")

    def test_transport_errors_become_engine_error(self):
        for exc in (ConnectionResetError("reset"), socket.timeout("timed out"), TimeoutError("t"),
                    http.client.IncompleteRead(b"abc", 10), http.client.RemoteDisconnected("gone"),
                    urllib.error.URLError("dns")):
            with self.assertRaises(T.EngineError, msg=repr(exc)):
                self.call(exc)

    def test_read_failure_inside_response_is_wrapped(self):
        class Boom(FakeResp):
            def read(self):
                raise http.client.IncompleteRead(b"x", 5)
        with mock.patch("urllib.request.urlopen", FakeNet(lambda *a: Boom())):
            with self.assertRaises(T.EngineError):
                T.http_request("https://api.example.com/x")

    def test_main_exits_4_not_traceback(self):
        with tempfile.TemporaryDirectory() as td, temp_home(DEEPGRAM_API_KEY=FAKE_KEY):
            src = pathlib.Path(td) / "v.mp4"
            src.write_bytes(b"x")
            with mock.patch.object(T.shutil, "which", return_value="/x"), \
                    mock.patch.object(T, "ffprobe_duration", return_value=5.0), \
                    mock.patch.object(T, "extract_audio", side_effect=lambda s, d, cloud: d.write_bytes(b"a")), \
                    mock.patch("urllib.request.urlopen", FakeNet(lambda *a: ConnectionResetError("boom"))), \
                    quiet():
                self.assertEqual(T.main(["--input", str(src), "--engine", "deepgram", "--out", str(pathlib.Path(td) / "o.srt")]), 4)

    def test_soniox_cleanup_still_runs_when_polling_dies(self):
        state = {"n": 0}

        def handler(m, u, h, d):
            if m == "POST" and u.endswith("/files"):
                return FakeResp({"id": "f1"})
            if m == "POST":
                return FakeResp({"id": "t1"})
            if m == "GET":
                return ConnectionResetError("reset")
            return FakeResp(b"", 204)
        net = FakeNet(handler)
        with tempfile.TemporaryDirectory() as td, mock.patch("urllib.request.urlopen", net), \
                mock.patch.object(T.time, "sleep"), quiet():
            a = pathlib.Path(td) / "a.mp3"
            a.write_bytes(b"x")
            with self.assertRaises(T.EngineError):
                T.soniox_transcribe(a, FAKE_KEY, "ko", "stt-async-v5", "")
        deletes = [c["url"].rsplit("/", 2)[-2:] for c in net.calls if c["method"] == "DELETE"]
        self.assertEqual(len(deletes), 2)

    def test_auth_error_body_is_hidden(self):
        body = b'{"error":{"message":"Incorrect API key provided: sk-proj-****abcd"}}'
        for code in (401, 403):
            with mock.patch("urllib.request.urlopen", FakeNet(lambda m, u, h, d: http_error(u, code, body))):
                with self.assertRaises(T.EngineError) as cm:
                    T.http_request("https://api.openai.com/v1/audio/transcriptions")
            self.assertNotIn("sk-proj", str(cm.exception))
            self.assertNotIn("Incorrect", str(cm.exception))
            self.assertEqual(cm.exception.status, code)

    def test_other_error_bodies_still_shown(self):
        with mock.patch("urllib.request.urlopen", FakeNet(lambda m, u, h, d: http_error(u, 422, b"bad language"))):
            with self.assertRaises(T.EngineError) as cm:
                T.http_request("https://api.example.com/x")
        self.assertIn("bad language", str(cm.exception))

    def test_deepgram_400_with_keyterm_retries_once_without(self):
        calls = []

        def handler(m, u, h, d):
            calls.append(u)
            if "keyterm=" in u:
                return http_error(u, 400, b"keyterm unsupported")
            return FakeResp({"results": {"utterances": [{"start": 0, "end": 1, "transcript": "네",
                                                          "words": [{"word": "네", "start": 0, "end": 1}]}]}})
        with tempfile.TemporaryDirectory() as td, mock.patch("urllib.request.urlopen", FakeNet(handler)), quiet():
            a = pathlib.Path(td) / "a.mp3"
            a.write_bytes(b"x")
            words = T.deepgram_transcribe(a, FAKE_KEY, "ko", "nova-3", "Acme Studio")
        self.assertEqual(len(calls), 2)
        self.assertIn("keyterm=", calls[0])
        self.assertNotIn("keyterm=", calls[1])
        self.assertEqual(words[0]["text"], "네")

    def test_deepgram_400_without_keyterm_is_not_retried(self):
        net = FakeNet(lambda m, u, h, d: http_error(u, 400))
        with tempfile.TemporaryDirectory() as td, mock.patch("urllib.request.urlopen", net):
            a = pathlib.Path(td) / "a.mp3"
            a.write_bytes(b"x")
            with self.assertRaises(T.EngineError):
                T.deepgram_transcribe(a, FAKE_KEY, "ko", "nova-3", "")
        self.assertEqual(len(net.calls), 1)


# ---------------------------------------------------------------- item 5
class FasterWhisperFallbackTests(unittest.TestCase):
    def fake_module(self, plan):
        """plan: list of callables(device, compute_type) -> iterable of segments, or raise."""
        seen = []

        class FakeModel:
            def __init__(self, name, device="auto", compute_type="default"):
                seen.append((device, compute_type))
                self.idx = len(seen) - 1

            def transcribe(self, path, **kw):
                return plan[self.idx](), object()
        mod = types.ModuleType("faster_whisper")
        mod.WhisperModel = FakeModel
        return mod, seen

    def seg(self):
        w = types.SimpleNamespace(word=" 네", start=0.0, end=0.5)
        return [types.SimpleNamespace(start=0.0, end=0.5, text=" 네", words=[w])]

    def run_it(self, plan):
        mod, seen = self.fake_module(plan)
        with mock.patch.dict(sys.modules, {"faster_whisper": mod}), \
                mock.patch.object(T.importlib.util, "find_spec", return_value=object()), quiet():
            try:
                return T.run_faster_whisper(pathlib.Path("a.wav"), "turbo", "ko", "", "float16"), seen
            except T.EngineError as e:
                return e, seen

    def test_lazy_cuda_error_during_iteration_retries_on_cpu_int8(self):
        def broken():
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
            yield
        words, seen = self.run_it([broken, lambda: iter(self.seg())])
        self.assertEqual([w["text"] for w in words], ["네"])
        self.assertEqual(seen, [("auto", "float16"), ("cpu", "int8")])

    def test_constructor_error_retries_too(self):
        def ctor_fail():
            raise OSError("cudnn_ops64_9.dll missing")
        mod, seen = self.fake_module([ctor_fail, lambda: iter(self.seg())])
        orig = mod.WhisperModel

        class Failing(orig):
            def __init__(self, name, device="auto", compute_type="default"):
                super().__init__(name, device, compute_type)
                if device == "auto":
                    raise OSError("cudnn_ops64_9.dll missing")
        mod.WhisperModel = Failing
        with mock.patch.dict(sys.modules, {"faster_whisper": mod}), \
                mock.patch.object(T.importlib.util, "find_spec", return_value=object()), quiet():
            words = T.run_faster_whisper(pathlib.Path("a.wav"), "turbo", "ko", "", "float16")
        self.assertEqual(seen[-1], ("cpu", "int8"))
        self.assertEqual(words[0]["text"], "네")

    def test_both_fail_raises_engine_error_with_hint(self):
        def broken():
            raise RuntimeError("cuda boom")
            yield
        err, seen = self.run_it([broken, broken])
        self.assertIsInstance(err, T.EngineError)
        self.assertIn("references/stt-local.md", str(err))
        self.assertEqual(len(seen), 2)                    # exactly one retry

    def test_success_first_time_no_retry(self):
        words, seen = self.run_it([lambda: iter(self.seg())])
        self.assertEqual(len(seen), 1)


# ---------------------------------------------------------------- item 6
class WhisperCliDiscoveryTests(unittest.TestCase):
    def test_env_var_wins(self):
        with tempfile.TemporaryDirectory() as td, temp_home(YTCAPTION_WHISPER_CLI=str(pathlib.Path(td) / "wc")):
            exe = pathlib.Path(td) / "wc"
            exe.write_bytes(b"#!/bin/sh\n")
            with mock.patch.object(T.shutil, "which", return_value=None):
                self.assertEqual(T.find_whisper_cli(), str(exe))
                self.assertTrue(T.available_runtimes()["whisper.cpp"])

    def test_bad_env_var_is_ignored_with_warning(self):
        with temp_home(YTCAPTION_WHISPER_CLI="/nonexistent/whisper-cli"), \
                mock.patch.object(T.shutil, "which", return_value=None), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertIsNone(T.find_whisper_cli())
        self.assertIn("YTCAPTION_WHISPER_CLI", err.getvalue())

    def test_probes_cmake_build_dirs_relative_to_cwd(self):
        with tempfile.TemporaryDirectory() as td, temp_home():
            for rel in ("whisper.cpp/build/bin", "whisper.cpp/build/bin/Release"):
                d = pathlib.Path(td) / rel
                d.mkdir(parents=True, exist_ok=True)
            release = pathlib.Path(td) / "whisper.cpp/build/bin/Release/whisper-cli.exe"
            release.write_bytes(b"x")
            old = os.getcwd()
            os.chdir(td)
            try:
                with mock.patch.object(T.shutil, "which", return_value=None):
                    self.assertEqual(pathlib.Path(T.find_whisper_cli()).resolve(), release.resolve())
                    (pathlib.Path(td) / "whisper.cpp/build/bin/whisper-cli").write_bytes(b"x")
                    self.assertTrue(T.find_whisper_cli().endswith("whisper-cli"))     # bin/ preferred over Release/
            finally:
                os.chdir(old)

    def test_check_setup_sees_env_var_runtime(self):
        with tempfile.TemporaryDirectory() as td:
            exe = pathlib.Path(td) / "wc"
            exe.write_bytes(b"x")
            with temp_home(YTCAPTION_WHISPER_CLI=str(exe)), mock.patch.object(T.shutil, "which", return_value=None), \
                    mock.patch.object(S, "check_token", return_value={"file": "x", "exists": False, "valid": False,
                                                                     "channel_title": None, "channel_id": None, "error": None}):
                self.assertTrue(S.gather()["local_runtimes"]["whisper-cli"])


# ---------------------------------------------------------------- item 7 / 8 / 9 / 11
class LocalRunTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.td.name)
        self.src = self.dir / "v.mp4"
        self.src.write_bytes(b"x")
        self.out = self.dir / "o.srt"

    def tearDown(self):
        self.td.cleanup()

    def main(self, argv, avail=None, cached=False, tier_runtime="mlx-whisper", **env):
        avail = avail or {"whisper.cpp": False, "mlx-whisper": True, "faster-whisper": False}
        e = dict(env)
        e["HF_HOME"] = str(self.dir / "hf")
        if cached:
            (self.dir / "hf/hub/models--mlx-community--whisper-large-v3-turbo/snapshots/abc").mkdir(parents=True)
        out, err = io.StringIO(), io.StringIO()
        with temp_home(**e), mock.patch.object(T, "available_runtimes", return_value=avail), \
                mock.patch.object(T.shutil, "which", return_value="/x"), \
                mock.patch.object(T, "ffprobe_duration", return_value=30.0), \
                mock.patch.object(T, "extract_audio", side_effect=lambda s, d, cloud: d.write_bytes(b"w")), \
                mock.patch.object(T, "local_transcribe", return_value=[{"start": 0, "end": 1, "text": "네"}]) as lt, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = T.main(argv)
        return code, out.getvalue(), err.getvalue(), lt

    def base(self, *extra):
        return ["--input", str(self.src), "--engine", "local", "--runtime", "mlx-whisper", "--out", str(self.out), *extra]

    def test_uncached_download_needs_yes(self):
        code, out, err, lt = self.main(self.base())
        self.assertEqual(code, 3)
        self.assertIn("will download ~1610 MB", err)
        self.assertIn("Hugging Face", err)
        self.assertIn("--yes", err)
        lt.assert_not_called()
        self.assertFalse(self.out.exists())

    def test_yes_allows_download(self):
        code, out, err, lt = self.main(self.base("--yes"))
        self.assertEqual(code, 0, err)
        self.assertIn("will download ~1610 MB", err)
        lt.assert_called_once()

    def test_cached_model_needs_no_flag(self):
        code, out, err, lt = self.main(self.base(), cached=True)
        self.assertEqual(code, 0, err)
        self.assertNotIn("will download", err)

    def test_dry_run_states_download_size(self):
        code, out, err, lt = self.main(self.base("--dry-run"))
        self.assertIn("will download ~1610 MB", out)
        self.assertEqual(code, 3)                          # the real run would refuse without --yes
        code, out, err, lt = self.main(self.base("--dry-run", "--yes"))
        self.assertEqual(code, 0)
        self.assertIn("will download ~1610 MB", out)
        lt.assert_not_called()

    def test_faster_whisper_repo_mapping(self):
        self.assertEqual(T.hf_repo_for("faster-whisper", "turbo"), "mobiuslabsgmbh/faster-whisper-large-v3-turbo")
        self.assertEqual(T.hf_repo_for("faster-whisper", "large-v3"), "Systran/faster-whisper-large-v3")
        self.assertIsNone(T.hf_repo_for("faster-whisper", str(self.dir)))       # local path: never downloads
        with temp_home(HF_HOME=str(self.dir / "hf")):
            self.assertFalse(T.model_cached("faster-whisper", "turbo"))
            (self.dir / "hf/hub/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/snapshots/x").mkdir(parents=True)
            self.assertTrue(T.model_cached("faster-whisper", "turbo"))

    def test_whispercpp_is_not_gated_by_yes(self):
        avail = {"whisper.cpp": True, "mlx-whisper": False, "faster-whisper": False}
        code, *_ = self.main(["--input", str(self.src), "--engine", "local", "--runtime", "whisper.cpp",
                              "--out", str(self.out)], avail=avail)
        self.assertEqual(code, 0)

    def test_language_base_code_for_local(self):
        seen = {}
        with mock.patch.object(T, "run_whispercpp", side_effect=lambda w, m, l, p, d: seen.setdefault("l", l) and []):
            T.local_transcribe(pathlib.Path("a.wav"), "whisper.cpp", "m", "pt-BR", "", "int8", self.dir)
        self.assertEqual(seen["l"], "pt")
        with mock.patch.object(T, "run_mlx_whisper", side_effect=lambda w, m, l, p, d: seen.update(m=l) or []):
            T.local_transcribe(pathlib.Path("a.wav"), "mlx-whisper", "m", "zh-TW", "", "int8", self.dir)
        self.assertEqual(seen["m"], "zh")

    def test_deepgram_keeps_full_locale(self):
        net = FakeNet(lambda m, u, h, d: FakeResp({"results": {"utterances": []}}))
        with mock.patch("urllib.request.urlopen", net):
            T.deepgram_transcribe(self.src, FAKE_KEY, "pt-BR", "nova-3", "")
        self.assertIn("language=pt-BR", net.calls[0]["url"])

    def test_mlx_argv_uses_equals_form_for_dash_values(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            (self.dir / "a.json").write_text(json.dumps({"segments": []}), encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        wav = self.dir / "a.wav"
        wav.write_bytes(b"x")
        with mock.patch.object(T.shutil, "which", return_value="/bin/mlx_whisper"), \
                mock.patch.object(T.subprocess, "run", side_effect=fake_run):
            T.run_mlx_whisper(wav, "-weird-model", "ko", "-dash prompt", self.dir)
        self.assertIn("--initial-prompt=-dash prompt", seen["cmd"])
        self.assertIn("--model=-weird-model", seen["cmd"])
        self.assertNotIn("--initial-prompt", seen["cmd"])

    def test_subprocess_output_is_decoded_as_utf8(self):
        seen = {}
        with mock.patch.object(T.subprocess, "run", side_effect=lambda cmd, **kw: seen.update(kw) or
                               subprocess.CompletedProcess(cmd, 0, "", "")):
            T._run(["x"])
        self.assertEqual((seen["encoding"], seen["errors"]), ("utf-8", "replace"))


class OverwriteTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.td.name)
        self.src = self.dir / "v.mp4"
        self.src.write_bytes(b"x")

    def tearDown(self):
        self.td.cleanup()

    def run_main(self, argv):
        net = FakeNet(lambda *a: FakeResp({"results": {"utterances": [{"start": 0, "end": 1, "transcript": "네",
                                                                        "words": [{"word": "네", "start": 0, "end": 1}]}]}}))
        with temp_home(DEEPGRAM_API_KEY=FAKE_KEY), mock.patch.object(T.shutil, "which", return_value="/x"), \
                mock.patch.object(T, "ffprobe_duration", return_value=5.0), \
                mock.patch.object(T, "extract_audio", side_effect=lambda s, d, cloud: d.write_bytes(b"a")), \
                mock.patch("urllib.request.urlopen", net), quiet(), contextlib.redirect_stdout(io.StringIO()):
            return T.main(argv), net

    def test_existing_out_refused_then_forced(self):
        out = self.dir / "o.srt"
        out.write_text("precious editor SRT", encoding="utf-8")
        code, net = self.run_main(["--input", str(self.src), "--engine", "deepgram", "--out", str(out)])
        self.assertEqual(code, 2)
        self.assertEqual(net.calls, [])                                   # refused before any API call/cost
        self.assertEqual(out.read_text(encoding="utf-8"), "precious editor SRT")
        code, _ = self.run_main(["--input", str(self.src), "--engine", "deepgram", "--out", str(out), "--force"])
        self.assertEqual(code, 0)
        self.assertNotIn("precious", out.read_text(encoding="utf-8"))

    def test_default_out_obeys_the_same_rule(self):
        default = self.dir / "v.stt.srt"
        default.write_text("keep", encoding="utf-8")
        code, _ = self.run_main(["--input", str(self.src), "--engine", "deepgram"])
        self.assertEqual(code, 2)
        self.assertEqual(default.read_text(encoding="utf-8"), "keep")

    def test_existing_kept_audio_also_needs_force(self):
        (self.dir / "o.audio.mp3").write_bytes(b"old")
        code, _ = self.run_main(["--input", str(self.src), "--engine", "deepgram", "--out", str(self.dir / "o.srt"),
                                 "--keep-audio"])
        self.assertEqual(code, 2)

    def test_dry_run_also_reports_existing_output(self):
        out = self.dir / "o.srt"
        out.write_text("x")
        code, _ = self.run_main(["--input", str(self.src), "--engine", "deepgram", "--out", str(out), "--dry-run"])
        self.assertEqual(code, 2)


# ---------------------------------------------------------------- items 2, 15, 16 + S5
class WritePrivateTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "posix permissions")
    def test_existing_world_readable_file_becomes_600(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "t.json"
            p.write_text("old")
            p.chmod(0o644)
            C.write_private(p, "new")
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
            self.assertEqual(p.read_text(), "new")

    @unittest.skipUnless(os.name == "posix", "posix permissions")
    def test_config_dir_created_private(self):
        with tempfile.TemporaryDirectory() as td:
            home = pathlib.Path(td) / "a" / "cfg"
            with temp_home(YTCAPTION_HOME=str(home)):
                C.set_env_key("DEEPGRAM_API_KEY", "abcdefgh1")
                self.assertEqual(stat.S_IMODE(home.stat().st_mode), 0o700)

    def test_token_path_expands_user(self):
        with temp_home():
            self.assertEqual(U.token_path("~/x/tok.json"), pathlib.Path.home() / "x" / "tok.json")
            self.assertEqual(U.token_path("bare.json"), C.config_dir() / "bare.json")


class ReauthTests(unittest.TestCase):
    def setup_files(self, d):
        (d / "cs.json").write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "csec"}}), encoding="utf-8")

    def run_reauth(self, paths_for, timeout=600.0, expect=None):
        import reauth_channel as R
        d = None
        with temp_home() as d:
            self.setup_files(d)
            tokfile = d / "tok.json"
            tokfile.write_text("{}")
            if os.name == "posix":
                tokfile.chmod(0o644)
            seen = {}

            def fake_open(url):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                redirect = q["redirect_uri"][0]
                seen["redirect"] = redirect
                port = urllib.parse.urlparse(redirect).port
                state = q["state"][0]

                def hit():
                    for path in paths_for(state):
                        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                        c.request("GET", path)
                        c.getresponse().read()
                        c.close()
                threading.Thread(target=hit, daemon=True).start()
                return True

            def fake_urlopen(arg, data=None, timeout=None):
                if isinstance(arg, str):
                    seen["token_post"] = data.decode()
                    return FakeResp({"access_token": "AT", "refresh_token": "RT"})
                return FakeResp({"items": [{"id": "UC9", "snippet": {"title": "Chan"}}]})

            argv = ["reauth", "--token", "tok.json", "--secret", "cs.json"]
            out = io.StringIO()
            with mock.patch.object(sys, "argv", argv), mock.patch.object(R.webbrowser, "open", fake_open), \
                    mock.patch("urllib.request.urlopen", fake_urlopen), mock.patch.object(R, "AUTH_TIMEOUT_SECONDS", timeout), \
                    contextlib.redirect_stdout(out):
                try:
                    code = R.main()
                    err = None
                except SystemExit as e:
                    code, err = None, str(e)
            content = tokfile.read_text(encoding="utf-8")
            mode = stat.S_IMODE(tokfile.stat().st_mode) if os.name == "posix" else None
        return code, err, seen, content, mode, out.getvalue()

    def test_loopback_favicon_and_wrong_state_ignored_then_success(self):
        code, err, seen, content, mode, out = self.run_reauth(
            lambda st: ["/favicon.ico", "/?code=BAD&state=wrong", f"/?code=GOOD&state={st}"])
        self.assertEqual(code, 0, err)
        self.assertTrue(seen["redirect"].startswith("http://127.0.0.1:"))
        self.assertIn("code=GOOD", seen["token_post"])
        self.assertIn("state did not match", out)
        self.assertIn("Waiting up to 10 minutes", out)
        self.assertEqual(json.loads(content)["refresh_token"], "RT")
        if mode is not None:
            self.assertEqual(mode, 0o600)                   # item 2: was 0644 before, replaced atomically

    def test_denied_reports_clearly(self):
        code, err, *_ = self.run_reauth(lambda st: [f"/?error=access_denied&state={st}"])
        self.assertIsNone(code)
        self.assertIn("access_denied", err)

    def test_timeout_message(self):
        code, err, *_ = self.run_reauth(lambda st: [], timeout=0.2)
        self.assertIsNone(code)
        self.assertIn("Timed out", err)


# ---------------------------------------------------------------- item 14 + S7 + S8
class CheckSetupFixTests(unittest.TestCase):
    def run_set_key(self, getpass_side_effect=None, tty=True, windows=False):
        out, err = io.StringIO(), io.StringIO()
        with temp_home() as d, mock.patch.object(S.sys.stdin, "isatty", return_value=tty), \
                mock.patch.object(S.getpass, "getpass", side_effect=getpass_side_effect or (lambda *a, **k: FAKE_KEY)), \
                mock.patch.object(C, "is_windows", return_value=windows), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = S.main(["--set-key", "deepgram"])
            saved = (d / "ytcaption.env").exists()
        return code, out.getvalue(), err.getvalue(), saved

    def test_getpass_warning_is_an_error_never_echo(self):
        def warn(*a, **k):
            import warnings
            warnings.warn("Can not control echo on the terminal.", getpass_warning(), stacklevel=2)
            return FAKE_KEY
        code, out, err, saved = self.run_set_key(warn)
        self.assertEqual(code, 2)
        self.assertFalse(saved)
        self.assertNotIn(FAKE_KEY, out + err)

    def test_direct_getpass_warning_exception(self):
        code, out, err, saved = self.run_set_key(getpass_warning()("no echo control"))
        self.assertEqual((code, saved), (2, False))
        self.assertIn("Git Bash/mintty", err)

    def test_non_tty_message_mentions_real_terminals(self):
        code, out, err, saved = self.run_set_key(tty=False)
        self.assertEqual(code, 2)
        for word in ("PowerShell", "cmd", "Terminal", "Git Bash/mintty does not work"):
            self.assertIn(word, err)

    def test_windows_note_printed(self):
        code, out, err, saved = self.run_set_key(windows=True)
        self.assertEqual(code, 0)
        self.assertIn("Windows", out)
        self.assertIn("not restricted", out)
        self.assertNotIn(FAKE_KEY, out)
        code, out, err, saved = self.run_set_key(windows=False)
        self.assertNotIn("not restricted", out)


def getpass_warning():
    import getpass
    return getpass.GetPassWarning


class PythonCmdTests(unittest.TestCase):
    def test_derived_from_sys_executable(self):
        for exe, want in [("/usr/bin/python3", "python3"), ("C:\\Python312\\python.exe", "python"),
                          ("/opt/homebrew/bin/python3.14", "python3"), ("/x/venv/bin/python", "python"),
                          ("/weird/interp", "python3"), ("", "python3")]:
            with mock.patch.object(sys, "executable", exe), mock.patch.object(C, "is_windows", return_value=False):
                # pathlib on posix does not split backslashes; only assert the posix-shaped cases exactly
                got = C.python_cmd()
            if "\\" not in exe:
                self.assertEqual(got, want, exe)

    def test_windows_style_basename(self):
        with mock.patch.object(sys, "executable", "python.exe"), mock.patch.object(C, "is_windows", return_value=False):
            self.assertEqual(C.python_cmd(), "python")

    def test_next_steps_never_hardcode_python3(self):
        with mock.patch.object(sys, "executable", "/usr/bin/python"), mock.patch.object(C, "is_windows", return_value=False):
            with temp_home() as d:
                with mock.patch.object(S, "check_token", return_value={"file": "x", "exists": False, "valid": False,
                                                                       "channel_title": None, "channel_id": None, "error": None}):
                    C.save_prefs({"input_mode": "stt_api", "stt_engine": "deepgram"})
                    i1 = S.gather()
                    C.save_prefs({"input_mode": "srt"})
                    (d / "client_secret.json").write_text("{}")
                    i2 = S.gather()
            self.assertIn("python scripts/check_setup.py --set-key deepgram", i1["next_step"])
            self.assertIn("python scripts/reauth_channel.py", i2["next_step"])
            for i in (i1, i2):
                self.assertNotIn("python3", i["next_step"])

    def test_ffmpeg_message_has_no_winget(self):
        msg = C.ffmpeg_missing_message()
        self.assertNotIn("winget", msg)
        for needle in ("https://ffmpeg.org/download.html", "brew install ffmpeg", "apt install ffmpeg", "choco install ffmpeg"):
            self.assertIn(needle, msg)
        with mock.patch.object(S.shutil, "which", side_effect=lambda n: None if n in ("ffmpeg", "ffprobe") else "/x"), \
                temp_home(), mock.patch.object(S, "check_token", return_value={"file": "x", "exists": False, "valid": False,
                                                                              "channel_title": None, "channel_id": None, "error": None}):
            C.save_prefs({"input_mode": "stt_api", "stt_engine": "deepgram"})
            self.assertIn("ffmpeg.org", S.gather()["next_step"])


class ExitSemanticsTests(unittest.TestCase):
    def main_with(self, prefs, token_valid, keys=None, runtimes=None):
        tok_ = {"file": "x", "exists": True, "valid": token_valid, "channel_title": "C" if token_valid else None,
                "channel_id": "U" if token_valid else None, "error": None if token_valid else "bad"}
        rt = runtimes or {"whisper.cpp": False, "mlx-whisper": False, "faster-whisper": False}
        out = io.StringIO()
        with temp_home(**{C.KEY_VARS[k]: FAKE_KEY for k in (keys or [])}) as d:
            (d / "client_secret.json").write_text("{}")
            C.save_prefs(prefs)
            with mock.patch.object(S, "check_token", return_value=tok_), \
                    mock.patch.object(S.T, "available_runtimes", return_value=rt), \
                    mock.patch.object(S.shutil, "which", return_value="/x"), contextlib.redirect_stdout(out):
                code = S.main([])
        return code, out.getvalue()

    def test_srt_or_unset_only_needs_token(self):
        self.assertEqual(self.main_with({"input_mode": "srt"}, True)[0], 0)
        self.assertEqual(self.main_with({"publish_mode": "review"}, True)[0], 0)
        self.assertEqual(self.main_with({"input_mode": "srt"}, False)[0], 1)

    def test_stt_api_requires_key(self):
        self.assertEqual(self.main_with({"input_mode": "stt_api", "stt_engine": "deepgram"}, True)[0], 1)
        self.assertEqual(self.main_with({"input_mode": "stt_api", "stt_engine": "deepgram"}, True, keys=["deepgram"])[0], 0)
        self.assertEqual(self.main_with({"input_mode": "stt_api", "stt_engine": "deepgram"}, False, keys=["deepgram"])[0], 1)

    def test_stt_local_requires_runtime(self):
        self.assertEqual(self.main_with({"input_mode": "stt_local"}, True)[0], 1)
        rt = {"whisper.cpp": True, "mlx-whisper": False, "faster-whisper": False}
        self.assertEqual(self.main_with({"input_mode": "stt_local"}, True, runtimes=rt)[0], 0)

    def test_input_line_printed(self):
        _, out = self.main_with({"input_mode": "srt"}, True)
        self.assertIn("Input: SRT always works; fallback when there is no SRT: none (ready)", out)
        _, out = self.main_with({"input_mode": "stt_api", "stt_engine": "deepgram"}, True)
        self.assertIn("fallback when there is no SRT: stt_api (not ready)", out)
        self.assertIn("[OK     ] YouTube token", out)


# ---------------------------------------------------------------- S1 / S2 legacy scripts
LEGACY = ["verify_srt.py", "flag_incomplete.py", "align_pairs.py", "frames_at.py", "extract_thumbnails.py",
          "set_localization.py", "upload_caption.py", "reauth_channel.py"]


def run_script(name, *args, encoding="cp1252"):
    env = {k: v for k, v in os.environ.items() if k not in support.SECRET_ENV}
    env["PYTHONIOENCODING"] = encoding       # simulate a legacy Windows console codepage
    env["PYTHONUTF8"] = "0"
    return subprocess.run([sys.executable, str(SCRIPTS / name), *args], capture_output=True, env=env)


class LegacyScriptTests(unittest.TestCase):
    def test_help_works_under_cp1252_and_ascii(self):
        for name in LEGACY + ["check_setup.py", "transcribe.py", "detect_hardware.py"]:
            for enc in ("cp1252", "ascii"):
                r = run_script(name, "--help", encoding=enc)
                self.assertEqual(r.returncode, 0, (name, enc, r.stderr.decode("utf-8", "replace")[-300:]))
                self.assertNotIn(b"Traceback", r.stderr, name)

    def test_verify_srt_pass_exits_0_with_checkmark_on_legacy_console(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "a.srt"
            p.write_text("1\n00:00:01,000 --> 00:00:02,000\n안녕\n", encoding="utf-8")
            for enc in ("cp1252", "ascii"):
                r = run_script("verify_srt.py", str(p), str(p), encoding=enc)
                self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
                self.assertIn("✓".encode("utf-8"), r.stdout)

    def test_align_pairs_with_korean_text_on_legacy_console(self):
        with tempfile.TemporaryDirectory() as td:
            a, b = pathlib.Path(td) / "a.srt", pathlib.Path(td) / "b.srt"
            a.write_text("1\n00:00:01,000 --> 00:00:02,000\n안녕하세요\n", encoding="utf-8")
            b.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n", encoding="utf-8")
            r = run_script("align_pairs.py", str(a), str(b), encoding="cp1252")
            self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
            out = r.stdout.decode("utf-8")
            self.assertIn("# Translation review table", out)
            self.assertIn("SRC: 안녕하세요", out)
            self.assertIn("TGT: Hello", out)

    def test_no_korean_prose_left_in_legacy_scripts(self):
        # flag_incomplete keeps Korean ONLY as data (endings, examples) -- checked separately
        for name in ["verify_srt.py", "align_pairs.py", "frames_at.py", "extract_thumbnails.py",
                     "set_localization.py", "upload_caption.py", "reauth_channel.py"]:
            text = (SCRIPTS / name).read_text(encoding="utf-8")
            self.assertIsNone(re.search("[\uac00-\ud7a3]", text), name)
        fi = (SCRIPTS / "flag_incomplete.py").read_text(encoding="utf-8")
        head = fi.split('"""')[1]
        self.assertIn("--merged", head)
        self.assertNotIn("--srt-merged", head)
        self.assertIn("Korean sources only", head)
        for line in fi.splitlines():
            if re.search("[\uac00-\ud7a3]", line):
                self.assertTrue(re.search(r'["\'`]', line) or line.strip().startswith(("#", "->", "between")), line)

    def test_flag_incomplete_skips_non_korean_source(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "en.srt"
            p.write_text("1\n00:00:01,000 --> 00:00:02,000\nThis is English and it goes on and\n", encoding="utf-8")
            r = run_script("flag_incomplete.py", str(p))
            self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
            self.assertEqual(r.stdout.decode("utf-8").strip(), "Korean-source heuristics only; skipping")
            k = pathlib.Path(td) / "ko.srt"
            k.write_text("1\n00:00:01,000 --> 00:00:02,000\n형이 자주 플레이하는 거 중에\n", encoding="utf-8")
            r = run_script("flag_incomplete.py", str(k))
            self.assertEqual(r.returncode, 0)
            self.assertIn("need checking", r.stdout.decode("utf-8"))
            self.assertIn("[strong]", r.stdout.decode("utf-8"))

    def test_flag_incomplete_help_states_korean_only(self):
        r = run_script("flag_incomplete.py", "--help", encoding="cp1252")
        self.assertIn("Korean sources only", " ".join(r.stdout.decode("utf-8").split()))

    def test_frames_at_rejects_url_fast(self):
        r = run_script("frames_at.py", "--video", "https://example.com/v.mp4", "--out", "x", "--sample", "2")
        self.assertNotEqual(r.returncode, 0)
        err = r.stderr.decode("utf-8", "replace")
        self.assertIn("LOCAL file", err)
        self.assertNotIn("Traceback", err)
        self.assertNotIn("ffprobe", err)


# ---------------------------------------------------------------- S3 / S4 / S9
def make_video(title="원제", loc=None, default_lang="en"):
    return {"id": "VID", "snippet": {"title": title, "description": "desc", "categoryId": "22", "tags": ["a"],
                                      "defaultLanguage": default_lang, "defaultAudioLanguage": None},
            "localizations": loc or {}}


class SetLocalizationTests(unittest.TestCase):
    def run_main(self, argv, video=None, prefs=None):
        put = {}

        def handler(m, u, h, d):
            if m == "GET":
                return FakeResp({"items": [video or make_video()]})
            body = json.loads(d)
            put["body"] = body
            return FakeResp({"snippet": body["snippet"], "localizations": body["localizations"]})
        net = FakeNet(handler)
        out = io.StringIO()
        with temp_home() as d, mock.patch.object(L, "access_token", return_value="AT") as at, \
                mock.patch("urllib.request.urlopen", net), contextlib.redirect_stdout(out):
            if prefs:
                C.save_prefs(prefs)
            try:
                code = L.main(argv)
            except SystemExit as e:
                code = e.code
        return code, put.get("body"), net, at, out.getvalue()

    def test_multiple_titles_and_no_suffix(self):
        code, body, net, at, out = self.run_main(["--video", "VID", "--title", "en=My English = Title",
                                                  "--title", "ja=日本語タイトル"])
        self.assertEqual(code, 0)
        loc = body["localizations"]
        self.assertEqual(loc["en"]["title"], "My English = Title")      # split on the first '=' only, verbatim
        self.assertEqual(loc["ja"]["title"], "日本語タイトル")
        self.assertEqual(loc["en"]["description"], "desc")
        self.assertEqual(body["snippet"]["title"], "원제")               # default title untouched
        self.assertEqual(body["snippet"]["defaultLanguage"], "ko")       # trap corrected (en -> ko)
        at.assert_called_once_with(C.DEFAULT_PREFS["token_file"])

    def test_en_title_alias(self):
        _, body, *_ = self.run_main(["--video", "VID", "--en-title", "Only EN"])
        self.assertEqual(body["localizations"]["en"]["title"], "Only EN")

    def test_alias_and_title_en_conflict_rejected(self):
        code, body, net, *_ = self.run_main(["--video", "VID", "--en-title", "A", "--title", "en=B"])
        self.assertIn("more than once", str(code))
        self.assertEqual(net.calls, [])

    def test_bad_title_format_rejected_before_network(self):
        for bad in ("Title without equals", "en=", "=text", "not a lang=x"):
            code, body, net, *_ = self.run_main(["--video", "VID", "--title", bad])
            self.assertIn("error", str(code), bad)
            self.assertEqual(net.calls, [], bad)

    def test_original_language_from_prefs_and_flag(self):
        _, body, *_ = self.run_main(["--video", "VID", "--title", "en=X"], prefs={"source_language": "ja"})
        self.assertEqual(body["snippet"]["defaultLanguage"], "ja")
        _, body, *_ = self.run_main(["--video", "VID", "--title", "en=X", "--original-language", "fr"],
                                    prefs={"source_language": "ja"})
        self.assertEqual(body["snippet"]["defaultLanguage"], "fr")
        _, body, *_ = self.run_main(["--video", "VID", "--title", "en=X", "--keep-default-language"])
        self.assertEqual(body["snippet"]["defaultLanguage"], "en")

    def test_existing_localizations_preserved_and_token_from_prefs(self):
        video = make_video(loc={"de": {"title": "Deutsch", "description": "d"}, "en": {"title": "old", "description": "custom"}})
        _, body, net, at, _ = self.run_main(["--video", "VID", "--title", "en=new"], video=video,
                                            prefs={"token_file": "other_token.json"})
        self.assertEqual(body["localizations"]["de"]["title"], "Deutsch")
        self.assertEqual(body["localizations"]["en"], {"title": "new", "description": "custom"})
        at.assert_called_once_with("other_token.json")

    def test_explicit_token_and_show_makes_no_put(self):
        code, body, net, at, out = self.run_main(["--video", "VID", "--token", "t.json", "--show"])
        self.assertIsNone(body)
        at.assert_called_once_with("t.json")
        self.assertEqual([c["method"] for c in net.calls], ["GET"])

    def test_no_title_is_an_error(self):
        code, body, *_ = self.run_main(["--video", "VID"])
        self.assertIn("need --title", str(code))


class UploadCaptionTests(unittest.TestCase):
    def run_main(self, argv, tracks=None, prefs=None):
        out = io.StringIO()
        sent = []
        with tempfile.TemporaryDirectory() as td, temp_home() as d, \
                mock.patch.object(U, "access_token", return_value="AT") as at, \
                mock.patch.object(U, "list_tracks", return_value=tracks or []), \
                mock.patch.object(U, "send", side_effect=lambda url, method, meta, srt, tok: sent.append(method) or {"id": "TID"}), \
                mock.patch.object(sys, "argv", ["upload_caption.py", *argv]), contextlib.redirect_stdout(out):
            srt = pathlib.Path(td) / "a.srt"
            srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n", encoding="utf-8")
            argv2 = [a.replace("SRT", str(srt)) for a in argv]
            sys.argv = ["upload_caption.py", *argv2]
            if prefs:
                C.save_prefs(prefs)
            code = U.main()
        return code, out.getvalue(), sent, at

    def test_token_optional_default_and_prefs(self):
        code, out, sent, at = self.run_main(["--video", "V", "--srt", "SRT", "--lang", "en"])
        at.assert_called_once_with("my_channel_token.json")
        code, out, sent, at = self.run_main(["--video", "V", "--srt", "SRT"], prefs={"token_file": "mine.json"})
        at.assert_called_once_with("mine.json")
        code, out, sent, at = self.run_main(["--video", "V", "--srt", "SRT", "--token", "x.json"])
        at.assert_called_once_with("x.json")

    def test_quota_estimates(self):
        _, out, sent, _ = self.run_main(["--video", "V", "--srt", "SRT", "--lang", "en"])
        self.assertEqual(sent, ["POST"])
        self.assertIn("quota used this run ~450 units", out)
        _, out, sent, _ = self.run_main(["--video", "V", "--srt", "SRT", "--lang", "en"],
                                        tracks=[{"id": "T1", "snippet": {"language": "en"}}])
        self.assertEqual(sent, ["PUT"])
        self.assertIn("quota used this run ~500 units", out)
        _, out, sent, _ = self.run_main(["--video", "V", "--list"])
        self.assertIn("quota used this run ~50 units", out)

    def test_dry_run_uploads_nothing(self):
        _, out, sent, _ = self.run_main(["--video", "V", "--srt", "SRT", "--lang", "en", "--dry-run"])
        self.assertEqual(sent, [])
        self.assertIn("DRY RUN", out)
        self.assertIn("~450", out)


if __name__ == "__main__":
    unittest.main()
