"""Authorize a single YouTube channel and write a refresh-token file (stdlib only).

    python3 reauth_channel.py --token my_channel_token.json \
        --secret client_secret.json \
        --expect-channel UCxxxxxxxxxxxxxxxxxxxxxx

A browser consent screen opens. Whichever account/brand-account you pick
there decides which channel the token belongs to -- pick wrong and captions
get uploaded to the wrong channel. So right after auth this script checks
the channel ID and refuses to save if it doesn't match --expect-channel.

If the GCP OAuth app is still in "Testing" status, refresh tokens expire
after 7 days. To keep using them long-term, publish the OAuth consent
screen's Audience to "In production".
"""
from __future__ import annotations

import argparse
import http.server
import json
import pathlib
import secrets
import threading
import urllib.parse
import urllib.request
import webbrowser

CRED = pathlib.Path.home() / ".claude" / "credentials"
SCOPES = "https://www.googleapis.com/auth/youtube.force-ssl"
AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"


class Catcher(http.server.BaseHTTPRequestHandler):
    code: str | None = None
    state: str = ""

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        ok = q.get("state", [""])[0] == Catcher.state and "code" in q
        if ok:
            Catcher.code = q["code"][0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        msg = "Authorized. You can close this tab." if ok else "Authorization failed."
        self.wfile.write(f"<html><body><h2>{msg}</h2></body></html>".encode())

    def log_message(self, *_):
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True, help="output token filename")
    ap.add_argument("--secret", required=True,
                     help="OAuth client_secret.json (Google Cloud Console download)")
    ap.add_argument("--expect-channel", default="",
                     help="refuse to save unless the authorized channel ID matches this")
    a = ap.parse_args()

    sec_path = pathlib.Path(a.secret)
    sec_path = sec_path if sec_path.is_absolute() else CRED / a.secret
    cs = json.loads(sec_path.read_text())
    c = cs.get("installed") or cs.get("web")
    cid, csec = c["client_id"], c["client_secret"]

    Catcher.state = secrets.token_urlsafe(16)
    srv = http.server.HTTPServer(("127.0.0.1", 0), Catcher)
    port = srv.server_address[1]
    redirect = f"http://localhost:{port}"
    threading.Thread(target=srv.handle_request, daemon=True).start()

    url = AUTH + "?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code",
        "scope": SCOPES, "access_type": "offline", "prompt": "consent",
        "state": Catcher.state,
    })
    print("Approve in the browser. If it doesn't open, visit this URL:\n" + url)
    webbrowser.open(url)

    for _ in range(600):
        if Catcher.code:
            break
        import time
        time.sleep(0.5)
    if not Catcher.code:
        raise SystemExit("Timed out waiting for the authorization code.")

    data = urllib.parse.urlencode({
        "code": Catcher.code, "client_id": cid, "client_secret": csec,
        "redirect_uri": redirect, "grant_type": "authorization_code",
    }).encode()
    tok = json.load(urllib.request.urlopen(TOKEN, data))
    at, rt = tok["access_token"], tok.get("refresh_token")
    if not rt:
        raise SystemExit("No refresh_token returned. Revoke the app's access and retry.")

    req = urllib.request.Request(
        "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
        headers={"Authorization": f"Bearer {at}"})
    items = json.load(urllib.request.urlopen(req)).get("items", [])
    if not items:
        raise SystemExit("This account has no channel. Check the account picked on the consent screen.")
    got_id = items[0]["id"]
    got_title = items[0]["snippet"]["title"]
    print(f"Authorized channel: {got_title} ({got_id})")

    if a.expect_channel and got_id != a.expect_channel:
        raise SystemExit(
            f"Channel mismatch -- expected {a.expect_channel}, got {got_id}. Not saving.")

    out = pathlib.Path(a.token)
    out = out if out.is_absolute() else CRED / a.token
    out.write_text(json.dumps({
        "refresh_token": rt, "client_id": cid, "client_secret": csec,
        "token_uri": TOKEN, "scopes": [SCOPES],
        "channel_id": got_id, "channel_title": got_title,
    }, ensure_ascii=False, indent=2))
    out.chmod(0o600)
    print(f"Saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
