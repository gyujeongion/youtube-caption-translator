"""First-run preflight for youtube-caption-translator, plus safe key/prefs setters.

    python3 check_setup.py [--json]              preflight checklist; last line is "NEXT STEP: ..."
    python3 check_setup.py --set-key deepgram    hidden prompt (getpass); writes ytcaption.env, chmod 600
    python3 check_setup.py --set-key openai
    python3 check_setup.py --set-key soniox
    python3 check_setup.py --set-pref KEY=VALUE [KEY=VALUE ...]
    python3 check_setup.py --show-prefs

(Commands the script PRINTS use the interpreter name of the current process:
python / python3 / py-style, taken from sys.executable.)

Exit code: 0 iff the YouTube token check passes AND, when prefs input_mode is stt_api or
stt_local, that fallback's prerequisites are ready (key present / runtime present).
input_mode=srt or unset needs nothing else: an editor SRT always works, so the SRT path
is never a failure. Otherwise exit 1.

--set-key must be run by the USER in a real terminal: keys are never taken as
arguments and must never be pasted into a chat. The key is not echoed or logged.

--set-pref keys (validated):
    source_language=ko                    target_languages=en,ja (comma list)
    input_mode=srt|stt_api|stt_local      stt_engine=deepgram|openai|soniox|local
    publish_mode=review|direct            token_file=NAME.json   client_secret=NAME.json

Files live in the config dir: $YTCAPTION_HOME if set, else ~/.claude/credentials/.
The preflight prints only booleans for keys and never prints tokens.
Python standard library only (3.10+).
"""
from __future__ import annotations

import argparse
import getpass
import http.client
import json
import os
import shutil
import sys
import urllib.error
import warnings

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402
import detect_hardware as H  # noqa: E402
import transcribe as T  # noqa: E402
import upload_caption as U  # noqa: E402

CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true"


def check_token(token_name: str) -> dict:
    """Validate the OAuth token by calling channels.list mine=true (1 quota unit)."""
    path = U.token_path(token_name)
    res = {"file": str(path), "exists": path.is_file(), "valid": False,
           "channel_title": None, "channel_id": None, "error": None}
    if not res["exists"]:
        return res
    try:
        at = U.access_token(token_name)
        items = U.api_get(CHANNELS_URL, at).get("items", [])
        if not items:
            res["error"] = "authorized account has no YouTube channel (wrong account/brand channel picked?)"
        else:
            res["valid"] = True
            res["channel_title"] = items[0]["snippet"]["title"]
            res["channel_id"] = items[0]["id"]
    except SystemExit as e:  # upload_caption.access_token exits on refresh failure
        res["error"] = str(e).splitlines()[0][:200] if str(e) else "token refresh failed"
    except urllib.error.HTTPError as e:
        res["error"] = f"YouTube API HTTP {e.code}"
    except (urllib.error.URLError, OSError, ValueError, KeyError, http.client.HTTPException) as e:
        res["error"] = f"{type(e).__name__}: {str(e)[:150]}"
    return res


def gather() -> dict:
    prefs = C.load_prefs()
    py_ok = sys.version_info >= (3, 10)
    ffmpeg, ffprobe = (bool(shutil.which(x)) for x in ("ffmpeg", "ffprobe"))
    ytdlp = bool(C.find_ytdlp())
    keys = {p: bool(C.get_key(p)) for p in C.KEY_VARS}
    rt = T.available_runtimes()
    local_rt = {"whisper-cli": rt["whisper.cpp"], "mlx_whisper": rt["mlx-whisper"],
                "faster_whisper": rt["faster-whisper"]}
    secret_name = prefs.get("client_secret") or C.DEFAULT_PREFS["client_secret"]
    token_name = prefs.get("token_file") or C.DEFAULT_PREFS["token_file"]
    secret_path = C.resolve_in_config(secret_name)
    hw = H.detect()
    token = check_token(token_name)

    stt_api_ready = ffmpeg and any(keys.values())
    stt_local_ready = ffmpeg and any(local_rt.values())
    mode = prefs.get("input_mode")
    engine = prefs.get("stt_engine") or ""
    if mode == "stt_api":
        input_ready = ffmpeg and (keys.get(engine, False) if engine in keys else any(keys.values()))
    elif mode == "stt_local":
        input_ready = stt_local_ready
    else:
        input_ready = True  # srt or unset: an editor SRT needs no setup
    info = {
        "config_dir": str(C.config_dir()),
        "python": {"ok": py_ok, "version": sys.version.split()[0], "notes": C.windows_python_notes()},
        "ffmpeg": ffmpeg, "ffprobe": ffprobe, "yt_dlp": ytdlp,
        "prefs": prefs,
        "client_secret": {"file": str(secret_path), "exists": secret_path.is_file()},
        "token": token,
        "stt_keys": keys,
        "local_runtimes": local_rt,
        "input_paths": {"srt": True, "stt_api": stt_api_ready, "stt_local": stt_local_ready},
        "input_ready": input_ready,
        "fallback_mode": mode if mode in ("stt_api", "stt_local") else "none",
        "hardware": {k: hw[k] for k in ("os", "arch", "cpu", "ram_gb", "gpus", "apple_silicon",
                                        "tier", "recommended", "table_date", "table_stale")},
    }
    info["ready"] = bool(input_ready and token["valid"])
    info["next_step"] = next_step(info)
    return info


def next_step(i: dict) -> str:
    prefs, tok = i["prefs"], i["token"]
    mode = prefs.get("input_mode")
    py = C.python_cmd()
    secret = prefs.get("client_secret") or C.DEFAULT_PREFS["client_secret"]
    name = prefs.get("token_file") or C.DEFAULT_PREFS["token_file"]
    if not i["python"]["ok"]:
        return f"Install Python 3.10 or newer, then re-run {py} scripts/check_setup.py."
    if not prefs:
        return ("Run the setup wizard: ask the user the wizard questions (languages, input path, publish mode, "
                f"channel) and store each answer with {py} scripts/check_setup.py --set-pref KEY=VALUE.")
    if mode in ("stt_api", "stt_local") and not i["ffmpeg"]:
        return C.ffmpeg_missing_message()
    if mode == "stt_api":
        eng = prefs.get("stt_engine")
        if eng in C.KEY_VARS and not i["stt_keys"][eng]:
            return (f"Ask the user to run this themselves in a terminal (never paste the key in chat): "
                    f"{py} scripts/check_setup.py --set-key {eng}")
        if not eng and not any(i["stt_keys"].values()):
            return (f"Pick a cloud STT engine ({py} scripts/check_setup.py --set-pref stt_engine=deepgram|openai|soniox), "
                    "then --set-key for it.")
    if mode == "stt_local" and not any(i["local_runtimes"].values()):
        rec = i["hardware"]["recommended"]
        steps = rec.get("install_steps") or ([rec["install_cmd"]] if rec.get("install_cmd") else [])
        numbered = " ".join(f"({n}) {st}" for n, st in enumerate(steps, 1))
        return f"Ask the user to approve installing the local runtime, then run these steps one at a time: {numbered}"
    if not i["client_secret"]["exists"]:
        return ("Google Cloud setup is not done: follow references/google-cloud-setup.md until "
                f"client_secret.json is saved at {i['client_secret']['file']}")
    if not tok["exists"]:
        return f"Authorize the channel: {py} scripts/reauth_channel.py --token {name} --secret {secret}"
    if not tok["valid"]:
        return (f"Token check failed ({tok['error']}). Re-authorize: "
                f"{py} scripts/reauth_channel.py --token {name} --secret {secret}")
    return "All set. Provide a source SRT (or a video for transcription) and a YouTube video ID."


def _flag(ok: bool, optional: bool = False) -> str:
    return "OK     " if ok else ("optional" if optional else "MISSING")


def format_report(i: dict) -> str:
    L = []
    L.append(f"Config dir: {i['config_dir']}")
    L.append(f"[{_flag(i['python']['ok'])}] Python >= 3.10 (found {i['python']['version']})")
    for note in i["python"].get("notes", []):
        L.append(f"  NOTE: {note}")
    L.append(f"[{_flag(i['ffmpeg'])}] ffmpeg")
    L.append(f"[{_flag(i['ffprobe'])}] ffprobe")
    L.append(f"[{_flag(i['yt_dlp'], True)}] yt-dlp (only needed to grab thumbnails from URLs)")
    p = i["prefs"]
    L.append("Prefs: " + (json.dumps(p, ensure_ascii=False) if p else "none yet (run the wizard)"))
    cs = i["client_secret"]
    L.append(f"[{_flag(cs['exists'])}] Google OAuth client secret: {cs['file']}")
    t = i["token"]
    if t["valid"]:
        L.append(f"[OK     ] YouTube token: {t['file']} -> channel '{t['channel_title']}' ({t['channel_id']})")
    elif t["exists"]:
        L.append(f"[FAILED ] YouTube token: {t['file']} -> {t['error']}")
    else:
        L.append(f"[MISSING] YouTube token: {t['file']}")
    for k, v in i["stt_keys"].items():
        L.append(f"[{_flag(v, True)}] {C.KEY_VARS[k]} present: {'yes' if v else 'no'}")
    for k, v in i["local_runtimes"].items():
        L.append(f"[{_flag(v, True)}] local runtime {k}")
    L.append(f"Input: SRT always works; fallback when there is no SRT: {i['fallback_mode']} "
             f"({'ready' if i['input_ready'] else 'not ready'})")
    L.append("")
    L.append("Hardware:")
    L.extend("  " + ln for ln in H.format_report({**i["hardware"]}).splitlines())
    L.append("")
    L.append("NEXT STEP: " + i["next_step"])
    return "\n".join(L)


NOT_A_TERMINAL_MSG = (
    "error: --set-key needs an interactive terminal (hidden prompt). Run it yourself in a normal PowerShell "
    "window, cmd or Windows Terminal (macOS/Linux: Terminal). Git Bash/mintty does not work, and neither do "
    "PowerShell ISE, IDLE, an IDE's output pane or piped input. Never pipe a key in and never paste it into a chat.")


def set_key(provider: str) -> int:
    if not sys.stdin.isatty():
        C.eprint(NOT_A_TERMINAL_MSG)
        return 2
    var = C.KEY_VARS[provider]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)  # never fall back to an echoing prompt
            key = getpass.getpass(f"Paste your {provider} API key ({var}); input is hidden: ").strip()
    except getpass.GetPassWarning:
        C.eprint("error: this terminal cannot hide typed input, so the key was NOT read. Use PowerShell, cmd, "
                 "Terminal or another real terminal (Git Bash/mintty does not work). Nothing saved.")
        return 2
    except (EOFError, KeyboardInterrupt):
        C.eprint("\ncancelled; nothing saved.")
        return 2
    if len(key) < 8 or any(ch.isspace() for ch in key) or any(ord(ch) < 33 for ch in key):
        C.eprint("error: that does not look like an API key (empty, too short, or contains whitespace). "
                 "Nothing saved.")
        return 2
    if not key.isascii() or any(ord(ch) > 126 for ch in key):
        C.eprint("error: the key contains non-ASCII characters (an input method such as the Korean IME may have "
                 "been active while pasting). Switch to English input, paste again. Nothing saved.")
        return 2
    try:
        path = C.set_env_key(var, key)
    except (OSError, ValueError) as e:
        C.eprint(f"error: could not save the key: {e}")
        return 2
    print(f"Saved {var} to {path}. The key was not displayed.")
    if C.is_windows():
        print("Note: on Windows this file's permissions are not restricted (chmod has no effect); it is stored in "
              "your user profile folder, so keep that folder private and do not sync or share it.")
    else:
        print("The file is readable only by your user (permissions 600).")
    return 0


def set_prefs(pairs: list[str]) -> int:
    prefs = C.load_prefs()
    for pair in pairs:
        if "=" not in pair:
            C.eprint(f"error: '{pair}' is not KEY=VALUE")
            return 2
        k, _, v = pair.partition("=")
        try:
            prefs[k.strip()] = C.validate_pref(k.strip(), v)
        except ValueError as e:
            C.eprint(f"error: {e}")
            return 2
    path = C.save_prefs(prefs)
    print(f"Saved prefs to {path}")
    print(json.dumps(prefs, ensure_ascii=False, indent=2))
    return 0


def show_prefs() -> int:
    prefs = C.load_prefs()
    print(json.dumps({"path": str(C.prefs_path()), "prefs": prefs,
                      "defaults_for_unset": {k: v for k, v in C.DEFAULT_PREFS.items() if k not in prefs}},
                     ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    C.setup_utf8_io()
    ap = argparse.ArgumentParser(
        description="Preflight checklist and safe setters for youtube-caption-translator.",
        epilog="Exit 0 when the YouTube token is valid and, if input_mode is stt_api or stt_local, that fallback is ready; else 1.")
    ap.add_argument("--json", action="store_true", help="machine-readable preflight output")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--set-key", choices=sorted(C.KEY_VARS), metavar="{deepgram,openai,soniox}",
                   help="store an STT API key via a hidden prompt (run in your own terminal)")
    g.add_argument("--set-pref", nargs="+", metavar="KEY=VALUE", help="store wizard answers")
    g.add_argument("--show-prefs", action="store_true", help="print stored prefs")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2
    if a.set_key:
        return set_key(a.set_key)
    if a.set_pref:
        return set_prefs(a.set_pref)
    if a.show_prefs:
        return show_prefs()
    info = gather()
    print(json.dumps(info, ensure_ascii=False, indent=2) if a.json else format_report(info))
    return 0 if info["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
