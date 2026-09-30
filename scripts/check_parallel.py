"""Check a translated SRT against the pivot track it was translated from (multi-language expansion).

    python3 check_parallel.py video.en.srt video.ja.srt --lang ja
    python3 check_parallel.py video.en.srt video.es.srt                       # --lang from file name
    python3 check_parallel.py video.en.srt video.fr.srt --keep-same 95,144    # blocks that stay in the pivot language

The multi-language expansion reuses the reviewed pivot track's merged blocks 1:1 (no
new boundaries), so this replaces verify_srt.py for those files. It checks:

- same number of blocks, same order, identical timecodes as the pivot track
- no empty block; no leftover source-language script (--source-lang, default: prefs
  source_language) and no block left identical to the pivot (= probably untranslated)
- at most 2 lines per block, per-language line-length limits (CJK counts characters)
- warnings only: capitalized proper nouns from the pivot missing in the translation

Blocks that SHOULD stay as-is are exempt from the "untranslated" error: lyrics marked
with a music note, [bracketed] song titles / sound cues, and every block you list in
--keep-same (a pun, a brand phrase, an English word spoken on camera). Mark those
explicitly instead of letting a translator bend the text just to pass this check.

Exit code 1 when there are errors, 0 otherwise. No network, no quota.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402

# max display width per line; CJK: one character = one cell
LINE_LIMITS = {"ja": 18, "zh": 18, "ko": 20, "th": 40}
DEFAULT_LIMIT = 44
SOURCE_SCRIPT = {
    "ko": re.compile(r"[\uac00-\ud7a3]"),
    "ja": re.compile(r"[\u3040-\u30ff]"),
    "th": re.compile(r"[\u0e00-\u0e7f]"),
    "ru": re.compile(r"[\u0400-\u04ff]"),
    "ar": re.compile(r"[\u0600-\u06ff]"),
    "hi": re.compile(r"[\u0900-\u097f]"),
}
TIMECODE = re.compile(r"(\d{1,2}:\d\d:\d\d[,.]\d{1,3})\s*-->\s*(\d{1,2}:\d\d:\d\d[,.]\d{1,3})")
PROPER = re.compile(r"\b[A-Z][A-Za-z0-9\u00C0-\u024F]*[A-Z][A-Za-z0-9\u00C0-\u024F]*\b")
MUSIC = ("\u266a", "\u266b")


def parse_srt(text: str) -> list[tuple[str, str, list[str]]]:
    """SRT text -> [(start, end, lines)], timecodes normalized to HH:MM:SS,mmm."""
    out = []
    for blk in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n").strip()):
        lines = blk.split("\n")
        idx = next((k for k, line in enumerate(lines) if TIMECODE.search(line)), None)
        if idx is None:
            continue
        m = TIMECODE.search(lines[idx])
        out.append((_norm(m.group(1)), _norm(m.group(2)), [ln for ln in lines[idx + 1:] if ln.strip()]))
    return out


def _norm(tc: str) -> str:
    hms, _, ms = tc.replace(".", ",").partition(",")
    h, m, s = hms.split(":")
    return f"{int(h):02d}:{m}:{s},{ms.ljust(3, '0')[:3]}"


def base_lang(lang: str) -> str:
    return lang.split("-")[0].lower()


def line_limit(lang: str) -> int:
    return LINE_LIMITS.get(base_lang(lang), DEFAULT_LIMIT)


def width(line: str, lang: str) -> int:
    if base_lang(lang) in ("th", "hi"):
        # combining vowel/tone marks take no cell of their own
        return sum(1 for ch in line if unicodedata.category(ch) not in ("Mn", "Me", "Mc"))
    return len(line)


def lang_from_name(path: pathlib.Path) -> str:
    parts = path.name.split(".")
    return parts[-2] if len(parts) >= 3 else ""


def parse_keep(text: str) -> set[int]:
    out = set()
    for item in (text or "").split(","):
        item = item.strip()
        if not item:
            continue
        if not item.isdigit():
            raise ValueError(f"--keep-same expects block numbers like 95,144, got {item!r}")
        out.add(int(item))
    return out


def check(pivot: list, target: list, lang: str, source_lang: str = "", keep: set[int] | None = None
          ) -> tuple[list[str], list[str]]:
    """Return (errors, warnings)."""
    keep = keep or set()
    errors: list[str] = []
    warnings: list[str] = []
    limit = line_limit(lang)
    src_re = SOURCE_SCRIPT.get(base_lang(source_lang)) if base_lang(source_lang) != base_lang(lang) else None
    if len(pivot) != len(target):
        errors.append(f"block count differs: pivot {len(pivot)} vs translation {len(target)}")
    for n, (p, t) in enumerate(zip(pivot, target), 1):
        if p[:2] != t[:2]:
            errors.append(f"#{n} timecode differs: {p[0]} --> {p[1]} vs {t[0]} --> {t[1]}")
        if not t[2]:
            errors.append(f"#{n} empty block")
            continue
        body = " ".join(t[2]).strip()
        p_body = " ".join(p[2]).strip()
        if src_re and src_re.search(body):
            errors.append(f"#{n} source-language text left over: {body}")
        exempt = n in keep or any(m in p_body for m in MUSIC) or p_body.startswith("[")
        if not exempt and body == p_body and re.search(r"[A-Za-z]{3}", body):
            errors.append(f"#{n} identical to the pivot (untranslated?): {body}  "
                          f"-- if it should stay, pass --keep-same {n}")
        if len(t[2]) > 2:
            errors.append(f"#{n} {len(t[2])} lines (max 2)")
        for line in t[2]:
            w = width(line, lang)
            if w > limit:
                warnings.append(f"#{n} line length {w} > {limit}: {line}")
        for tok in sorted(set(PROPER.findall(p_body))):
            if tok not in body:
                warnings.append(f"#{n} proper noun missing? {tok}")
    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    C.setup_utf8_io()
    ap = argparse.ArgumentParser(description="Check a translated SRT against its pivot track (same blocks/timecodes).")
    ap.add_argument("pivot", help="reviewed pivot-language SRT (usually <video>.en.srt)")
    ap.add_argument("target", help="translated SRT (e.g. <video>.ja.srt)")
    ap.add_argument("--lang", default="", help="target language code (default: taken from the file name)")
    ap.add_argument("--source-lang", default=None,
                    help="original spoken language, to catch leftover source text (default: prefs source_language)")
    ap.add_argument("--keep-same", default="", metavar="N,N",
                    help="block numbers that correctly stay identical to the pivot (puns, brand phrases)")
    ap.add_argument("--max-warnings", type=int, default=40)
    a = ap.parse_args(argv)

    target_path = C.expand_path(a.target)
    lang = a.lang or lang_from_name(target_path)
    if not lang:
        raise SystemExit("error: cannot tell the language from the file name; pass --lang")
    source_lang = a.source_lang if a.source_lang is not None else (C.load_prefs().get("source_language") or "")
    try:
        keep = parse_keep(a.keep_same)
        pivot = parse_srt(C.read_text_file(C.expand_path(a.pivot)))
        target = parse_srt(C.read_text_file(target_path))
    except (ValueError, FileNotFoundError) as e:
        raise SystemExit(f"error: {e}") from None

    errors, warnings = check(pivot, target, lang, source_lang, keep)
    print(f"[{lang}] {target_path.name}: {len(target)} blocks | {len(errors)} errors | {len(warnings)} warnings")
    for e in errors:
        print(f"  x {e}")
    for w in warnings[:a.max_warnings]:
        print(f"  . {w}")
    if len(warnings) > a.max_warnings:
        print(f"  . ... {len(warnings) - a.max_warnings} more warnings")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
