"""Authorize a single YouTube channel and write a refresh-token file (stdlib only).

    python3 reauth_channel.py --token my_channel_token.json \
        --secret client_secret.json \
        --expect-channel UCxxxxxxxxxxxxxxxxxxxxxx

A browser consent screen opens. Whichever account/brand-account you pick
there decides which channel the token belongs to -- pick wrong and captions
get uploaded to the wrong channel. So right after auth this script checks
the channel ID and refuses to save if it doesn't match --expect-channel.

--token / --secret are bare filenames (resolved against the config dir:
$YTCAPTION_HOME or ~/.claude/credentials/) or absolute paths. The token file
is written atomically with owner-only permissions.

The script listens on http://127.0.0.1:<random port> for Google's redirect and
waits up to 10 minutes. Unrelated requests (favicon, stale tabs, wrong state)
are ignored.

If the GCP OAuth app is still in "Testing" status, refresh tokens expire
after 7 days. To keep using them long-term, publish the OAuth consent
screen's Audience to "In production".
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import pathlib
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402
from _common import config_dir  # noqa: E402

# Credentials live in config_dir(): $YTCAPTION_HOME if set, else ~/.claude/credentials
# (the historical default). Resolved at call time, not import time.
SCOPES = "https://www.googleapis.com/auth/youtube.force-ssl"
AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
AUTH_TIMEOUT_SECONDS = 600.0
HTTP_TIMEOUT = 60.0


class Catcher(http.server.BaseHTTPRequestHandler):
    """Collects the redirect. Result lives on self.server.auth (a dict)."""

    def _reply(self, status: int, msg: str) -> None:
        body = f"<html><head><meta charset=\"utf-8\"></head><body><h2>{msg}</h2></body></html>".encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        auth = self.server.auth  # type: ignore[attr-defined]
        parsed = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(parsed.query)
        if "code" not in q and "error" not in q:
            self._reply(404, "Nothing to see here.")  # favicon, prefetch, stray hit
            return
        if q.get("state", [""])[0] != auth["state"]:
            print("Note: ignored a browser request whose state did not match (stale tab?). Still waiting...")
            self._reply(400, "This authorization link is stale. Go back to the terminal and follow the newest link.")
            return
        if "error" in q:
            auth["error"] = q["error"][0]
            self._reply(200, "Authorization was not granted. You can close this tab.")
            return
        auth["code"] = q["code"][0]
        self._reply(200, "Authorized. You can close this tab.")

    def log_message(self, *_):
        pass


class LoopbackServer(http.server.HTTPServer):
    """127.0.0.1-only one-shot listener.

    allow_reuse_address is switched off: on Windows SO_REUSEADDR lets another process bind the same
    port and receive our redirect (on POSIX it only permits re-binding during TIME_WAIT, which a
    random port never needs). Binding to loopback does not trigger the Windows Firewall prompt.
    """
    allow_reuse_address = False

    def handle_error(self, request, client_address):  # noqa: D401
        # a browser that closes the tab mid-response (ConnectionAbortedError/WinError 10053) is not an error
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def wait_for_code(srv: http.server.HTTPServer, timeout: float) -> dict:
    """Serve requests until a valid code/error arrives or the deadline passes."""
    srv.timeout = 1.0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if srv.auth["code"] or srv.auth["error"]:  # type: ignore[attr-defined]
            break
        srv.handle_request()
    return srv.auth  # type: ignore[attr-defined]


def read_client_secret(path: pathlib.Path) -> tuple[str, str]:
    try:
        cs = C.read_json(path)
        c = cs.get("installed") or cs.get("web")
        return c["client_id"], c["client_secret"]
    except FileNotFoundError:
        raise SystemExit(f"OAuth client secret not found: {path}\n"
                         "Download it from Google Cloud Console (see references/google-cloud-setup.md) and save it "
                         "there, or pass --secret with the full path.") from None
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise SystemExit(f"Cannot read the OAuth client secret {path}: {type(e).__name__}: {str(e)[:200]}. "
                         "It must be the JSON downloaded from Google Cloud Console (a 'Desktop app' client).") from None


def open_browser(url: str) -> bool:
    """webbrowser.open that never raises; False means 'no browser was started' (headless, blocked, ...)."""
    try:
        return bool(webbrowser.open(url))
    except Exception:  # noqa: BLE001 - any failure just means: the user opens the printed URL by hand
        return False


def main(argv: list[str] | None = None) -> int:
    C.setup_utf8_io()
    ap = argparse.ArgumentParser(description="Authorize a YouTube channel and save a refresh-token file.")
    ap.add_argument("--token", required=True, help="output token filename")
    ap.add_argument("--secret", required=True,
                    help="OAuth client_secret.json (Google Cloud Console download)")
    ap.add_argument("--expect-channel", default="",
                    help="refuse to save unless the authorized channel ID matches this")
    a = ap.parse_args(argv)

    sec_path = C.resolve_in_config(a.secret)
    cid, csec = read_client_secret(sec_path)

    state = secrets.token_urlsafe(16)
    srv = LoopbackServer(("127.0.0.1", 0), Catcher)
    srv.auth = {"code": None, "error": None, "state": state}  # type: ignore[attr-defined]
    port = srv.server_address[1]
    redirect = f"http://127.0.0.1:{port}"

    url = AUTH + "?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code",
        "scope": SCOPES, "access_type": "offline", "prompt": "consent",
        "state": state,
    })
    print("Approve in the browser. If it doesn't open, copy this whole URL (it may wrap over several lines) "
          "into a browser:\n" + url)
    if not open_browser(url):
        print("Note: no browser could be started automatically; open the URL above by hand.")
    minutes = int(AUTH_TIMEOUT_SECONDS // 60)
    print(f"Waiting up to {minutes} minutes for you to finish in the browser...")
    sys.stdout.flush()

    try:
        result = wait_for_code(srv, AUTH_TIMEOUT_SECONDS)
    except KeyboardInterrupt:
        raise SystemExit("\ncancelled (Ctrl+C); nothing saved.") from None
    finally:
        srv.server_close()
    if result["error"]:
        raise SystemExit(
            f"Authorization was not granted (error={result['error']}). Run this command again and click Allow.")
    if not result["code"]:
        raise SystemExit(f"Timed out after {minutes} minutes waiting for the browser. Run this command again.")

    data = urllib.parse.urlencode({
        "code": result["code"], "client_id": cid, "client_secret": csec,
        "redirect_uri": redirect, "grant_type": "authorization_code",
    }).encode()
    try:
        tok = json.load(urllib.request.urlopen(TOKEN, data, timeout=HTTP_TIMEOUT))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Token exchange failed (HTTP {e.code}): {e.read().decode('utf-8', 'replace')[:300]}")
    except (urllib.error.URLError, OSError) as e:
        raise SystemExit(f"Token exchange failed: network error ({type(e).__name__}: {str(e)[:150]}). "
                         "Check the internet connection/proxy and run this command again.") from None
    at, rt = tok["access_token"], tok.get("refresh_token")
    if not rt:
        raise SystemExit("No refresh_token returned. Revoke the app's access and retry.")

    req = urllib.request.Request(
        "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
        headers={"Authorization": f"Bearer {at}"})
    try:
        items = json.load(urllib.request.urlopen(req, timeout=HTTP_TIMEOUT)).get("items", [])
    except (urllib.error.URLError, OSError) as e:
        raise SystemExit(f"Channel lookup failed ({type(e).__name__}: {str(e)[:150]}). Run this command again.") from None
    if not items:
        raise SystemExit("This account has no channel. Check the account picked on the consent screen.")
    got_id = items[0]["id"]
    got_title = items[0]["snippet"]["title"]
    print(f"Authorized channel: {got_title} ({got_id})")

    if a.expect_channel and got_id != a.expect_channel:
        raise SystemExit(
            f"Channel mismatch -- expected {a.expect_channel}, got {got_id}. Not saving.")

    out = C.resolve_in_config(a.token)
    C.write_private(out, json.dumps({
        "refresh_token": rt, "client_id": cid, "client_secret": csec,
        "token_uri": TOKEN, "scopes": [SCOPES],
        "channel_id": got_id, "channel_title": got_title,
    }, ensure_ascii=False, indent=2))
    print(f"Saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
