"""Upload an SRT caption track to a YouTube video (updates it if the same language already exists).

    python3 upload_caption.py --video <videoId> --srt <file.srt> \
        --lang en --name "English" --token youtube_token.json

    python3 upload_caption.py --video <videoId> --list   # list current tracks

Stdlib only, no external packages. --token can be a bare filename (resolved
against ~/.claude/credentials/) or an absolute path.

If the refresh fails with invalid_grant, the token has expired or been
revoked. Re-authorize with reauth_channel.py.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import pathlib
import urllib.error
import urllib.parse
import urllib.request
import uuid

CRED = pathlib.Path.home() / ".claude" / "credentials"
API = "https://www.googleapis.com/youtube/v3/captions"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/captions"


def token_path(name: str) -> pathlib.Path:
    p = pathlib.Path(name)
    return p if p.is_absolute() else CRED / name


def access_token(name: str) -> str:
    p = token_path(name)
    tok = json.loads(p.read_text())
    cid = tok.get("client_id")
    csec = tok.get("client_secret")
    if not cid:
        secret_name = tok.get("client_secret_file", "")
        cs = json.loads((CRED / secret_name).read_text())
        c = cs.get("installed") or cs.get("web")
        cid, csec = c["client_id"], c["client_secret"]
    data = urllib.parse.urlencode({
        "client_id": cid, "client_secret": csec,
        "refresh_token": tok["refresh_token"], "grant_type": "refresh_token",
    }).encode()
    try:
        return json.load(urllib.request.urlopen(
            "https://oauth2.googleapis.com/token", data))["access_token"]
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if "invalid_grant" in body:
            raise SystemExit(
                f"Token expired or revoked: {p}\n"
                f"Re-authorize with reauth_channel.py.\n{body}")
        raise SystemExit(f"Token refresh failed {e.code}: {body}")


def api_get(url: str, at: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {at}"})
    return json.load(urllib.request.urlopen(req))


def list_tracks(video: str, at: str) -> list[dict]:
    q = urllib.parse.urlencode({"part": "snippet", "videoId": video})
    return api_get(f"{API}?{q}", at).get("items", [])


def multipart(meta: dict, srt: pathlib.Path) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    ctype = mimetypes.guess_type(srt.name)[0] or "application/octet-stream"
    body = b"".join([
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode(),
        json.dumps(meta).encode(), b"\r\n",
        f"--{boundary}\r\nContent-Type: {ctype}\r\n\r\n".encode(),
        srt.read_bytes(), b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    return body, f"multipart/related; boundary={boundary}"


def send(url: str, method: str, meta: dict, srt: pathlib.Path, at: str) -> dict:
    body, ctype = multipart(meta, srt)
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": f"Bearer {at}", "Content-Type": ctype,
    })
    try:
        return json.load(urllib.request.urlopen(req))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Upload failed {e.code}: {e.read().decode()[:800]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--srt")
    ap.add_argument("--lang", default="en")
    ap.add_argument("--name", default="")
    ap.add_argument("--token", required=True, help="token filename or path (see reauth_channel.py)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--draft", action="store_true",
                    help="upload as a private draft (not visible to viewers)")
    a = ap.parse_args()

    at = access_token(a.token)
    tracks = list_tracks(a.video, at)

    if a.list or not a.srt:
        if not tracks:
            print("No caption tracks")
        for t in tracks:
            s = t["snippet"]
            print(f"{t['id']}  lang={s['language']}  name={s.get('name','')!r}  "
                  f"status={s.get('status')}  draft={s.get('isDraft')}")
        return 0

    srt = pathlib.Path(a.srt)
    if not srt.exists():
        raise SystemExit(f"SRT not found: {srt}")

    existing = next((t for t in tracks if t["snippet"]["language"] == a.lang), None)
    q = urllib.parse.urlencode({"part": "snippet", "uploadType": "multipart"})

    if existing:
        meta = {"id": existing["id"], "snippet": {"isDraft": a.draft}}
        res = send(f"{UPLOAD}?{q}", "PUT", meta, srt, at)
        print(f"Updated existing '{a.lang}' track -- id={res['id']}")
    else:
        meta = {"snippet": {
            "videoId": a.video, "language": a.lang,
            "name": a.name or a.lang.upper(), "isDraft": a.draft,
        }}
        res = send(f"{UPLOAD}?{q}", "POST", meta, srt, at)
        print(f"Created new '{a.lang}' track -- id={res['id']}")

    print(f"  check: https://studio.youtube.com/video/{a.video}/translations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
