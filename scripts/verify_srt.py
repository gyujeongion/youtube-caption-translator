"""Check that a translated SRT stays consistent with the source timecodes.

    python3 verify_srt.py <source.srt> <translated.srt>            # mode auto-detected
    python3 verify_srt.py <source.srt> <translated.srt> --strict   # force 1:1

Two modes:

- strict -- entries are 1:1. Indexes and timecodes must match the source exactly.
- merged -- the translation was merged into sentences and retimed (the
  recommended approach). Boundaries must sit on source boundaries, entries must
  be monotonic without overlap, and the source speech must be fully covered.

If the entry counts differ, merged mode is selected automatically.
Exit code: 0 = passed, 1 = problems found, 2 = bad arguments.
"""
from __future__ import annotations

import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402

TIME = re.compile(r"^(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})")


def to_ms(stamp: str) -> int:
    h, m, s = stamp.split(":")
    sec, ms = s.split(",")
    return ((int(h) * 60 + int(m)) * 60 + int(sec)) * 1000 + int(ms)


def parse(path: pathlib.Path) -> list[dict]:
    raw = C.read_text_file(path)  # BOM / CRLF tolerant; TextDecodeError (ValueError) if not UTF-8
    out = []
    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if len(lines) < 2 or not TIME.match(lines[1]):
            continue
        m = TIME.match(lines[1])
        out.append({
            "index": lines[0].strip(),
            "start": to_ms(m.group(1)),
            "end": to_ms(m.group(2)),
            "raw_start": m.group(1),
            "raw_end": m.group(2),
            "text": "\n".join(lines[2:]).strip(),
        })
    return out


def check_common(dst: list[dict], problems: list[str]) -> None:
    prev_end = -1
    for i, y in enumerate(dst, start=1):
        if y["end"] <= y["start"]:
            problems.append(f"#{i} end is before or equal to start")
        if y["start"] < prev_end:
            problems.append(f"#{i} overlaps the previous entry ({y['raw_start']})")
        prev_end = y["end"]
        if not y["text"]:
            problems.append(f"#{i} translated text is empty")
        for ln in y["text"].split("\n"):
            if len(ln) > 60:
                problems.append(f"#{i} a line is over 60 characters ({len(ln)}) -- needs a line break")
        if y["text"].count("\n") >= 2:
            problems.append(f"#{i} 3 or more lines -- covers the video on YouTube")
        dur = y["end"] - y["start"]
        if dur > 9000:
            problems.append(f"#{i} {dur/1000:.1f}s is too long -- consider splitting")


def strict(src, dst, problems):
    if len(src) != len(dst):
        problems.append(f"entry count mismatch: source {len(src)} vs translation {len(dst)}")
    for i, (x, y) in enumerate(zip(src, dst), start=1):
        if x["index"] != y["index"]:
            problems.append(f"#{i} index mismatch: {x['index']} vs {y['index']}")
        if x["start"] != y["start"] or x["end"] != y["end"]:
            problems.append(f"#{i} timecode mismatch: {x['raw_start']} vs {y['raw_start']}")


def merged(src, dst, problems):
    starts = {e["start"] for e in src}
    ends = {e["end"] for e in src}
    for i, y in enumerate(dst, start=1):
        if y["start"] not in starts:
            problems.append(f"#{i} start {y['raw_start']} is not on a source boundary")
        if y["end"] not in ends:
            problems.append(f"#{i} end {y['raw_end']} is not on a source boundary")
    for x in src:
        covered = any(y["start"] <= x["start"] and y["end"] >= x["end"] for y in dst)
        if not covered:
            problems.append(f"source entry {x['index']} ({x['raw_start']}) is not covered")


def main(a: str, b: str, force_strict: bool) -> int:
    C.setup_utf8_io()
    try:
        src, dst = parse(C.expand_path(a)), parse(C.expand_path(b))
    except (OSError, ValueError) as e:  # missing file, or an SRT saved in a legacy encoding
        C.eprint(f"error: {e}")
        return 2
    mode = "strict" if (force_strict or len(src) == len(dst)) else "merged"
    problems: list[str] = []

    check_common(dst, problems)
    (strict if mode == "strict" else merged)(src, dst, problems)

    if problems:
        print(f"✗ verification FAILED ({mode} mode) -- {len(problems)} problem(s)")
        for p in problems[:40]:
            print("  -", p)
        if len(problems) > 40:
            print(f"  ... and {len(problems) - 40} more")
        return 1

    print(f"✓ verification passed ({mode} mode) -- source {len(src)} -> translation {len(dst)} entries")
    return 0


if __name__ == "__main__":
    C.setup_utf8_io()
    args = [a for a in sys.argv[1:] if a != "--strict"]
    if any(a in ("-h", "--help") for a in args):
        print(__doc__)
        raise SystemExit(0)
    if len(args) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(args[0], args[1], "--strict" in sys.argv))
