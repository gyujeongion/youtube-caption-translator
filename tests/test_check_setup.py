import contextlib
import io
import json
import os
import stat
import unittest
from unittest import mock

import support
from support import FAKE_KEY, temp_home

import _common as C
import check_setup as S
import upload_caption as U


def run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = S.main(argv)
    return code, out.getvalue(), err.getvalue()


class PrefsCliTests(unittest.TestCase):
    def test_set_and_show_prefs(self):
        with temp_home():
            code, out, _ = run_main(["--set-pref", "source_language=ko", "target_languages=en,ja",
                                     "input_mode=stt_api", "stt_engine=deepgram", "publish_mode=direct"])
            self.assertEqual(code, 0)
            p = C.load_prefs()
            self.assertEqual(p["target_languages"], ["en", "ja"])
            self.assertEqual(p["publish_mode"], "direct")
            code, out, _ = run_main(["--show-prefs"])
            d = json.loads(out)
            self.assertEqual(d["prefs"]["stt_engine"], "deepgram")
            self.assertIn("token_file", d["defaults_for_unset"])

    def test_invalid_pref_rejected_and_nothing_written(self):
        with temp_home() as d:
            code, _, err = run_main(["--set-pref", "publish_mode=whenever"])
            self.assertEqual(code, 2)
            self.assertIn("publish_mode", err)
            self.assertFalse((d / "ytcaption_prefs.json").exists())
            self.assertEqual(run_main(["--set-pref", "bogus=1"])[0], 2)
            self.assertEqual(run_main(["--set-pref", "noequals"])[0], 2)


class SetKeyTests(unittest.TestCase):
    def test_key_saved_hidden_and_locked(self):
        with temp_home() as d:
            with mock.patch.object(S.sys.stdin, "isatty", return_value=True), \
                    mock.patch.object(S.getpass, "getpass", return_value=FAKE_KEY) as gp:
                code, out, err = run_main(["--set-key", "deepgram"])
            self.assertEqual(code, 0)
            self.assertNotIn(FAKE_KEY, out + err)          # never echoed
            self.assertTrue(gp.called)
            self.assertIn(f"DEEPGRAM_API_KEY={FAKE_KEY}", (d / "ytcaption.env").read_text())
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE((d / "ytcaption.env").stat().st_mode), 0o600)

    def test_refuses_without_tty(self):
        with temp_home() as d:
            with mock.patch.object(S.sys.stdin, "isatty", return_value=False):
                code, out, err = run_main(["--set-key", "soniox"])
            self.assertEqual(code, 2)
            self.assertFalse((d / "ytcaption.env").exists())

    def test_rejects_junk_key(self):
        with temp_home() as d:
            with mock.patch.object(S.sys.stdin, "isatty", return_value=True), \
                    mock.patch.object(S.getpass, "getpass", return_value="has space in it"):
                code, out, err = run_main(["--set-key", "openai"])
            self.assertEqual(code, 2)
            self.assertNotIn("has space", out + err)
            self.assertFalse((d / "ytcaption.env").exists())

    def test_key_flag_only_accepts_known_providers(self):
        self.assertEqual(run_main(["--set-key", "elevenlabs"])[0], 2)


def fake_token(valid=True, exists=True, error=None):
    return {"file": "/x/tok.json", "exists": exists, "valid": valid,
            "channel_title": "My Channel" if valid else None, "channel_id": "UC123" if valid else None,
            "error": error}


class GatherTests(unittest.TestCase):
    def gather(self, prefs=None, token=None, secret=False, keys=None):
        with temp_home() as d:
            if prefs is not None:
                C.save_prefs(prefs)
            if secret:
                (d / "client_secret.json").write_text("{}")
            for k, v in (keys or {}).items():
                os.environ[C.KEY_VARS[k]] = v
            with mock.patch.object(S, "check_token", return_value=token or fake_token(False, False)):
                return S.gather()

    def test_fresh_install_says_run_wizard(self):
        i = self.gather()
        self.assertFalse(i["ready"])
        self.assertIn("wizard", i["next_step"])

    def test_missing_client_secret(self):
        i = self.gather(prefs={"input_mode": "srt"})
        self.assertIn("google-cloud-setup", i["next_step"])

    def test_missing_token_points_at_reauth(self):
        i = self.gather(prefs={"input_mode": "srt"}, secret=True)
        self.assertIn("reauth_channel.py", i["next_step"])
        self.assertIn("my_channel_token.json", i["next_step"])

    def test_bad_token(self):
        i = self.gather(prefs={"input_mode": "srt"}, secret=True, token=fake_token(False, True, "invalid_grant"))
        self.assertIn("invalid_grant", i["next_step"])
        self.assertFalse(i["ready"])

    def test_ready_srt_mode(self):
        i = self.gather(prefs={"input_mode": "srt"}, secret=True, token=fake_token())
        self.assertTrue(i["ready"])
        self.assertTrue(i["next_step"].startswith("All set"))

    def test_stt_api_needs_key_and_never_leaks_it(self):
        i = self.gather(prefs={"input_mode": "stt_api", "stt_engine": "deepgram"}, secret=True, token=fake_token())
        self.assertFalse(i["ready"])
        self.assertIn("--set-key deepgram", i["next_step"])
        i = self.gather(prefs={"input_mode": "stt_api", "stt_engine": "deepgram"}, secret=True, token=fake_token(),
                        keys={"deepgram": FAKE_KEY})
        self.assertTrue(i["ready"])
        self.assertTrue(i["stt_keys"]["deepgram"] is True)
        self.assertNotIn(FAKE_KEY, json.dumps(i))

    def test_stt_local_without_runtime_shows_install(self):
        with mock.patch.object(S.T, "available_runtimes",
                               return_value={"whisper.cpp": False, "mlx-whisper": False, "faster-whisper": False}):
            i = self.gather(prefs={"input_mode": "stt_local"}, secret=True, token=fake_token())
        self.assertFalse(i["ready"])
        self.assertIn(i["hardware"]["recommended"]["install_cmd"], i["next_step"])

    def test_json_keys(self):
        i = self.gather(prefs={"input_mode": "srt"})
        for k in ("python", "ffmpeg", "ffprobe", "yt_dlp", "client_secret", "token", "stt_keys",
                  "local_runtimes", "hardware", "next_step", "ready"):
            self.assertIn(k, i)
        self.assertEqual(set(i["local_runtimes"]), {"whisper-cli", "mlx_whisper", "faster_whisper"})

    def test_text_report_ends_with_next_step(self):
        i = self.gather(prefs={"input_mode": "srt"})
        self.assertTrue(S.format_report(i).splitlines()[-1].startswith("NEXT STEP:"))


class CheckTokenTests(unittest.TestCase):
    def test_valid_token_reports_channel_without_secrets(self):
        with temp_home() as d:
            (d / "t.json").write_text(json.dumps({"refresh_token": "RT-SECRET", "client_id": "cid",
                                                  "client_secret": "CS-SECRET"}))
            with mock.patch.object(U, "access_token", return_value="AT-SECRET"), \
                    mock.patch.object(U, "api_get", return_value={"items": [{"id": "UC1", "snippet": {"title": "Chan"}}]}):
                res = S.check_token("t.json")
            self.assertTrue(res["valid"])
            self.assertEqual((res["channel_title"], res["channel_id"]), ("Chan", "UC1"))
            blob = json.dumps(res)
            for s in ("RT-SECRET", "CS-SECRET", "AT-SECRET"):
                self.assertNotIn(s, blob)

    def test_refresh_failure_is_reported_not_raised(self):
        with temp_home() as d:
            (d / "t.json").write_text("{}")
            with mock.patch.object(U, "access_token", side_effect=SystemExit("Token expired or revoked: x\nbody")):
                res = S.check_token("t.json")
            self.assertFalse(res["valid"])
            self.assertIn("expired", res["error"])

    def test_missing_file(self):
        with temp_home():
            res = S.check_token("nope.json")
            self.assertEqual((res["exists"], res["valid"]), (False, False))

    def test_no_channel(self):
        with temp_home() as d:
            (d / "t.json").write_text("{}")
            with mock.patch.object(U, "access_token", return_value="x"), \
                    mock.patch.object(U, "api_get", return_value={"items": []}):
                self.assertFalse(S.check_token("t.json")["valid"])


class ConfigDirCompatTests(unittest.TestCase):
    def test_upload_caption_resolves_against_config_dir(self):
        with temp_home() as d:
            self.assertEqual(U.token_path("a.json"), d / "a.json")
            self.assertEqual(U.token_path(str(d / "b.json")), d / "b.json")

    def test_default_dir_unchanged(self):
        env = {k: v for k, v in os.environ.items() if k != "YTCAPTION_HOME"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(U.token_path("a.json"), C.pathlib.Path.home() / ".claude" / "credentials" / "a.json")


if __name__ == "__main__":
    unittest.main()
