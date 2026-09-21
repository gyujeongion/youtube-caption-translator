"""Shared test helpers: put scripts/ on sys.path, fake HTTP, temp config dir."""
from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest.mock as mock

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

SECRET_ENV = ("DEEPGRAM_API_KEY", "OPENAI_API_KEY", "SONIOX_API_KEY", "YTCAPTION_HOME", "YTCAPTION_MODEL_DIR")
FAKE_KEY = "test-key-not-real-0123456789"


@contextlib.contextmanager
def temp_home(**extra_env):
    """Fresh YTCAPTION_HOME with all key env vars removed (never touches ~/.claude)."""
    with tempfile.TemporaryDirectory() as d:
        env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV}
        env["YTCAPTION_HOME"] = d
        env.update(extra_env)
        with mock.patch.dict(os.environ, env, clear=True):
            yield pathlib.Path(d)


class FakeResp:
    def __init__(self, body=b"", status=200):
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.status = status

    def read(self):
        return self._body

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeNet:
    """Callable replacement for urllib.request.urlopen; records every request.

    handler(method, url, headers, data) -> FakeResp | Exception
    """

    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def __call__(self, req, timeout=None, **kw):
        headers = {k.lower(): v for k, v in req.header_items()}
        rec = {"method": req.get_method(), "url": req.full_url, "headers": headers, "data": req.data}
        self.calls.append(rec)
        out = self.handler(rec["method"], rec["url"], headers, req.data)
        if isinstance(out, Exception):
            raise out
        return out


def quiet():
    return contextlib.redirect_stderr(io.StringIO())
