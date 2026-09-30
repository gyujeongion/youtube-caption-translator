"""Download caption tracks that are already on a YouTube video as SRT files.

    python3 download_caption.py --video <videoId> --lang en --out video.en.srt
    python3 download_caption.py --video <videoId> --all --out-dir <folder>   # every track -> <videoId>.<lang>.srt
    python3 download_caption.py --video <videoId> --all --out-dir <folder> --dry-run

Used by the multi-language expansion (step 7 in SKILL.md): the reviewed pivot-language
track that is already published is the timing and meaning skeleton for every other
language, so you do not need the original editor SRT on disk.

Only works for videos on the authorized channel (captions.download needs owner access).
Auto-generated (ASR) tracks are skipped unless --include-asr is given.

Quota: captions.list 50 units + captions.download 200 units per track.
--token follows the same convention as upload_caption.py.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402
from upload_caption import API, HTTP_TIMEOUT, QUOTA_LIST, access_token, list_tracks, raise_for_http  # noqa: E402

QUOTA_DOWNLOAD = 200


def download(track_id: str, at: str) -> bytes:
    q = urllib.parse.urlencode({"tfmt": "srt"})
    req = urllib.request.Request(f"{API}/{track_id}?{q}", headers={"Authorization": f"Bearer {at}"})
    try:
        return urllib.request.urlopen(req, timeout=HTTP_TIMEOUT).read()
    except urllib.error.HTTPError as e:
        raise_for_http(e, "Download", 400)
        raise  # unreachable


def pick_tracks(tracks: list[dict], lang: str | None, include_asr: bool) -> list[dict]:
    """Filter the captions.list items: one language, or all (ASR skipped unless asked)."""
    out = []
    for t in tracks:
        s = t.get("snippet", {})
        if s.get("trackKind") == "asr" and not include_asr:
            continue
        if lang and s.get("language") != lang:
            continue
        out.append(t)
    return out


def main(argv: list[str] | None = None) -> int:
    C.setup_utf8_io()
    ap = argparse.ArgumentParser(description="Download a video's caption tracks as SRT.")
    ap.add_argument("--video", required=True)
    ap.add_argument("--token", default=None,
                    help="token filename or path (default: prefs token_file, else my_channel_token.json)")
    ap.add_argument("--lang", help="language code of the one track to download")
    ap.add_argument("--out", help="output path for --lang (default: <videoId>.<lang>.srt)")
    ap.add_argument("--all", action="store_true", help="download every track into --out-dir")
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--include-asr", action="store_true", help="also download auto-generated (ASR) tracks")
    ap.add_argument("--dry-run", action="store_true", help="list what would be downloaded; download nothing")
    a = ap.parse_args(argv)
    if bool(a.all) == bool(a.lang):
        raise SystemExit("error: give exactly one of --lang LANG or --all")

    at = access_token(a.token or C.default_token_name())
    tracks = pick_tracks(list_tracks(a.video, at), a.lang, a.include_asr or bool(a.lang))
    used = QUOTA_LIST
    if not tracks:
        raise SystemExit(f"no matching caption track on {a.video}" + (f" for '{a.lang}'" if a.lang else ""))

    for t in tracks:
        lang = t["snippet"]["language"]
        if a.all:
            path = C.expand_path(a.out_dir) / f"{a.video}.{lang}.srt"
        else:
            path = C.expand_path(a.out or f"{a.video}.{lang}.srt")
        if a.dry_run:
            print(f"DRY RUN: would download '{lang}' -> {path}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(download(t["id"], at))
        used += QUOTA_DOWNLOAD
        print(f"saved {path}")
    print(f"quota used this run ~{used} units")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
