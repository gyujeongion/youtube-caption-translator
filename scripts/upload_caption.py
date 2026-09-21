"""Upload an SRT caption track to a YouTube video (updates it if the same language already exists).

    python3 upload_caption.py --video <videoId> --srt <file.srt> \
        --lang en --name "English" [--token my_channel_token.json] [--draft] [--dry-run]

    python3 upload_caption.py --video <videoId> --list   # list current tracks

Stdlib only, no external packages. --token is OPTIONAL: it defaults to the prefs
`token_file`, else my_channel_token.json. It can be a bare filename (resolved
against the config dir: $YTCAPTION_HOME or ~/.claude/credentials/) or an absolute path.

Quota: every run first calls captions.list (50 units), then captions.insert (400)
for a new track or captions.update (450) to replace an existing one. Real cost per
track is therefore ~450 units new, ~500 replacing. The default daily quota is
10,000 units. The script prints "quota used this run ~N units" at the end.
--dry-run authenticates and lists tracks (50 units) but uploads nothing.

If the refresh fails with invalid_grant, the token has expired or been
revoked. Re-authorize with reauth_channel.py.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402
from _common import config_dir  # noqa: E402

# Credentials live in config_dir(): $YTCAPTION_HOME if set, else ~/.claude/credentials
# (the historical default). Resolved at call time, not import time.
API = "https://www.googleapis.com/youtube/v3/captions"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/captions"


QUOTA_LIST, QUOTA_INSERT, QUOTA_UPDATE = 50, 400, 450  # units, per Google's quota table
HTTP_TIMEOUT = 60.0
UPLOAD_TIMEOUT = 300.0


def _body_text(e: urllib.error.HTTPError, limit: int | None = None) -> str:
    try:
        text = e.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - a broken error body must not hide the HTTP status
        text = ""
    return text if limit is None else text[:limit]


def token_path(name: str) -> pathlib.Path:
    return C.resolve_in_config(name)


def access_token(name: str) -> str:
    p = token_path(name)
    try:
        tok = C.read_json(p)
    except FileNotFoundError:
        raise SystemExit(f"Token file not found: {p}\nAuthorize the channel first with reauth_channel.py.") from None
    except ValueError as e:
        raise SystemExit(f"Token file is not valid JSON: {p} ({str(e)[:120]})\n"
                         "Re-authorize with reauth_channel.py.") from None
    cid = tok.get("client_id")
    csec = tok.get("client_secret")
    if not cid:
        secret_name = tok.get("client_secret_file", "")
        cs = C.read_json(C.resolve_in_config(secret_name))
        c = cs.get("installed") or cs.get("web")
        cid, csec = c["client_id"], c["client_secret"]
    data = urllib.parse.urlencode({
        "client_id": cid, "client_secret": csec,
        "refresh_token": tok["refresh_token"], "grant_type": "refresh_token",
    }).encode()
    try:
        return json.load(urllib.request.urlopen(
            "https://oauth2.googleapis.com/token", data, timeout=HTTP_TIMEOUT))["access_token"]
    except urllib.error.HTTPError as e:
        body = _body_text(e)
        if "invalid_grant" in body:
            raise SystemExit(
                f"Token expired or revoked: {p}\n"
                f"Re-authorize with reauth_channel.py.\n{body}")
        raise SystemExit(f"Token refresh failed {e.code}: {body}")


def api_get(url: str, at: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {at}"})
    return json.load(urllib.request.urlopen(req, timeout=HTTP_TIMEOUT))


def list_tracks(video: str, at: str) -> list[dict]:
    q = urllib.parse.urlencode({"part": "snippet", "videoId": video})
    return api_get(f"{API}?{q}", at).get("items", [])


def multipart(meta: dict, srt: pathlib.Path) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    # fixed type (mimetypes.guess_type reads the Windows registry and can answer differently per machine);
    # the SRT is re-encoded as BOM-less UTF-8 with LF newlines whatever the editor/Notepad wrote
    ctype = "application/octet-stream"
    try:
        srt_bytes = C.read_text_file(srt).encode("utf-8")
    except ValueError as e:
        raise SystemExit(f"error: {e}") from None
    body = b"".join([
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode(),
        json.dumps(meta).encode(), b"\r\n",
        f"--{boundary}\r\nContent-Type: {ctype}\r\n\r\n".encode(),
        srt_bytes, b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    return body, f"multipart/related; boundary={boundary}"


def send(url: str, method: str, meta: dict, srt: pathlib.Path, at: str) -> dict:
    body, ctype = multipart(meta, srt)
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": f"Bearer {at}", "Content-Type": ctype,
    })
    try:
        return json.load(urllib.request.urlopen(req, timeout=UPLOAD_TIMEOUT))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Upload failed {e.code}: {_body_text(e, 800)}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Upload an SRT caption track to a YouTube video, or list tracks.")
    ap.add_argument("--video", required=True)
    ap.add_argument("--srt")
    ap.add_argument("--lang", default="en")
    ap.add_argument("--name", default="")
    ap.add_argument("--token", default=None,
                    help="token filename or path (default: prefs token_file, else my_channel_token.json)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--draft", action="store_true",
                    help="upload as a private draft (not visible to viewers)")
    ap.add_argument("--dry-run", action="store_true",
                    help="list tracks and report what would happen; upload nothing")
    C.setup_utf8_io()
    a = ap.parse_args(argv)

    at = access_token(a.token or C.default_token_name())
    tracks = list_tracks(a.video, at)
    used = QUOTA_LIST

    if a.list or not a.srt:
        if not tracks:
            print("No caption tracks")
        for t in tracks:
            s = t["snippet"]
            print(f"{t['id']}  lang={s['language']}  name={s.get('name','')!r}  "
                  f"status={s.get('status')}  draft={s.get('isDraft')}")
        print(f"quota used this run ~{used} units")
        return 0

    srt = C.expand_path(a.srt)
    if not srt.exists():
        raise SystemExit(f"SRT not found: {srt}")

    existing = next((t for t in tracks if t["snippet"]["language"] == a.lang), None)
    q = urllib.parse.urlencode({"part": "snippet", "uploadType": "multipart"})

    if a.dry_run:
        action = "update (replace)" if existing else "create"
        cost = QUOTA_UPDATE if existing else QUOTA_INSERT
        print(f"DRY RUN: would {action} the '{a.lang}' track from {srt}; nothing uploaded.")
        print(f"quota used this run ~{used} units (a real upload would add ~{cost}, total ~{used + cost})")
        return 0

    if existing:
        meta = {"id": existing["id"], "snippet": {"isDraft": a.draft}}
        res = send(f"{UPLOAD}?{q}", "PUT", meta, srt, at)
        used += QUOTA_UPDATE
        print(f"Updated existing '{a.lang}' track -- id={res['id']}")
    else:
        meta = {"snippet": {
            "videoId": a.video, "language": a.lang,
            "name": a.name or a.lang.upper(), "isDraft": a.draft,
        }}
        res = send(f"{UPLOAD}?{q}", "POST", meta, srt, at)
        used += QUOTA_INSERT
        print(f"Created new '{a.lang}' track -- id={res['id']}")

    print(f"  check: https://studio.youtube.com/video/{a.video}/translations")
    print(f"quota used this run ~{used} units")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
