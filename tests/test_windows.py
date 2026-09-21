"""Windows-hardening tests.

There is no Windows machine in the loop, so these tests (1) exercise the real code paths that break on
Windows with real files in Hangul + space directory names and legacy console encodings, and (2) simulate
Windows by patching `_common.is_windows` / `detect_hardware._os_name` / subprocess results (never
`os.name`: pathlib refuses to change flavour). The GitHub Actions matrix runs the same file on real Windows.
"""
from __future__ import annotations

import contextlib
import ctypes
import io
import json
import ntpath
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import unittest.mock as mock

import support
from support import FAKE_KEY, temp_home

import _common as C
import check_setup as S
import detect_hardware as H
import extract_thumbnails as E
import frames_at as F
import reauth_channel as R
import transcribe as T
import upload_caption as U

SCRIPTS = support.SCRIPTS
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
HANGUL_DIR = "한글 폴더 with spaces"      # Hangul + spaces
PCT_DIR = "100% 한글 (test)"             # '%' would break ffmpeg %02d patterns / yt-dlp templates
SRT_KO = "1\n00:00:01,000 --> 00:00:02,000\n안녕하세요\n\n2\n00:00:03,000 --> 00:00:04,000\nこんにちは\n"


@contextlib.contextmanager
def hangul_dir(name: str = HANGUL_DIR):
    with tempfile.TemporaryDirectory(prefix="yt ") as base:
        d = pathlib.Path(base) / name
        d.mkdir()
        yield d


def run_script(name: str, *args: str, encoding: str = "cp1252", env_extra: dict | None = None, cwd=None):
    """Run a script as a legacy-Windows-console user would (no UTF-8 mode, narrow stdout codepage)."""
    env = {k: v for k, v in os.environ.items() if k not in support.SECRET_ENV}
    env.update({"PYTHONIOENCODING": encoding, "PYTHONUTF8": "0"})
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-X", "utf8=0", str(SCRIPTS / name), *args],
                          capture_output=True, env=env, cwd=cwd)


def text(b: bytes) -> str:
    return b.decode("utf-8", "replace")


def make_video(path: pathlib.Path, seconds: int = 3) -> None:
    r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                        f"testsrc=size=320x240:rate=10:duration={seconds}", "-c:v", "mpeg4", "-pix_fmt", "yuv420p",
                        "-y", str(path)], capture_output=True, text=True)
    if r.returncode != 0 or not path.is_file():
        raise unittest.SkipTest("ffmpeg cannot build a synthetic video: " + r.stderr[-200:])


# ============================================================ 1. console / file encoding
class ConsoleEncodingTests(unittest.TestCase):
    def test_every_script_help_survives_legacy_encodings(self):
        names = [p.name for p in sorted(SCRIPTS.glob("*.py")) if p.name != "_common.py"]
        self.assertGreaterEqual(len(names), 10)
        for name in names:
            for enc in ("cp1252", "cp949", "ascii"):
                r = run_script(name, "--help", encoding=enc)
                self.assertEqual(r.returncode, 0, (name, enc, text(r.stderr)[-300:]))
                self.assertNotIn(b"Traceback", r.stderr, (name, enc))
                self.assertTrue(r.stdout.strip(), (name, enc))

    def test_subcommand_help_and_bad_args_do_not_traceback(self):
        for args in (["grab", "--help"], ["gallery", "--help"]):
            r = run_script("extract_thumbnails.py", *args, encoding="cp1252")
            self.assertEqual(r.returncode, 0, text(r.stderr))
        r = run_script("verify_srt.py", encoding="ascii")            # no args: usage + exit 2, never a crash
        self.assertEqual(r.returncode, 2)
        self.assertNotIn(b"Traceback", r.stderr)

    def test_setup_utf8_io_makes_narrow_streams_safe(self):
        for enc in ("cp1252", "cp949", "ascii"):
            raw = io.BytesIO()
            wrapped = io.TextIOWrapper(raw, encoding=enc, errors="strict")
            with mock.patch.object(sys, "stdout", wrapped), mock.patch.object(sys, "stderr", io.TextIOWrapper(io.BytesIO(), encoding=enc)):
                C.setup_utf8_io()
                print("안녕하세요 こんにちは ✓ →")
                sys.stdout.flush()
            self.assertEqual(raw.getvalue().decode("utf-8").strip(), "안녕하세요 こんにちは ✓ →", enc)

    def test_korean_paths_and_prefs_print_on_a_cp1252_console(self):
        with hangul_dir() as home:
            r = run_script("check_setup.py", "--set-pref", "token_file=한글 토큰.json", encoding="cp1252",
                           env_extra={"YTCAPTION_HOME": str(home)})
            self.assertEqual(r.returncode, 0, text(r.stderr))
            r = run_script("check_setup.py", "--show-prefs", encoding="ascii", env_extra={"YTCAPTION_HOME": str(home)})
            self.assertEqual(r.returncode, 0, text(r.stderr))
            data = json.loads(text(r.stdout))
            self.assertEqual(data["prefs"]["token_file"], "한글 토큰.json")
            self.assertIn(HANGUL_DIR, data["path"])
            # the prefs file itself is BOM-less UTF-8 with LF only
            raw = (home / "ytcaption_prefs.json").read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
            self.assertNotIn(b"\r", raw)
            self.assertIn("한글 토큰.json", raw.decode("utf-8"))

    def test_verify_align_flag_on_legacy_console_with_korean_japanese(self):
        with hangul_dir() as d:
            a, b = d / "원본 파일.srt", d / "번역 파일.srt"
            a.write_bytes(SRT_KO.encode("utf-8"))
            b.write_bytes(SRT_KO.encode("utf-8"))
            r = run_script("verify_srt.py", str(a), str(b), encoding="cp1252")
            self.assertEqual(r.returncode, 0, text(r.stderr))
            self.assertIn("✓", text(r.stdout))
            table = d / "표 검토.md"
            r = run_script("align_pairs.py", str(a), str(b), "-o", str(table), encoding="ascii")
            self.assertEqual(r.returncode, 0, text(r.stderr))
            raw = table.read_bytes()
            self.assertNotIn(b"\r", raw)                        # LF on every OS (Path.write_text would give CRLF on Windows)
            self.assertIn("こんにちは", raw.decode("utf-8"))
            r = run_script("flag_incomplete.py", str(a), encoding="cp949")
            self.assertEqual(r.returncode, 0, text(r.stderr))

    def test_subprocess_helpers_never_use_the_locale_codepage(self):
        seen = {}

        def fake(cmd, **kw):
            seen.update(kw)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        with mock.patch.object(H.subprocess, "run", side_effect=fake):
            H._run(["x"])
        self.assertEqual((seen["encoding"], seen["errors"]), ("utf-8", "replace"))
        seen.clear()
        with mock.patch.object(T.subprocess, "run", side_effect=fake):
            T._run(["x"])
        self.assertEqual((seen["encoding"], seen["errors"]), ("utf-8", "replace"))
        self.assertEqual(seen["stdin"], subprocess.DEVNULL)

    def test_no_bare_text_mode_io_in_scripts(self):
        """Every text open()/read_text()/write_text() in scripts/ names its encoding (cp949/cp1252 default otherwise)."""
        bad = []
        for p in sorted(SCRIPTS.glob("*.py")):
            lines = p.read_text(encoding="utf-8").splitlines()
            for n, line in enumerate(lines):
                code = line.split("#")[0]
                window = " ".join(lines[n:n + 3])                                   # call may continue on the next lines
                if re.search(r"\.(read_text|write_text)\(", code) and "encoding" not in window:
                    bad.append(f"{p.name}:{n + 1}")
                if re.search(r"(?<![\w.])open\(", code) and "encoding" not in window and '"rb"' not in window \
                        and "'rb'" not in window and '"wb"' not in window:
                    bad.append(f"{p.name}:{n + 1}")
        self.assertEqual(bad, [])


# ============================================================ 4. SRT bytes: BOM / CRLF / encodings
class SrtBytesTests(unittest.TestCase):
    def test_utf8_bom_crlf_and_lone_cr(self):
        with hangul_dir() as d:
            p = d / "편집기 자막.srt"
            p.write_bytes(b"\xef\xbb\xbf" + SRT_KO.replace("\n", "\r\n").encode("utf-8"))
            blocks = C.read_srt(p)
            self.assertEqual([b["text"] for b in blocks], ["안녕하세요", "こんにちは"])
            self.assertEqual(blocks[0]["index"], "1")            # BOM did not stick to the first index
            p.write_bytes(SRT_KO.replace("\n", "\r").encode("utf-8"))   # classic-Mac line ends
            self.assertEqual(len(C.read_srt(p)), 2)

    def test_utf16_with_bom_from_notepad_unicode(self):
        with hangul_dir() as d:
            p = d / "utf16.srt"
            p.write_bytes(SRT_KO.replace("\n", "\r\n").encode("utf-16"))       # BOM + LE
            self.assertEqual(len(C.read_srt(p)), 2)
            p.write_bytes(b"\xfe\xff" + SRT_KO.encode("utf-16-be"))
            self.assertEqual(len(C.read_srt(p)), 2)

    def test_legacy_encoding_is_refused_with_a_fix_not_guessed(self):
        with hangul_dir() as d:
            p = d / "ansi.srt"
            p.write_bytes(SRT_KO.split("こ")[0].encode("cp949"))                 # Notepad 'ANSI' on Korean Windows
            with self.assertRaises(C.TextDecodeError) as cm:
                C.read_srt(p)
            self.assertIn("UTF-8", str(cm.exception))
            self.assertIsInstance(cm.exception, ValueError)
            (d / "nobom16.srt").write_bytes(SRT_KO.encode("utf-16-le"))
            with self.assertRaises(C.TextDecodeError):
                C.read_srt(d / "nobom16.srt")

    def test_clis_report_a_bad_encoding_cleanly(self):
        with hangul_dir() as d:
            a = d / "a.srt"
            a.write_bytes("1\n00:00:01,000 --> 00:00:02,000\n안녕\n".encode("cp949"))
            for script, args in (("verify_srt.py", [str(a), str(a)]), ("align_pairs.py", [str(a), str(a)]),
                                 ("flag_incomplete.py", [str(a)])):
                r = run_script(script, *args, encoding="cp1252")
                self.assertEqual(r.returncode, 2, (script, text(r.stderr)))
                self.assertIn("UTF-8", text(r.stderr))
                self.assertNotIn(b"Traceback", r.stderr)
            r = run_script("verify_srt.py", str(d / "missing.srt"), str(a))
            self.assertEqual(r.returncode, 2)
            self.assertNotIn(b"Traceback", r.stderr)

    def test_written_files_are_bom_less_lf(self):
        with hangul_dir() as d:
            blocks = [{"start": 1.0, "end": 2.0, "text": "안녕\nこんにちは"}]
            C.write_srt(d / "o.srt", blocks)
            raw = (d / "o.srt").read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
            self.assertNotIn(b"\r", raw)
            C.write_text_lf(d / "t.md", "a\nb\n")
            self.assertEqual((d / "t.md").read_bytes(), b"a\nb\n")

    def test_json_and_env_with_bom(self):
        with hangul_dir() as d, temp_home(YTCAPTION_HOME=str(d)):
            (d / "ytcaption_prefs.json").write_bytes(b"\xef\xbb\xbf" + json.dumps({"source_language": "ja"}).encode())
            self.assertEqual(C.load_prefs()["source_language"], "ja")
            (d / "ytcaption.env").write_bytes(b"\xef\xbb\xbfDEEPGRAM_API_KEY=abc12345\r\nOPENAI_API_KEY=xyz98765\r\n")
            self.assertEqual(C.get_key("deepgram"), "abc12345")   # BOM did not glue itself onto the variable name
            self.assertEqual(C.get_key("openai"), "xyz98765")     # CRLF did not stay on the value
            (d / "tok.json").write_bytes(b"\xef\xbb\xbf" + json.dumps({"refresh_token": "r"}).encode())
            self.assertEqual(C.read_json(d / "tok.json")["refresh_token"], "r")


# ============================================================ 3. paths
class PathTests(unittest.TestCase):
    def test_expand_path_windows_habits(self):
        with mock.patch.object(C, "is_windows", return_value=True), \
                mock.patch.dict(os.environ, {"USERPROFILE": r"C:\Users\홍길동", "MYDIR": r"D:\작업 폴더"}):
            self.assertEqual(str(C.expand_path('"D:\\a b\\c"')), "D:\\a b\\c")      # cmd `set X="..."` keeps quotes
            self.assertEqual(str(C.expand_path('D:\\a b"')), "D:\\a b")            # "D:\a b\" ate the closing quote
            self.assertEqual(str(C.expand_path("%USERPROFILE%\\x.srt")), "C:\\Users\\홍길동\\x.srt")
            self.assertEqual(str(C.expand_path("%MYDIR%\\자막.srt")), "D:\\작업 폴더\\자막.srt")
            self.assertEqual(str(C.expand_path("  spaced.srt  ")), "spaced.srt")

    def test_expand_path_is_inert_on_posix(self):
        with mock.patch.object(C, "is_windows", return_value=False), mock.patch.dict(os.environ, {"X": "y"}):
            # '$' is legal in POSIX names and must not be expanded (compare as Path: a real
            # WindowsPath prints backslashes, so a plain string compare fails on Windows runners)
            self.assertEqual(C.expand_path("$X/a.srt"), pathlib.Path("$X/a.srt"))
            self.assertEqual(C.expand_path("~/a.srt"), pathlib.Path.home() / "a.srt")

    def test_config_dir_default_and_override_with_quotes_and_vars(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("YTCAPTION_HOME", None)
            self.assertEqual(C.config_dir(), pathlib.Path.home() / ".claude" / "credentials")
        with mock.patch.object(C, "is_windows", return_value=True), \
                mock.patch.dict(os.environ, {"YTCAPTION_HOME": '"%TEMPX%\\ytcaption"', "TEMPX": r"C:\Temp"}):
            self.assertEqual(str(C.config_dir()), "C:\\Temp\\ytcaption")
            self.assertEqual(str(C.resolve_in_config("tok.json")).replace("/", "\\"), "C:\\Temp\\ytcaption\\tok.json")

    def test_windows_path_shapes_with_purewindowspath(self):
        pw = pathlib.PureWindowsPath
        self.assertTrue(pw(r"C:\Users\홍길동\a b\x.srt").is_absolute())
        self.assertFalse(pw(r"C:x.srt").is_absolute())                             # drive-relative: NOT absolute
        self.assertFalse(pw(r"\Users\x.srt").is_absolute())
        self.assertEqual(pw(r"C:\a\b.audio.mp3").stem, "b.audio")
        # config_dir / drive-relative or rooted names stay inside sane locations
        self.assertEqual(str(pw(r"C:\cfg") / "tok.json"), r"C:\cfg\tok.json")
        self.assertEqual(ntpath.normcase(r"C:\Python312\Python.EXE"), ntpath.normcase("c:/python312/python.exe"))

    def test_real_hangul_space_paths_roundtrip_through_config_and_tokens(self):
        with hangul_dir() as d, temp_home(YTCAPTION_HOME=str(d / "설정 폴더")):
            p = C.set_env_key("DEEPGRAM_API_KEY", "abcdefgh12")
            self.assertEqual(p.parent.name, "설정 폴더")
            self.assertEqual(C.get_key("deepgram"), "abcdefgh12")
            C.save_prefs({"source_language": "ko", "token_file": "내 채널.json"})
            self.assertEqual(U.token_path("내 채널.json"), d / "설정 폴더" / "내 채널.json")
            tok = d / "설정 폴더" / "내 채널.json"
            tok.write_text(json.dumps({"refresh_token": "r", "client_id": "c", "client_secret": "s"}), encoding="utf-8")
            calls = []

            def fake_urlopen(url, data=None, timeout=None):
                calls.append((url, data, timeout))
                return support.FakeResp({"access_token": "AT"})
            with mock.patch("urllib.request.urlopen", fake_urlopen):
                self.assertEqual(U.access_token("내 채널.json"), "AT")
            self.assertEqual(len(calls), 1)
            self.assertIn(b"refresh_token=r", calls[0][1])
            self.assertTrue(calls[0][2])                                              # has a timeout

    def test_media_arg_is_absolute_and_never_an_option(self):
        with hangul_dir() as d:
            old = os.getcwd()
            os.chdir(d)
            try:
                for name in ("-danger.mp4", "--x.wav", "-i"):
                    arg = C.media_arg(pathlib.Path(name))
                    self.assertTrue(os.path.isabs(arg), arg)
                    self.assertFalse(arg.startswith("-"), arg)
                    self.assertTrue(arg.endswith(name))
            finally:
                os.chdir(old)

    def test_ffmpeg_argv_uses_absolute_paths_and_no_paths_in_filters(self):
        seen = []
        with hangul_dir() as d:
            old = os.getcwd()
            os.chdir(d)
            try:
                with mock.patch.object(T.shutil, "which", return_value=r"C:\Program Files\ffmpeg\bin\ffmpeg.exe"), \
                        mock.patch.object(T.subprocess, "run",
                                          side_effect=lambda cmd, **kw: seen.append(cmd) or
                                          subprocess.CompletedProcess(cmd, 0, "", "")):
                    (d / "-x.mp3").write_bytes(b"x")
                    try:
                        T.extract_audio(pathlib.Path("-in.mp4"), pathlib.Path("-x.mp3"), cloud=True)
                    except T.EngineError:
                        pass                                                        # the fake ffmpeg produced nothing
                cwd = os.getcwd()
            finally:
                os.chdir(old)
        cmd = seen[0]
        self.assertEqual(cmd[0], r"C:\Program Files\ffmpeg\bin\ffmpeg.exe")         # PATHEXT-resolved, not a bare name
        self.assertEqual(cmd[cmd.index("-i") + 1], os.path.join(cwd, "-in.mp4"))     # absolute: cannot look like an option
        self.assertEqual(cmd[-1], os.path.join(cwd, "-x.mp3"))
        for a in cmd:                                                               # every '-...' is a real ffmpeg flag
            if a.startswith("-"):
                self.assertIn(a, {"-y", "-nostdin", "-loglevel", "-i", "-vn", "-ac", "-ar", "-c:a", "-b:a"}, a)

    def test_long_path_warning_only_on_windows(self):
        long = "C:\\" + "a" * 250 + "\\x.srt"
        with mock.patch.object(C, "is_windows", return_value=False):
            self.assertIsNone(C.path_length_warning(long))
        with mock.patch.object(C, "is_windows", return_value=True), \
                mock.patch.object(C.os.path, "abspath", side_effect=lambda p: p):
            self.assertIn("259", C.path_length_warning(long) or "")
            self.assertIsNone(C.path_length_warning("C:\\short\\x.srt"))

    def test_format_cmd_quotes_for_the_shell_family(self):
        argv = ["ffmpeg", "-i", "C:\\한글 폴더\\a b.mp4", "out file.png"]
        with mock.patch.object(C, "is_windows", return_value=True):
            self.assertEqual(C.format_cmd(argv), 'ffmpeg -i "C:\\한글 폴더\\a b.mp4" "out file.png"')
        with mock.patch.object(C, "is_windows", return_value=False):
            self.assertEqual(C.format_cmd(argv), "ffmpeg -i 'C:\\한글 폴더\\a b.mp4' 'out file.png'")


# ============================================================ 2. executables / interpreter advice
class ExecutableTests(unittest.TestCase):
    def which_map(self, table):
        return mock.patch.object(shutil, "which", side_effect=lambda n, *a, **k: table.get(n))

    def test_python_cmd_windows_variants(self):
        exe = r"C:\Python312\python.exe"
        with mock.patch.object(C, "is_windows", return_value=True), mock.patch.object(sys, "executable", exe):
            with self.which_map({"python": "c:/python312/PYTHON.exe"}):          # same interpreter, other spelling
                self.assertEqual(C.python_cmd(), "python")
            with self.which_map({"python": r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\python.exe", "py": r"C:\Windows\py.exe"}), \
                    mock.patch.object(sys, "base_prefix", sys.prefix):
                self.assertEqual(C.python_cmd(), f"py -{sys.version_info.major}.{sys.version_info.minor}")
            with self.which_map({}), mock.patch.object(sys, "base_prefix", sys.prefix):
                self.assertEqual(C.python_cmd(), f'"{exe}"')                       # nothing on PATH: full path
            with self.which_map({"py": r"C:\Windows\py.exe"}), mock.patch.object(sys, "base_prefix", "/base"):
                self.assertEqual(C.python_cmd(), f'"{exe}"')                       # venv: py would pick another Python
            for cmd in ("python", f"py -{sys.version_info.major}.{sys.version_info.minor}"):
                self.assertNotIn("python3", cmd)

    def test_python_cmd_windows_never_python3_even_for_python3_exe_name(self):
        with mock.patch.object(C, "is_windows", return_value=True), \
                mock.patch.object(sys, "executable", r"C:\Python312\python3.12.exe"), \
                self.which_map({"python": r"C:\Python312\python3.12.exe"}):
            self.assertEqual(C.python_cmd(), "python")                             # python.org installs have no python3.exe

    def test_store_stub_and_launcher_notes(self):
        stub = r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\python.exe"
        with mock.patch.object(C, "is_windows", return_value=True), mock.patch.object(sys, "executable", r"C:\Python312\python.exe"):
            with self.which_map({"python": stub, "py": r"C:\Windows\py.exe"}):
                notes = C.windows_python_notes()
                self.assertEqual(len(notes), 1)
                self.assertIn("Microsoft Store", notes[0])
                self.assertIn("py -3", notes[0])
            with self.which_map({"python": r"C:\Python312\python.exe"}):
                self.assertEqual(C.windows_python_notes(), [])
            with self.which_map({"py": r"C:\Windows\py.exe"}):
                self.assertIn("py -3", C.windows_python_notes()[0])
            # running FROM the Store Python: the alias is fine
            with mock.patch.object(sys, "executable", stub), self.which_map({"python": stub}):
                self.assertEqual(C.windows_python_notes(), [])
        with mock.patch.object(C, "is_windows", return_value=False):
            self.assertEqual(C.windows_python_notes(), [])

    def test_check_setup_report_shows_the_python_note(self):
        stub = r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\python.exe"
        with temp_home(), mock.patch.object(C, "is_windows", return_value=True), \
                mock.patch.object(sys, "executable", r"C:\Python312\python.exe"), \
                self.which_map({"python": stub, "py": r"C:\Windows\py.exe"}), \
                mock.patch.object(S, "check_token", return_value={"file": "x", "exists": False, "valid": False,
                                                                    "channel_title": None, "channel_id": None,
                                                                    "error": None}):
            info = S.gather()
        self.assertTrue(info["python"]["notes"])
        self.assertIn("NOTE: Typing 'python'", S.format_report(info))
        self.assertIn("py -", info["next_step"])                                   # advice never says the Store 'python'

    def test_find_ytdlp_uses_path_then_python_module(self):
        with self.which_map({"yt-dlp": r"C:\tools\yt-dlp.exe"}):
            self.assertEqual(C.find_ytdlp(), [r"C:\tools\yt-dlp.exe"])
        with self.which_map({}), mock.patch.object(C.importlib.util, "find_spec", return_value=object()):
            self.assertEqual(C.find_ytdlp(), [sys.executable, "-m", "yt_dlp"])
        with self.which_map({}), mock.patch.object(C.importlib.util, "find_spec", return_value=None):
            self.assertIsNone(C.find_ytdlp())

    def test_require_exe_message(self):
        with self.which_map({}):
            with self.assertRaises(SystemExit) as cm:
                C.require_exe("ffmpeg")
            self.assertIn("ffmpeg", str(cm.exception))
            self.assertIn("NEW terminal", str(cm.exception))
            with self.assertRaises(SystemExit):
                C.require_exe("yt-dlp")

    def test_whisper_cli_env_var_accepts_quotes_and_percent_vars(self):
        with hangul_dir() as d:
            exe = d / "whisper-cli.exe"
            exe.write_bytes(b"x")
            with mock.patch.object(C, "is_windows", return_value=True), \
                    mock.patch.dict(os.environ, {"YTCAPTION_WHISPER_CLI": f'"{exe}"'}):
                self.assertEqual(T.find_whisper_cli(), str(exe))

    def test_model_download_command_uses_curl_exe_on_windows(self):
        with hangul_dir() as d, temp_home(YTCAPTION_MODEL_DIR=str(d)), mock.patch.object(C, "is_windows", return_value=True):
            with self.assertRaises(T.DependencyError) as cm:
                T.find_whispercpp_model("large-v3-turbo")
        msg = str(cm.exception)
        self.assertIn("curl.exe --create-dirs -L -o", msg)     # bare `curl` is an Invoke-WebRequest alias in PowerShell 5.1
        self.assertIn("ggml-large-v3-turbo.bin", msg)

    def test_whisper_cpp_gets_ascii_names_when_the_work_folder_is_not_ascii(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"], seen["kw"] = cmd, kw
            (pathlib.Path(kw.get("cwd") or ".") / "wcpp.json").write_text(
                json.dumps({"transcription": [{"offsets": {"from": 0, "to": 1000}, "text": " 네"}]}), encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        with hangul_dir() as d, temp_home(YTCAPTION_MODEL_DIR=str(d)), mock.patch.object(C, "is_windows", return_value=True):
            (d / "ggml-small-q5_1.bin").write_bytes(b"m")
            elsewhere = d / "kept"
            elsewhere.mkdir()
            wav = elsewhere / "오디오.wav"
            wav.write_bytes(b"RIFF")
            with mock.patch.object(T.shutil, "which", return_value=r"C:\w\whisper-cli.exe"), \
                    mock.patch.object(T.subprocess, "run", side_effect=fake_run), support.quiet():
                words = T.run_whispercpp(wav, "ggml-small-q5_1.bin", "ko", "", d)
        cmd = seen["cmd"]
        self.assertEqual(seen["kw"]["cwd"], str(d))
        self.assertEqual(cmd[cmd.index("-f") + 1], "input.wav")
        self.assertEqual(cmd[cmd.index("-of") + 1], "wcpp")
        self.assertEqual([w["text"] for w in words], ["네"])


# ============================================================ 5. hardware detection
class WindowsHardwareTests(unittest.TestCase):
    def test_memorystatusex_layout_matches_win32(self):
        self.assertEqual(ctypes.sizeof(H.MemoryStatusEx), 64)
        names = [f[0] for f in H.MemoryStatusEx._fields_]
        self.assertEqual(names[:4], ["dwLength", "dwMemoryLoad", "ullTotalPhys", "ullAvailPhys"])
        self.assertEqual(len(names), 9)
        self.assertEqual(H.MemoryStatusEx.ullTotalPhys.offset, 8)

    def test_windows_ram_via_fake_kernel32(self):
        def fake_call(ref):
            ref._obj.ullTotalPhys = int(15.4 * 1024**3)
            return 1
        kernel32 = types.SimpleNamespace(GlobalMemoryStatusEx=fake_call)
        with mock.patch.object(ctypes, "windll", types.SimpleNamespace(kernel32=kernel32), create=True):
            self.assertEqual(H._windows_ram_gb(), 15.4)

    def test_windows_ram_falls_back_to_powershell_when_the_call_fails(self):
        kernel32 = types.SimpleNamespace(GlobalMemoryStatusEx=lambda ref: 0)
        with mock.patch.object(ctypes, "windll", types.SimpleNamespace(kernel32=kernel32), create=True), \
                mock.patch.object(H, "_powershell", return_value="17048768512\r\n"):
            self.assertEqual(H._windows_ram_gb(), 15.9)
        with mock.patch.object(H, "_powershell", return_value=""):                # no windll (e.g. this Mac) and no PowerShell
            with mock.patch.object(ctypes, "windll", create=True, new=mock.Mock(side_effect=AttributeError)):
                self.assertEqual(H._windows_ram_gb(), 0.0)

    def test_powershell_invocation_is_safe(self):
        seen = {}

        def fake(cmd, **kw):
            seen["cmd"], seen["kw"] = cmd, kw
            return subprocess.CompletedProcess(cmd, 0, "Intel(R) Core(TM) i7\r\n", "")
        with mock.patch.object(H.shutil, "which", side_effect=lambda n: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" if n == "powershell" else None), \
                mock.patch.object(H.subprocess, "run", side_effect=fake):
            out = H._powershell("Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }")
        cmd = seen["cmd"]
        self.assertTrue(cmd[0].endswith("powershell.exe"))
        self.assertIn("-NoProfile", cmd)
        self.assertIn("-NonInteractive", cmd)
        self.assertNotIn('"', " ".join(cmd))                                        # no embedded double quotes to mangle
        self.assertIn("UTF8", cmd[-1])                                              # forced UTF-8 stdout
        self.assertEqual(seen["kw"]["encoding"], "utf-8")
        self.assertGreaterEqual(seen["kw"]["timeout"], 20)
        self.assertEqual(out, "Intel(R) Core(TM) i7")

    def test_powershell_falls_back_to_pwsh_then_system32(self):
        with mock.patch.object(H.shutil, "which", side_effect=lambda n: "/usr/bin/pwsh" if n == "pwsh" else None):
            self.assertEqual(H._powershell_exe(), "/usr/bin/pwsh")
        with hangul_dir() as d:
            ps = d / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
            ps.parent.mkdir(parents=True)
            ps.write_bytes(b"x")
            with mock.patch.object(H.shutil, "which", return_value=None), mock.patch.dict(os.environ, {"SystemRoot": str(d)}):
                self.assertEqual(H._powershell_exe(), str(ps))
            with mock.patch.object(H.shutil, "which", return_value=None), mock.patch.dict(os.environ, {"SystemRoot": str(d / "none")}):
                self.assertIsNone(H._powershell_exe())
                self.assertEqual(H._powershell("anything"), "")

    def test_no_wmic_and_no_shell_anywhere(self):
        for p in sorted(SCRIPTS.glob("*.py")):
            src = p.read_text(encoding="utf-8")
            code = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
            self.assertIsNone(re.search(r"""["']wmic(\.exe)?(\s|["'])""", code), p.name)   # never invoked (docstring mention ok)
            self.assertNotIn("shell=True", code, p.name)
            for posix_only in ("os.getuid", "os.geteuid", "os.uname", "fcntl", "os.fork", "signal.SIGKILL",
                               "signal.SIGALRM", "os.killpg", "os.setsid"):
                self.assertNotIn(posix_only, code, (p.name, posix_only))

    def test_cpu_name_prefers_registry_then_powershell(self):
        fake = types.SimpleNamespace(
            HKEY_LOCAL_MACHINE=0, OpenKey=lambda root, sub: contextlib.nullcontext(sub),
            QueryValueEx=lambda key, name: ("  Intel(R) Core(TM)  i7-1360P   \r\n", 1))
        with mock.patch.dict(sys.modules, {"winreg": fake}):
            self.assertEqual(H._cpu_name("windows"), "Intel(R) Core(TM) i7-1360P")
        broken = types.SimpleNamespace(HKEY_LOCAL_MACHINE=0, OpenKey=mock.Mock(side_effect=OSError),
                                       QueryValueEx=None)
        with mock.patch.dict(sys.modules, {"winreg": broken}), \
                mock.patch.object(H, "_powershell", return_value="AMD Ryzen 7 7840U\r\n") as ps:
            self.assertEqual(H._cpu_name("windows"), "AMD Ryzen 7 7840U")
            self.assertIn("Win32_Processor", ps.call_args[0][0])

    def test_gpu_detection_from_windows_output(self):
        cim = "Intel(R) UHD Graphics\r\nNVIDIA GeForce RTX 4060 Laptop GPU\r\nMicrosoft Basic Display Adapter\r\n"
        smi = "NVIDIA GeForce RTX 4060 Laptop GPU, 8188\r\n"

        def fake_run(cmd, timeout=8):
            return smi if "nvidia-smi" in cmd[0].lower() else ""
        with mock.patch.object(H, "_nvidia_smi", return_value=r"C:\Windows\System32\nvidia-smi.exe"), \
                mock.patch.object(H, "_run", side_effect=fake_run), mock.patch.object(H, "_powershell", return_value=cim):
            gpus = H._gpus("windows", False, "Intel(R) Core(TM) i7", 16.0)
        self.assertEqual([g["vendor"] for g in gpus], ["nvidia", "intel"])
        self.assertEqual(gpus[0]["vram_gb"], 8.0)
        self.assertFalse(gpus[1]["discrete"])
        tier, rec = H.recommend({"os": "windows", "ram_gb": 15.2, "gpus": gpus, "apple_silicon": False})
        self.assertEqual((tier, rec["runtime"], rec["compute_type"]), ("nvidia-8gb+", "faster-whisper", "float16"))

    def test_gpu_detection_without_nvidia_smi_or_powershell_uses_registry_names(self):
        with mock.patch.object(H, "_nvidia_smi", return_value=None), mock.patch.object(H, "_powershell", return_value=""), \
                mock.patch.object(H, "_windows_registry_gpus", return_value="AMD Radeon(TM) Graphics\nNVIDIA GeForce GTX 1650"):
            gpus = H._gpus("windows", False, "AMD Ryzen 5", 13.9)
        self.assertEqual([g["vendor"] for g in gpus], ["amd", "nvidia"])
        self.assertFalse(gpus[0]["discrete"])
        tier, _ = H.recommend({"os": "windows", "ram_gb": 13.9, "gpus": gpus, "apple_silicon": False})
        self.assertEqual(tier, "cpu-16gb+")                       # 16 GB laptop reporting 13.9 GB is not 'cpu-8gb'

    def test_igpu_vs_discrete_names(self):
        integrated = [("intel", "Intel(R) UHD Graphics"), ("intel", "Intel(R) UHD Graphics 770"),
                      ("intel", "Intel(R) Iris(R) Xe Graphics"), ("intel", "Intel(R) Iris(R) Plus Graphics"),
                      ("intel", "Intel(R) Arc(TM) Graphics"), ("intel", "Intel(R) Arc(TM) 140V GPU (16GB)"),
                      ("amd", "AMD Radeon(TM) Graphics"), ("amd", "AMD Radeon(TM) 780M Graphics"),
                      ("amd", "AMD Radeon 610M"), ("amd", "AMD Radeon(TM) Vega 8 Graphics"),
                      ("amd", "AMD Radeon(TM) 8060S Graphics")]
        discrete = [("intel", "Intel(R) Arc(TM) A770 Graphics"), ("intel", "Intel(R) Arc(TM) A370M Graphics"),
                    ("intel", "Intel(R) Arc(TM) B580 Graphics"), ("intel", "Intel(R) Arc(TM) Pro A40"),
                    ("amd", "AMD Radeon RX 7800 XT"), ("amd", "AMD Radeon RX 6600M"),
                    ("amd", "AMD Radeon PRO W7800"), ("nvidia", "NVIDIA GeForce RTX 4060 Laptop GPU")]
        for v, n in integrated:
            self.assertFalse(H.is_discrete_gpu(v, n), n)
        for v, n in discrete:
            self.assertTrue(H.is_discrete_gpu(v, n), n)

    def test_nvidia_smi_fallback_folders(self):
        with hangul_dir() as d, mock.patch.object(C, "is_windows", return_value=True), \
                mock.patch.object(H.shutil, "which", return_value=None):
            exe = d / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe"
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"x")
            with mock.patch.dict(os.environ, {"ProgramFiles": str(d)}):
                self.assertEqual(H._nvidia_smi(), str(exe))
            with mock.patch.dict(os.environ, {"ProgramFiles": str(d / "x"), "SystemRoot": str(d / "y")}):
                self.assertIsNone(H._nvidia_smi())                                  # absent nvidia-smi is not an error

    def test_missing_executables_never_raise(self):
        with mock.patch.object(H.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(H._run(["nope"]), "")
        with mock.patch.object(H.subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 1)):
            self.assertEqual(H._run(["slow"]), "")


# ============================================================ 4. atomic writes / cleanup
class FileOpsTests(unittest.TestCase):
    def test_replace_retries_transient_permission_errors(self):
        real = os.replace
        calls = []

        def flaky(src, dst):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError(13, "locked by antivirus")
            return real(src, dst)
        with hangul_dir() as d:
            target = d / "t.json"
            target.write_text("old", encoding="utf-8")
            with mock.patch.object(C.os, "replace", side_effect=flaky), mock.patch.object(C.time, "sleep"):
                C.write_private(target, "new")
            self.assertEqual(target.read_text(encoding="utf-8"), "new")
            self.assertEqual(len(calls), 3)

    def test_permanent_lock_raises_and_leaves_no_temp_litter(self):
        with hangul_dir() as d:
            target = d / "t.json"
            target.write_text("old", encoding="utf-8")
            with mock.patch.object(C.os, "replace", side_effect=PermissionError(13, "locked")), \
                    mock.patch.object(C.time, "sleep"):
                with self.assertRaises(PermissionError):
                    C.write_private(target, "new")
            self.assertEqual(target.read_text(encoding="utf-8"), "old")              # original untouched
            self.assertEqual([p.name for p in d.iterdir()], ["t.json"])               # no .tmp left behind

    def test_write_private_overwrites_existing_and_creates_parents(self):
        with hangul_dir() as d:
            target = d / "새 폴더" / "안쪽" / "t.json"
            C.write_private(target, "가나다\n")
            C.write_private(target, "라마바\n")
            self.assertEqual(target.read_bytes(), "라마바\n".encode("utf-8"))

    def test_remove_tree_handles_read_only_files(self):
        with hangul_dir() as d:
            work = d / "work"
            (work / "sub").mkdir(parents=True)
            f = work / "sub" / "ro.wav"
            f.write_bytes(b"x")
            os.chmod(f, 0o444)                                                        # read-only attribute on Windows
            C.remove_tree(work)
            self.assertFalse(work.exists())
            C.remove_tree(d / "never existed")                                        # missing dir: no error

    def test_remove_tree_gives_up_quietly_when_something_stays_locked(self):
        with hangul_dir() as d, mock.patch.object(C.shutil, "rmtree"), mock.patch.object(C.time, "sleep") as sl:
            C.remove_tree(d)                                                          # rmtree 'succeeds' but dir remains
            self.assertEqual(sl.call_count, 4)
            self.assertTrue(d.exists())

    def test_make_temp_dir_is_a_real_directory_and_cleanup_removes_it(self):
        d = C.make_temp_dir()
        (d / "audio.wav").write_bytes(b"x")
        C.remove_tree(d)
        self.assertFalse(d.exists())

    def test_keep_audio_copies_out_of_an_ascii_work_folder(self):
        """--keep-audio used to write into the (possibly Hangul) output folder; now it is a copy of the temp file."""
        with hangul_dir() as d, temp_home(DEEPGRAM_API_KEY=FAKE_KEY):
            src = d / "영상 파일.mp4"
            src.write_bytes(b"v")
            out = d / "결과 폴더" / "영상 파일.srt"
            seen = {}

            def fake_extract(s, dst, *, cloud):
                seen["dst"] = dst
                dst.write_bytes(b"ID3audio")
            dg = {"results": {"utterances": [{"start": 0, "end": 1, "transcript": "네.", "words": [
                {"word": "네", "punctuated_word": "네.", "start": 0.0, "end": 1.0}]}]}}
            with mock.patch.object(T.shutil, "which", return_value=r"C:\ffmpeg.exe"), \
                    mock.patch.object(T, "ffprobe_duration", return_value=5.0), \
                    mock.patch.object(T, "extract_audio", side_effect=fake_extract), \
                    mock.patch("urllib.request.urlopen", support.FakeNet(lambda *a: support.FakeResp(dg))), \
                    contextlib.redirect_stdout(io.StringIO()), support.quiet():
                code = T.main(["--input", str(src), "--engine", "deepgram", "--out", str(out), "--keep-audio"])
            self.assertEqual(code, 0)
            self.assertEqual((d / "결과 폴더" / "영상 파일.audio.mp3").read_bytes(), b"ID3audio")
            self.assertEqual(seen["dst"].name, "audio.mp3")                           # ffmpeg wrote to plain ASCII
            self.assertFalse(seen["dst"].parent.exists())                              # temp folder removed

    def test_ctrl_c_is_a_clean_cancel(self):
        with hangul_dir() as d, temp_home(DEEPGRAM_API_KEY=FAKE_KEY):
            src = d / "v.mp4"
            src.write_bytes(b"v")
            err = io.StringIO()
            with mock.patch.object(T.shutil, "which", return_value="/x"), mock.patch.object(T, "ffprobe_duration", return_value=5.0), \
                    mock.patch.object(T, "extract_audio", side_effect=KeyboardInterrupt), \
                    contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = T.main(["--input", str(src), "--engine", "deepgram", "--out", str(d / "o.srt")])
            self.assertEqual(code, 130)
            self.assertIn("cancelled", err.getvalue())


# ============================================================ real ffmpeg on Hangul / space / '%' / '-' paths
@unittest.skipUnless(HAVE_FFMPEG, "needs ffmpeg + ffprobe on PATH (installed by the CI workflow)")
class RealFfmpegPathTests(unittest.TestCase):
    def test_extract_audio_from_and_to_hangul_paths_and_dash_names(self):
        with hangul_dir() as d:
            src = d / "원본 영상 테스트.mp4"
            make_video(src)
            wav = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                                  "-y", str(d / "-tone.wav")], capture_output=True)
            self.assertEqual(wav.returncode, 0, wav.stderr)
            old = os.getcwd()
            os.chdir(d)
            try:
                T.extract_audio(pathlib.Path("-tone.wav"), pathlib.Path("-out 오디오.mp3"), cloud=True)
            finally:
                os.chdir(old)
            self.assertGreater((d / "-out 오디오.mp3").stat().st_size, 0)
            self.assertIsNotNone(T.ffprobe_duration(d / "-out 오디오.mp3"))

    def test_frames_at_cli_with_hangul_and_space_paths_on_a_legacy_console(self):
        with hangul_dir() as d:
            video = d / "영상 파일.mp4"
            make_video(video)
            out = d / "프레임 폴더"
            r = run_script("frames_at.py", "--video", str(video), "--out", str(out), "--at", "1,2", "--crop", "full,left",
                           encoding="cp1252")
            self.assertEqual(r.returncode, 0, text(r.stderr))
            self.assertEqual(len(list(out.glob("*.jpg"))), 4)
            self.assertIn("프레임 폴더", text(r.stdout))                                 # printed as UTF-8, not '?'

    def test_frames_at_with_a_relative_dash_video_name(self):
        with hangul_dir() as d:
            make_video(d / "-clip.mp4")
            r = run_script("frames_at.py", "--video=-clip.mp4", "--out", "o", "--at", "1", cwd=d)
            self.assertEqual(r.returncode, 0, text(r.stderr))
            self.assertEqual(len(list((d / "o").glob("*.jpg"))), 1)

    def test_extract_thumbnails_grab_and_gallery_survive_percent_and_hangul(self):
        with hangul_dir(PCT_DIR) as d:
            video = d / "영상.mp4"
            make_video(video)
            out = d / "후보 100%"
            r = run_script("extract_thumbnails.py", "grab", "--source", str(video), "--timestamps", "0:01",
                           "--out", str(out), "--burst", "1", "--fps", "2", encoding="cp1252")
            self.assertEqual(r.returncode, 0, text(r.stderr))
            frames = sorted((out / "001").glob("*.png"))
            self.assertEqual(len(frames), 2, [p.name for p in (out / "001").iterdir()])
            picks = d / "선택"
            picks.mkdir()
            shutil.copy(frames[0], picks / "01_한글 라벨.png")
            notes = d / "메모.json"
            notes.write_bytes(b"\xef\xbb\xbf" + json.dumps({"01_한글 라벨": "메모 <b>&"}).encode("utf-8"))
            html_out = d / "갤러리.html"
            r = run_script("extract_thumbnails.py", "gallery", "--picks-dir", str(picks), "--out", str(html_out),
                           "--title", "제목 <x> & 'q'", "--notes", str(notes), encoding="cp1252")
            self.assertEqual(r.returncode, 0, text(r.stderr))
            raw = html_out.read_bytes()
            page = raw.decode("utf-8")
            self.assertIn('<meta charset="utf-8">', page)                               # else Windows browsers show mojibake
            self.assertNotIn(b"\r\n", raw)
            self.assertIn("한글 라벨", page)
            self.assertIn("&lt;b&gt;", page)                                            # notes/titles are escaped
            self.assertNotIn("<x>", page)
            self.assertFalse((picks / "_gallery_jpeg").exists())                        # temp jpeg folder cleaned up


# ============================================================ yt-dlp / thumbnails argv
class ThumbnailArgvTests(unittest.TestCase):
    def test_download_uses_python_module_fallback_and_safe_output_template(self):
        seen = []
        with hangul_dir(PCT_DIR) as d:
            def fake_run(cmd, **kw):
                seen.append(cmd)
                (d / "source.mp4").write_bytes(b"v")
                return subprocess.CompletedProcess(cmd, 0)
            with mock.patch.object(C, "find_ytdlp", return_value=[sys.executable, "-m", "yt_dlp"]), \
                    mock.patch.object(E.subprocess, "run", side_effect=fake_run), support.quiet():
                got = E.download_source("https://www.youtube.com/watch?v=abc", d, 1080)
            self.assertEqual(got, d / "source.mp4")
        cmd = seen[0]
        self.assertEqual(cmd[:3], [sys.executable, "-m", "yt_dlp"])
        self.assertEqual(cmd[cmd.index("-P") + 1], os.path.abspath(d))              # folder via -P, never inside -o
        self.assertEqual(cmd[cmd.index("-o") + 1], "source.%(ext)s")               # '%' / ':' in the folder cannot break it
        self.assertEqual(cmd[-2:], ["--", "https://www.youtube.com/watch?v=abc"])  # a URL can never be an option

    def test_missing_ytdlp_and_ffmpeg_give_install_advice_not_a_traceback(self):
        with hangul_dir() as d:
            with mock.patch.object(C, "find_ytdlp", return_value=None):
                with self.assertRaises(SystemExit) as cm:
                    E.download_source("https://example.com/v", d, 720)
                self.assertIn("yt-dlp", str(cm.exception))
            with mock.patch.object(shutil, "which", return_value=None):
                with self.assertRaises(SystemExit) as cm:
                    E.grab(types.SimpleNamespace(out=str(d / "o"), source=str(d / "v.mp4"), timestamps="0:01",
                                                 burst=1.0, fps=2.0, max_height=720, keep_source=False))
                self.assertIn("ffmpeg", str(cm.exception))

    def test_burst_output_pattern_is_relative_to_the_burst_folder(self):
        seen = []
        with hangul_dir(PCT_DIR) as d:
            video = d / "v.mp4"
            video.write_bytes(b"v")
            with mock.patch.object(shutil, "which", return_value=r"C:\ffmpeg\bin\ffmpeg.exe"), \
                    mock.patch.object(E.subprocess, "run",
                                      side_effect=lambda cmd, **kw: seen.append((cmd, kw)) or subprocess.CompletedProcess(cmd, 0)), \
                    contextlib.redirect_stdout(io.StringIO()), support.quiet():
                E.grab(types.SimpleNamespace(out=str(d / "out"), source=str(video), timestamps="1:02", burst=1.0,
                                             fps=2.0, max_height=720, keep_source=True))
        cmd, kw = seen[0]
        self.assertEqual(cmd[0], r"C:\ffmpeg\bin\ffmpeg.exe")
        self.assertEqual(cmd[cmd.index("-vf") + 1], "fps=2.0")                      # no path inside the filter string
        self.assertIn("102_%02d.png", cmd)                                          # bare pattern, cwd does the rest
        self.assertTrue(kw["cwd"].endswith("102"))


# ============================================================ CI workflow sanity (no YAML library needed)
class WorkflowTests(unittest.TestCase):
    WF = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows" / "test.yml"

    def setUp(self):
        if not self.WF.is_file():
            self.skipTest("workflow file not present in this checkout")
        self.src = self.WF.read_text(encoding="utf-8")

    def test_structure(self):
        self.assertNotIn("\t", self.src)
        for n, line in enumerate(self.src.splitlines(), 1):
            stripped = line.lstrip(" ")
            if stripped and not stripped.startswith("#"):
                self.assertEqual((len(line) - len(stripped)) % 2, 0, f"odd indentation on line {n}")
        for needle in ("on:\n  push:\n  pull_request:", "permissions:\n  contents: read",
                       "ubuntu-latest", "windows-latest", "macos-latest", '"3.10"', '"3.12"', '"3.x"',
                       "actions/checkout@v7", "actions/setup-python@v7", "python -m unittest discover tests -v",
                       "PYTHONIOENCODING: cp1252", 'PYTHONUTF8: "0"', "smoke_help.py", "py_compile"):
            self.assertIn(needle, self.src, needle)
        uses = re.findall(r"uses:\s*(\S+)", self.src)
        self.assertTrue(uses and all(re.fullmatch(r"actions/[\w-]+@v\d+", u) for u in uses), uses)


# ============================================================ 6. OAuth loopback
class OAuthLoopbackTests(unittest.TestCase):
    def test_server_binds_loopback_only_on_a_random_port_without_reuse(self):
        self.assertFalse(R.LoopbackServer.allow_reuse_address)
        srv = R.LoopbackServer(("127.0.0.1", 0), R.Catcher)
        try:
            host, port = srv.server_address[:2]
            self.assertEqual(host, "127.0.0.1")
            self.assertGreater(port, 1024)
        finally:
            srv.server_close()

    def test_second_server_gets_a_different_port(self):
        a = R.LoopbackServer(("127.0.0.1", 0), R.Catcher)
        b = R.LoopbackServer(("127.0.0.1", 0), R.Catcher)
        try:
            self.assertNotEqual(a.server_address[1], b.server_address[1])
        finally:
            a.server_close()
            b.server_close()

    def test_browser_failure_never_raises(self):
        with mock.patch.object(R.webbrowser, "open", side_effect=RuntimeError("no browser")):
            self.assertFalse(R.open_browser("https://x"))
        with mock.patch.object(R.webbrowser, "open", return_value=False):
            self.assertFalse(R.open_browser("https://x"))
        with mock.patch.object(R.webbrowser, "open", return_value=True):
            self.assertTrue(R.open_browser("https://x"))

    def run_main(self, files, argv, *, open_result=False, wait_side_effect=None):
        out = io.StringIO()
        with temp_home() as d:
            for name, content in files.items():
                (d / name).write_text(content, encoding="utf-8")
            with mock.patch.object(R.webbrowser, "open", return_value=open_result), \
                    mock.patch.object(R, "wait_for_code", side_effect=wait_side_effect), \
                    contextlib.redirect_stdout(out):
                try:
                    code, err = R.main(argv), None
                except SystemExit as e:
                    code, err = None, str(e)
        return code, err, out.getvalue()

    SECRET = json.dumps({"installed": {"client_id": "cid", "client_secret": "csec"}})

    def test_url_is_printed_and_flow_continues_when_no_browser_starts(self):
        code, err, out = self.run_main({"cs.json": self.SECRET}, ["--token", "t.json", "--secret", "cs.json"],
                                       wait_side_effect=lambda srv, t: {"code": None, "error": None, "state": ""})
        self.assertIn("https://accounts.google.com/o/oauth2/v2/auth?", out)
        self.assertIn("open the URL above by hand", out)
        self.assertIn("Timed out", err)

    def test_ctrl_c_while_waiting_is_a_clean_cancel(self):
        code, err, out = self.run_main({"cs.json": self.SECRET}, ["--token", "t.json", "--secret", "cs.json"],
                                       wait_side_effect=KeyboardInterrupt)
        self.assertIn("cancelled", err)
        self.assertIn("nothing saved", err)

    def test_secret_with_bom_is_accepted_and_missing_secret_is_explained(self):
        with temp_home() as d:
            (d / "cs.json").write_bytes(b"\xef\xbb\xbf" + self.SECRET.encode())
            self.assertEqual(R.read_client_secret(d / "cs.json"), ("cid", "csec"))
            with self.assertRaises(SystemExit) as cm:
                R.read_client_secret(d / "nope.json")
            self.assertIn("google-cloud-setup.md", str(cm.exception))
            (d / "bad.json").write_text("{not json", encoding="utf-8")
            with self.assertRaises(SystemExit) as cm:
                R.read_client_secret(d / "bad.json")
            self.assertNotIn("Traceback", str(cm.exception))
            (d / "list.json").write_text("[]", encoding="utf-8")
            with self.assertRaises(SystemExit):
                R.read_client_secret(d / "list.json")

    def test_browser_disconnect_is_not_noise(self):
        srv = R.LoopbackServer(("127.0.0.1", 0), R.Catcher)
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                try:
                    raise ConnectionAbortedError(10053, "browser closed the tab")
                except ConnectionAbortedError:
                    srv.handle_error(None, ("127.0.0.1", 1))
        finally:
            srv.server_close()
        self.assertEqual(err.getvalue(), "")

    def test_reply_has_length_and_utf8_charset(self):
        import http.client
        import threading
        srv = R.LoopbackServer(("127.0.0.1", 0), R.Catcher)
        srv.auth = {"code": None, "error": None, "state": "s"}
        srv.timeout = 5
        t = threading.Thread(target=srv.handle_request, daemon=True)
        t.start()
        try:
            c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
            c.request("GET", "/favicon.ico")
            resp = c.getresponse()
            body = resp.read()
            c.close()
        finally:
            t.join(5)
            srv.server_close()
        self.assertEqual(resp.status, 404)
        self.assertEqual(int(resp.getheader("Content-Length")), len(body))
        self.assertIn("utf-8", resp.getheader("Content-Type"))


# ============================================================ 7. getpass / --set-key on Windows terminals
class SetKeyWindowsTests(unittest.TestCase):
    def run_set_key(self, *, tty=True, key=FAKE_KEY, windows=True):
        out, err = io.StringIO(), io.StringIO()
        with temp_home() as d, mock.patch.object(S.sys.stdin, "isatty", return_value=tty), \
                mock.patch.object(S.getpass, "getpass", return_value=key), \
                mock.patch.object(C, "is_windows", return_value=windows), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = S.main(["--set-key", "openai"])
            saved = (d / "ytcaption.env").exists()
        return code, out.getvalue(), err.getvalue(), saved

    def test_non_tty_message_names_each_terminal_family(self):
        code, out, err, saved = self.run_set_key(tty=False)
        self.assertEqual((code, saved), (2, False))
        for word in ("PowerShell", "cmd", "Windows Terminal", "Git Bash/mintty does not work", "ISE", "piped"):
            self.assertIn(word, err)
        self.assertNotIn(FAKE_KEY, out + err)

    def test_ime_pasted_non_ascii_key_is_refused_with_advice(self):
        code, out, err, saved = self.run_set_key(key="ｓｋ－ｔｅｓｔ１２３４５６")      # full-width characters from an IME
        self.assertEqual((code, saved), (2, False))
        self.assertIn("English input", err)
        self.assertNotIn("ｓｋ", out + err)

    def test_unwritable_env_file_is_reported_not_a_traceback(self):
        with mock.patch.object(C, "set_env_key", side_effect=PermissionError(13, "locked")), \
                mock.patch.object(S.sys.stdin, "isatty", return_value=True), \
                mock.patch.object(S.getpass, "getpass", return_value=FAKE_KEY), \
                contextlib.redirect_stderr(io.StringIO()) as err, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(S.main(["--set-key", "openai"]), 2)
        self.assertIn("could not save", err.getvalue())
        self.assertNotIn(FAKE_KEY, err.getvalue())

    def test_getpass_warning_on_a_non_console_stdin_never_falls_back_to_echo(self):
        import getpass

        def fallback(*a, **k):                      # what getpass does on Windows when sys.stdin is not the console
            import warnings
            warnings.warn("Can not control echo on the terminal.", getpass.GetPassWarning, stacklevel=2)
            return FAKE_KEY
        with temp_home() as d, mock.patch.object(S.sys.stdin, "isatty", return_value=True), \
                mock.patch.object(S.getpass, "getpass", side_effect=fallback), \
                contextlib.redirect_stderr(io.StringIO()) as err, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(S.main(["--set-key", "openai"]), 2)
            self.assertFalse((d / "ytcaption.env").exists())
        self.assertNotIn(FAKE_KEY, err.getvalue())


# ============================================================ upload payload
class UploadPayloadTests(unittest.TestCase):
    def test_multipart_carries_clean_utf8_lf_srt_whatever_the_editor_wrote(self):
        with hangul_dir() as d:
            srt = d / "영상 자막.srt"
            srt.write_bytes(b"\xef\xbb\xbf" + SRT_KO.replace("\n", "\r\n").encode("utf-8"))
            body, ctype = U.multipart({"snippet": {"name": "한국어"}}, srt)
        self.assertTrue(ctype.startswith("multipart/related; boundary="))
        self.assertNotIn(b"\xef\xbb\xbf", body)
        payload = body.split(b"application/octet-stream\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[0]
        self.assertEqual(payload.decode("utf-8"), SRT_KO)
        self.assertNotIn(b"\r", payload)

    def test_legacy_encoded_srt_stops_the_upload_with_a_fix(self):
        with hangul_dir() as d:
            srt = d / "ansi.srt"
            srt.write_bytes("1\n00:00:01,000 --> 00:00:02,000\n안녕\n".encode("cp949"))
            with self.assertRaises(SystemExit) as cm:
                U.multipart({}, srt)
            self.assertIn("UTF-8", str(cm.exception))

    def test_main_help_and_arg_paths_with_quotes(self):
        with hangul_dir() as d, temp_home():
            srt = d / "a.srt"
            srt.write_text(SRT_KO, encoding="utf-8")
            tok = d / "tok.json"
            tok.write_text(json.dumps({"refresh_token": "r", "client_id": "c", "client_secret": "s"}), encoding="utf-8")

            def handler(method, url, headers, data):
                if "captions" in url:
                    return support.FakeResp({"items": []})
                return support.FakeResp({"access_token": "AT"})

            def fake_urlopen(req, data=None, timeout=None):
                if isinstance(req, str):
                    return support.FakeResp({"access_token": "AT"})
                return support.FakeNet(handler)(req, timeout=timeout)
            out = io.StringIO()
            with mock.patch("urllib.request.urlopen", fake_urlopen), mock.patch.object(C, "is_windows", return_value=True), \
                    contextlib.redirect_stdout(out):
                code = U.main(["--video", "VID", "--srt", f'"{srt}"', "--token", f'"{tok}"', "--dry-run"])
            self.assertEqual(code, 0)
            self.assertIn("DRY RUN", out.getvalue())


if __name__ == "__main__":
    unittest.main()
