"""Shared helpers for youtube-caption-translator scripts (Python stdlib only, 3.10+).

Not a CLI. Imported by check_setup.py, detect_hardware.py, transcribe.py and
(for the config directory) by upload_caption.py / reauth_channel.py.

Config directory
    $YTCAPTION_HOME if set, else ~/.claude/credentials/ (the historical default,
    kept for backward compatibility). It holds:
      ytcaption.env          KEY=VALUE lines (DEEPGRAM_API_KEY, OPENAI_API_KEY,
                             SONIOX_API_KEY). Real environment variables win.
      ytcaption_prefs.json   wizard answers (see PREFS_SCHEMA).
      client_secret*.json / *_token.json   Google OAuth files.
"""
from __future__ import annotations

import codecs
import importlib.util
import json
import ntpath
import os
import pathlib
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time

# --------------------------------------------------------------------------
# config dir / env / prefs
# --------------------------------------------------------------------------

ENV_FILE_NAME = "ytcaption.env"
PREFS_FILE_NAME = "ytcaption_prefs.json"
KEY_VARS = {
    "deepgram": "DEEPGRAM_API_KEY",
    "openai": "OPENAI_API_KEY",
    "soniox": "SONIOX_API_KEY",
}

DEFAULT_PREFS = {
    "source_language": "ko",
    "target_languages": ["en"],
    "input_mode": "srt",
    "stt_engine": "",
    "publish_mode": "review",
    "token_file": "my_channel_token.json",
    "client_secret": "client_secret.json",
}

_LANG_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$")

# key -> (kind, allowed)   kind: enum | lang | langlist | str
PREFS_SCHEMA = {
    "source_language": ("lang", None),
    "target_languages": ("langlist", None),
    "input_mode": ("enum", ("srt", "stt_api", "stt_local")),
    "stt_engine": ("enum", ("deepgram", "openai", "soniox", "local", "")),
    "publish_mode": ("enum", ("review", "direct")),
    "token_file": ("str", None),
    "client_secret": ("str", None),
}


def expand_path(value: str | os.PathLike) -> pathlib.Path:
    """User-typed path -> Path, forgiving about Windows habits.

    Strips whitespace and stray double quotes (cmd's `set X="C:\\dir"` keeps the quotes; a
    trailing backslash before the closing quote turns `"C:\\dir\\"` into `C:\\dir"`), expands
    %USERPROFILE%-style variables (PowerShell does not expand them itself) and `~`.
    """
    text = os.fspath(value).strip()
    if is_windows():
        text = text.strip('"').strip()
        text = ntpath.expandvars(text)
    return pathlib.Path(text).expanduser()


def config_dir() -> pathlib.Path:
    """Return the config directory ($YTCAPTION_HOME or ~/.claude/credentials).

    On Windows that is %USERPROFILE%\\.claude\\credentials unless YTCAPTION_HOME is set.
    """
    override = os.environ.get("YTCAPTION_HOME", "").strip()
    if override:
        return expand_path(override)
    return pathlib.Path.home() / ".claude" / "credentials"


def resolve_in_config(name: str) -> pathlib.Path:
    """Bare filename -> config dir; absolute path stays as-is."""
    p = expand_path(name)
    return p if p.is_absolute() else config_dir() / p


def parse_env_text(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if k:
            out[k] = v
    return out


def load_env() -> dict[str, str]:
    """ytcaption.env values, overridden by real environment variables.

    Only the variables we know about (KEY_VARS) plus anything present in the
    file are considered; the rest of os.environ is not copied.
    """
    values: dict[str, str] = {}
    p = config_dir() / ENV_FILE_NAME
    if p.is_file():
        try:
            values.update(parse_env_text(read_text_file(p)))
        except (OSError, ValueError):
            pass
    for k in set(values) | set(KEY_VARS.values()):
        if os.environ.get(k):
            values[k] = os.environ[k]
    return values


def get_key(provider: str) -> str:
    """API key for a cloud provider, or '' (never logged by callers)."""
    return load_env().get(KEY_VARS[provider], "").strip()


def validate_pref(key: str, value: str):
    """Validate one --set-pref value. Returns the normalized value or raises ValueError."""
    if key not in PREFS_SCHEMA:
        raise ValueError(f"unknown pref '{key}' (valid: {', '.join(PREFS_SCHEMA)})")
    kind, allowed = PREFS_SCHEMA[key]
    value = value.strip()
    if kind == "enum":
        if value not in allowed:
            raise ValueError(f"{key} must be one of: {', '.join(a or '(empty)' for a in allowed)}")
        return value
    if kind == "lang":
        if not _LANG_RE.match(value):
            raise ValueError(f"{key} must be a language code like ko, en, ja, pt-BR")
        return value
    if kind == "langlist":
        items = [v.strip() for v in value.split(",") if v.strip()]
        if not items or any(not _LANG_RE.match(v) for v in items):
            raise ValueError(f"{key} must be a comma-separated list of language codes, e.g. en,ja")
        return items
    if not value:
        raise ValueError(f"{key} must not be empty")
    return value


def default_token_name() -> str:
    """Token file name: prefs token_file, else my_channel_token.json (the canonical default)."""
    return str(load_prefs().get("token_file") or DEFAULT_PREFS["token_file"])


def prefs_path() -> pathlib.Path:
    return config_dir() / PREFS_FILE_NAME


def load_prefs() -> dict:
    """Saved prefs (only what is stored; no defaults merged). {} if none."""
    p = prefs_path()
    if not p.is_file():
        return {}
    try:
        data = read_json(p)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_prefs(prefs: dict) -> pathlib.Path:
    p = prefs_path()
    write_private(p, json.dumps(prefs, ensure_ascii=False, indent=2) + "\n")
    return p


def replace_file(src: pathlib.Path, dst: pathlib.Path, attempts: int = 8) -> None:
    """os.replace with a short retry: on Windows the target can be briefly locked by an
    antivirus scanner, the search indexer or a cloud-sync client (PermissionError)."""
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.05 * (i + 1))


def write_private(path: pathlib.Path, text: str) -> None:
    """Atomically write a text file readable only by the owner (chmod 600; best-effort on Windows)."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    # O_BINARY (Windows only) keeps the C runtime from turning "\n" into "\r\n"
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:  # closed before the replace
            f.write(text)
        replace_file(tmp, path)
    finally:
        try:
            tmp.unlink()  # only still there if something above failed
        except OSError:
            pass
    try:
        path.chmod(0o600)
    except OSError:
        pass  # Windows: chmod is best-effort


def set_env_key(name: str, value: str) -> pathlib.Path:
    """Insert/replace NAME=value in ytcaption.env (keeps other lines), chmod 600."""
    p = config_dir() / ENV_FILE_NAME
    lines: list[str] = []
    if p.is_file():
        lines = read_text_file(p).splitlines()
    new_line = f"{name}={value}"
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("export "):
            s = s[len("export "):].lstrip()
        if s.split("=", 1)[0].strip() == name and "=" in s:
            lines[i] = new_line
            break
    else:
        lines.append(new_line)
    write_private(p, "\n".join(lines) + "\n")
    return p


# --------------------------------------------------------------------------
# text files: BOM / CRLF / legacy-encoding tolerant reading
# --------------------------------------------------------------------------

class TextDecodeError(ValueError):
    """A text file is not UTF-8 (or UTF-16 with BOM). The message says how to fix it."""


def decode_text(raw: bytes, name: str = "file") -> str:
    """bytes -> str. Accepts UTF-8 (with or without BOM) and UTF-16 with BOM (Windows Notepad's
    'Unicode'); normalizes CRLF/CR to LF. Anything else raises TextDecodeError instead of guessing
    (a wrong guess between cp949/cp932/cp1252 silently corrupts subtitles)."""
    if raw.startswith(codecs.BOM_UTF8):
        raw = raw[len(codecs.BOM_UTF8):]
    elif raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        try:
            text = raw.decode("utf-16")
        except UnicodeDecodeError:
            raise TextDecodeError(f"{name}: corrupt UTF-16 text") from None
        return text.replace("\r\n", "\n").replace("\r", "\n")
    elif b"\x00" in raw[:4096]:
        raise TextDecodeError(f"{name}: looks like UTF-16 without a BOM. Re-save it as UTF-8 and retry.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise TextDecodeError(
            f"{name} is not UTF-8 text (probably saved in a legacy Windows encoding such as cp949/ANSI). "
            "Re-save it as UTF-8 (Notepad: File > Save As > Encoding: UTF-8; in your editor export the "
            "SRT as UTF-8) and retry.") from None
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_text_file(path: str | os.PathLike) -> str:
    """Read a text file as UTF-8 (BOM/CRLF tolerant, see decode_text)."""
    p = pathlib.Path(path)
    return decode_text(p.read_bytes(), str(p))


def read_json(path: str | os.PathLike):
    """json.loads of read_text_file (a Notepad-added BOM no longer breaks it)."""
    return json.loads(read_text_file(path))


def write_text_lf(path: str | os.PathLike, text: str) -> None:
    """Write UTF-8 (no BOM) with LF newlines on every OS (Windows would otherwise write CRLF)."""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


# --------------------------------------------------------------------------
# SRT helpers
# --------------------------------------------------------------------------

_SRT_TIME = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")


def format_ts(seconds: float) -> str:
    """Seconds -> 'HH:MM:SS,mmm' (rounded to the nearest millisecond)."""
    ms_total = max(0, int(round(seconds * 1000)))
    h, rem = divmod(ms_total, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_ts(stamp: str) -> float:
    m = re.match(r"^\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*$", stamp)
    if not m:
        raise ValueError(f"bad SRT timestamp: {stamp!r}")
    h, mi, s, ms = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def read_srt(path: str | os.PathLike) -> list[dict]:
    """Parse an SRT file -> [{'index', 'start', 'end', 'text'}] (seconds)."""
    raw = read_text_file(path)
    out: list[dict] = []
    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = [ln for ln in block.split("\n")]
        while lines and not lines[0].strip():
            lines.pop(0)
        if len(lines) < 2:
            continue
        tm = _SRT_TIME.search(lines[1])
        if not tm:
            continue
        g = tm.groups()
        start = int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2]) + int(g[3].ljust(3, "0")) / 1000
        end = int(g[4]) * 3600 + int(g[5]) * 60 + int(g[6]) + int(g[7].ljust(3, "0")) / 1000
        out.append({
            "index": lines[0].strip(),
            "start": start,
            "end": end,
            "text": "\n".join(lines[2:]).strip(),
        })
    return out


def srt_text(blocks: list[dict]) -> str:
    parts = []
    for i, b in enumerate(blocks, 1):
        parts.append(f"{i}\n{format_ts(b['start'])} --> {format_ts(b['end'])}\n{b['text']}\n")
    return "\n".join(parts)


def write_srt(path: str | os.PathLike, blocks: list[dict]) -> None:
    """Write UTF-8 (no BOM), LF newlines."""
    write_text_lf(path, srt_text(blocks))


# --------------------------------------------------------------------------
# word / sentence segmenter
# --------------------------------------------------------------------------

SENTENCE_END = tuple(".?!。！？…")
CLAUSE_END = tuple(",;:、，；：")
NO_SPACE_LANGS = ("ja", "zh", "yue", "th")

MAX_BLOCK_SECONDS = 7.0
MAX_LINES = 2
MAX_LINE_CHARS = 42
GAP_BREAK_SECONDS = 0.8
MIN_BLOCK_SECONDS = 1.0


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def wrap_lines(text: str, max_chars: int = MAX_LINE_CHARS, max_lines: int = MAX_LINES) -> list[str] | None:
    """Balanced wrap into <= max_lines lines of <= max_chars each, or None if impossible.

    Splits on spaces; if there are none (ja/zh) or one token is longer than a
    line, falls back to a hard split between characters.
    """
    text = _clean(text)
    if len(text) <= max_chars:
        return [text]
    if max_lines < 2:
        return None
    if len(text) > max_chars * max_lines:
        return None
    # candidate split points: after each space; else any char boundary
    spaces = [i for i, ch in enumerate(text) if ch == " "]
    best: tuple[int, int] | None = None  # (score, index)
    mid = len(text) / 2
    for i in spaces:
        a, b = text[:i].rstrip(), text[i + 1:].lstrip()
        if not a or not b or len(a) > max_chars or len(b) > max_chars:
            continue
        score = abs(len(a) - len(b))
        if a.endswith(SENTENCE_END + CLAUSE_END):
            score -= 6  # prefer breaking after punctuation
        if best is None or score < best[0]:
            best = (score, i)
    if best is not None:
        i = best[1]
        return [text[:i].rstrip(), text[i + 1:].lstrip()]
    if spaces:
        return None  # spaces exist but no split fits: caller must end the block earlier
    # no spaces at all (ja/zh or one huge token): split between characters
    cut = int(round(mid))
    a, b = text[:cut].rstrip(), text[cut:].lstrip()
    if 0 < len(a) <= max_chars and 0 < len(b) <= max_chars:
        return [a, b]
    return None


def segments_to_words(segments: list[dict]) -> list[dict]:
    """Segment list [{start,end,text}] -> pseudo-words with interpolated times.

    For engines that only give phrase-level timing. Each whitespace-separated
    token gets a slice of the segment proportional to its character count
    (text without spaces, e.g. ja/zh, is split per character group of 1).
    """
    words: list[dict] = []
    for seg in segments:
        text = _clean(seg.get("text", ""))
        if not text:
            continue
        start, end = float(seg["start"]), float(seg["end"])
        if end < start:
            end = start
        toks = text.split(" ")
        split_nospace = False
        if len(toks) == 1 and len(text) > 12:
            split_nospace = True
            # no spaces (ja/zh or long token): split into ~6-char pieces so the
            # segmenter has break points
            toks = [text[i:i + 6] for i in range(0, len(text), 6)]
        total = sum(len(t) for t in toks) or 1
        acc = 0
        for t in toks:
            w0 = start + (end - start) * acc / total
            acc += len(t)
            w1 = start + (end - start) * acc / total
            words.append({"start": w0, "end": w1, "text": t, "cont": split_nospace and acc > len(t)})
    return words


def _join(ws: list[dict], glue: bool) -> str:
    """Join word dicts; no space in ja/zh mode or before a 'cont' (continuation) piece."""
    out = ""
    for i, w in enumerate(ws):
        if i and not glue and not w.get("cont"):
            out += " "
        out += w["text"]
    return out


def segment_words(
    words: list[dict],
    *,
    max_seconds: float = MAX_BLOCK_SECONDS,
    max_lines: int = MAX_LINES,
    max_line_chars: int = MAX_LINE_CHARS,
    gap_seconds: float = GAP_BREAK_SECONDS,
    lang: str = "ko",
    min_seconds: float = MIN_BLOCK_SECONDS,
) -> list[dict]:
    """Group timed words into subtitle blocks.

    words: [{'start': sec, 'end': sec, 'text': str}], chronological.
    Returns [{'start', 'end', 'text'}] where text contains '\n' between lines.

    Rules: a block never exceeds max_seconds, max_lines lines, or
    max_line_chars chars per line; it breaks after sentence punctuation
    (. ? ! 。 ！ ？ …), after a comma once it is reasonably full, and before a
    silence longer than gap_seconds. If words carry a 'speaker' label, a block also ends at
    every speaker change (blocks never mix speakers). Blocks shorter than min_seconds are
    extended into following silence when possible (never overlapping).
    """
    glue = lang.split("-")[0].lower() in NO_SPACE_LANGS
    ws = []
    prev_end = 0.0
    for w in words:
        t = _clean(str(w.get("text", "")))
        if not t:
            continue
        s = max(float(w["start"]), prev_end if ws else 0.0)
        e = max(float(w["end"]), s)
        ws.append({"start": s, "end": e, "text": t, "cont": bool(w.get("cont")), "speaker": w.get("speaker")})
        prev_end = e

    cap = max_line_chars * max_lines
    blocks: list[dict] = []
    cur: list[dict] = []

    def fits(cand: list[dict]) -> bool:
        if cand[-1]["end"] - cand[0]["start"] > max_seconds and len(cand) > 1:
            return False
        txt = _join(cand, glue)
        if len(txt) > cap:
            return False
        return wrap_lines(txt, max_line_chars, max_lines) is not None

    def flush() -> None:
        nonlocal cur
        if not cur:
            return
        txt = _join(cur, glue)
        lines = wrap_lines(txt, max_line_chars, max_lines)
        if lines is None:
            # a single token longer than the whole block: cut it into block-sized
            # pieces and share the time proportionally, so no text is dropped
            step = max_line_chars * max_lines
            pieces = [txt[i:i + step] for i in range(0, len(txt), step)]
            t0, span = cur[0]["start"], cur[-1]["end"] - cur[0]["start"]
            done = 0
            for piece in pieces:
                a = t0 + span * done / len(txt)
                done += len(piece)
                b = t0 + span * done / len(txt)
                half = (len(piece) + 1) // 2 if len(piece) > max_line_chars else len(piece)
                blocks.append({"start": a, "end": b,
                               "text": "\n".join(x for x in (piece[:half], piece[half:]) if x)})
        else:
            blocks.append({"start": cur[0]["start"], "end": cur[-1]["end"], "text": "\n".join(lines)})
        cur = []

    for w in ws:
        if cur:
            gap = w["start"] - cur[-1]["end"]
            prev_text = cur[-1]["text"]
            cur_len = len(_join(cur, glue))
            if gap > gap_seconds:
                flush()
            elif (w["speaker"] is not None and cur[-1]["speaker"] is not None
                  and w["speaker"] != cur[-1]["speaker"]):
                flush()  # never put two speakers in one subtitle block
            elif prev_text.endswith(SENTENCE_END):
                flush()
            elif prev_text.endswith(CLAUSE_END) and cur_len >= max_line_chars * 0.7:
                flush()
            elif not fits(cur + [w]):
                flush()
        cur.append(w)
    flush()

    # give very short blocks a minimum on-screen time without overlapping
    for i, b in enumerate(blocks):
        if b["end"] - b["start"] < min_seconds:
            limit = blocks[i + 1]["start"] if i + 1 < len(blocks) else b["start"] + min_seconds
            b["end"] = min(b["start"] + min_seconds, max(limit, b["end"]))
    return blocks


# --------------------------------------------------------------------------
# speaker diarization helpers
# --------------------------------------------------------------------------

def normalize_speakers(words: list[dict]) -> list[dict]:
    """Relabel engine speaker ids (ints, '1', 'A', ...) as S1, S2, ... in order of first appearance.

    Returns new word dicts; words without a speaker keep speaker None. Input is not modified.
    """
    labels: dict[str, str] = {}
    out = []
    for w in words:
        w2 = dict(w)
        raw = w.get("speaker")
        if raw is not None:
            key = str(raw)
            if key not in labels:
                labels[key] = f"S{len(labels) + 1}"
            w2["speaker"] = labels[key]
        out.append(w2)
    return out


def speaker_turns(words: list[dict]) -> list[dict]:
    """Merge consecutive same-speaker words -> [{'start', 'end', 'speaker'}] (seconds, 3 decimals).

    Words with no speaker label are attached to the speaker of the previous labelled word
    (or the next one at the very start); if no word has a label the result is [].
    """
    labelled = [w for w in words if w.get("speaker") is not None]
    if not labelled:
        return []
    first = labelled[0]["speaker"]
    turns: list[dict] = []
    cur_spk = first
    for w in words:
        spk = w.get("speaker")
        spk = cur_spk if spk is None else spk
        if turns and spk == turns[-1]["speaker"]:
            turns[-1]["end"] = max(turns[-1]["end"], float(w["end"]))
        else:
            turns.append({"start": float(w["start"]), "end": float(w["end"]), "speaker": spk})
        cur_spk = spk
    for t in turns:
        t["start"], t["end"] = round(t["start"], 3), round(t["end"], 3)
    return turns


# --------------------------------------------------------------------------
# multipart + misc
# --------------------------------------------------------------------------

def encode_multipart(fields: dict[str, str | list[str]], file_field: str,
                     file_path: str | os.PathLike, content_type: str) -> tuple[bytes, str]:
    """Build a multipart/form-data body. Repeated fields: pass a list of values.

    The uploaded filename is a fixed 'audio<ext>' so local paths/names never leak to providers.
    """
    boundary = secrets.token_hex(16)
    chunks: list[bytes] = []
    for name, val in fields.items():
        for v in (val if isinstance(val, list) else [val]):
            chunks.append(
                (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{v}\r\n').encode("utf-8"))
    p = pathlib.Path(file_path)
    upload_name = "audio" + (p.suffix.lower() if re.fullmatch(r"\.[A-Za-z0-9]{1,5}", p.suffix) else "")
    chunks.append(
        (f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{upload_name}"\r\n'
         f"Content-Type: {content_type}\r\n\r\n").encode("utf-8"))
    chunks.append(p.read_bytes())
    chunks.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def setup_utf8_io() -> None:
    """Make stdout/stderr UTF-8 (errors replaced) so Korean/check marks never crash cp1252/cp949 consoles.

    Call at the start of every CLI main. Streams without reconfigure() (e.g. StringIO) are left alone.
    """
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if reconf is not None:
            try:
                reconf(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def is_windows() -> bool:
    return os.name == "nt"


def _same_path(a: str, b: str) -> bool:
    """Windows path equality (case-insensitive, slash-insensitive) without touching the disk."""
    return ntpath.normcase(ntpath.normpath(a)) == ntpath.normcase(ntpath.normpath(b))


def _windows_python_cmd() -> str:
    """How to type this interpreter in a Windows terminal.

    `python` only if it resolves to THIS interpreter (on a fresh Windows `python` may be the
    Microsoft Store stub that opens the Store, and python.org installs have no `python3`); else the
    `py` launcher pinned to this version; inside a venv (py would pick another Python) the full path.
    """
    exe = sys.executable or ""
    found = shutil.which("python")
    if found and exe and _same_path(found, exe):
        return "python"
    in_venv = getattr(sys, "base_prefix", sys.prefix) != sys.prefix
    if not in_venv and shutil.which("py"):
        return f"py -{sys.version_info.major}.{sys.version_info.minor}"
    if exe:
        return f'"{exe}"'
    return "python"


def python_cmd() -> str:
    """Interpreter name to show in printed commands (python / python3 / py-style), from sys.executable."""
    if is_windows():
        return _windows_python_cmd()
    base = pathlib.Path(sys.executable or "").name.lower()
    if base.endswith(".exe"):
        base = base[:-4]
    m = re.fullmatch(r"python(\d)(\.\d+)*", base)
    if m:
        return f"python{m.group(1)}"
    if base == "python":
        return "python"
    return "python3"


def windows_python_notes() -> list[str]:
    """Advice lines about the Microsoft Store `python` stub / py launcher (Windows only, else [])."""
    if not is_windows():
        return []
    notes: list[str] = []
    found = shutil.which("python")
    exe = sys.executable or ""
    stub = bool(found) and "\\windowsapps\\" in ntpath.normcase(found) and not (exe and _same_path(found, exe))
    if stub:
        notes.append(
            "Typing 'python' here may open the Microsoft Store instead of running Python (the Store shortcut "
            "is first on PATH). Use the py launcher (py -3) or switch off 'App execution aliases' for python.exe "
            "in Windows Settings > Apps > Advanced app settings.")
    elif not found and shutil.which("py"):
        notes.append("'python' is not on PATH, but the py launcher is: type py -3 instead of python.")
    return notes


def ffmpeg_missing_message() -> str:
    return ("ffmpeg not found. Install ffmpeg from https://ffmpeg.org/download.html or with your package manager "
            "(brew install ffmpeg / apt install ffmpeg / choco install ffmpeg), then re-run. "
            "On Windows, open a NEW terminal window after installing so PATH picks it up "
            "(a manual download must have its bin folder added to PATH).")


def find_ytdlp() -> list[str] | None:
    """argv prefix that runs yt-dlp: the executable on PATH (PATHEXT-aware), else `python -m yt_dlp`
    when the pip package is importable (pip's Scripts folder is often not on PATH on Windows)."""
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    return None


def require_exe(name: str) -> str:
    """Full path of an external tool (PATHEXT-aware) or SystemExit with install advice."""
    exe = shutil.which(name)
    if exe:
        return exe
    if name in ("ffmpeg", "ffprobe"):
        raise SystemExit(ffmpeg_missing_message())
    raise SystemExit(f"{name} not found on PATH.")


def media_arg(path: str | os.PathLike) -> str:
    """Path as a command-line argument for ffmpeg/ffprobe/yt-dlp: absolute, so a relative name
    that starts with '-' can never be read as an option, and never a bare 'C:name' drive-relative
    form. (Callers always pass argv lists, never shell strings.)"""
    return os.path.abspath(os.fspath(path))


def format_cmd(argv: list[str]) -> str:
    """argv -> a string that is safe to show and paste into the current shell family."""
    if is_windows():
        return subprocess.list2cmdline([str(a) for a in argv])
    return shlex.join(str(a) for a in argv)


def path_length_warning(*paths: str | os.PathLike) -> str | None:
    """Windows only: warning text when a path is near the classic 260-character MAX_PATH limit."""
    if not is_windows():
        return None
    longest = max((len(os.path.abspath(os.fspath(p))) for p in paths), default=0)
    if longest >= 240:
        return (f"a path is {longest} characters long; Windows may refuse paths over 259 characters unless "
                "long paths are enabled. If it fails, move the files to a short folder such as C:\\captions.")
    return None


def remove_tree(path: str | os.PathLike) -> None:
    """Best-effort rmtree that survives Windows' read-only files and briefly-locked handles."""
    def _unlock(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    kw = {"onexc": _unlock} if sys.version_info >= (3, 12) else {"onerror": _unlock}
    for i in range(4):
        shutil.rmtree(path, **kw)
        if not os.path.exists(path):
            return
        time.sleep(0.15 * (i + 1))


def eprint(*a) -> None:
    print(*a, file=sys.stderr)


def make_temp_dir(prefix: str = "ytcaption_") -> pathlib.Path:
    return pathlib.Path(tempfile.mkdtemp(prefix=prefix))
