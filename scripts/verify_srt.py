"""번역 SRT가 원본 타임코드와 어긋나지 않는지 검증한다.

    python3 verify_srt.py <원본.srt> <번역.srt>          # 모드 자동 판별
    python3 verify_srt.py <원본.srt> <번역.srt> --strict # 1:1 강제

두 가지 모드가 있다.

- strict — 엔트리가 1:1. 인덱스·타임코드가 원본과 완전히 같아야 한다.
- merged — 문장 단위로 병합해 타임코드를 재배치한 경우(권장 방식).
  경계가 원본 경계 위에 놓여 있고, 겹침 없이 단조 증가하며,
  원본 발화 구간을 빠짐없이 덮는지 본다.

엔트리 수가 다르면 자동으로 merged 모드로 판정한다.
"""
from __future__ import annotations

import pathlib
import re
import sys

TIME = re.compile(r"^(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})")


def to_ms(stamp: str) -> int:
    h, m, s = stamp.split(":")
    sec, ms = s.split(",")
    return ((int(h) * 60 + int(m)) * 60 + int(sec)) * 1000 + int(ms)


def parse(path: pathlib.Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
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
            problems.append(f"#{i} 끝이 시작보다 빠르거나 같다")
        if y["start"] < prev_end:
            problems.append(f"#{i} 앞 엔트리와 겹친다 ({y['raw_start']})")
        prev_end = y["end"]
        if not y["text"]:
            problems.append(f"#{i} 번역 텍스트가 비었다")
        for ln in y["text"].split("\n"):
            if len(ln) > 60:
                problems.append(f"#{i} 한 줄이 60자 초과 ({len(ln)}자) — 줄바꿈 필요")
        if y["text"].count("\n") >= 2:
            problems.append(f"#{i} 3줄 이상 — 유튜브에서 화면을 가린다")
        dur = y["end"] - y["start"]
        if dur > 9000:
            problems.append(f"#{i} {dur/1000:.1f}초로 너무 길다 — 분할 권장")


def strict(src, dst, problems):
    if len(src) != len(dst):
        problems.append(f"엔트리 수 불일치: 원본 {len(src)} vs 번역 {len(dst)}")
    for i, (x, y) in enumerate(zip(src, dst), start=1):
        if x["index"] != y["index"]:
            problems.append(f"#{i} 인덱스 불일치: {x['index']} vs {y['index']}")
        if x["start"] != y["start"] or x["end"] != y["end"]:
            problems.append(f"#{i} 타임코드 불일치: {x['raw_start']} vs {y['raw_start']}")


def merged(src, dst, problems):
    starts = {e["start"] for e in src}
    ends = {e["end"] for e in src}
    for i, y in enumerate(dst, start=1):
        if y["start"] not in starts:
            problems.append(f"#{i} 시작 {y['raw_start']}이 원본 경계에 없다")
        if y["end"] not in ends:
            problems.append(f"#{i} 끝 {y['raw_end']}이 원본 경계에 없다")
    for x in src:
        covered = any(y["start"] <= x["start"] and y["end"] >= x["end"] for y in dst)
        if not covered:
            problems.append(f"원본 {x['index']} ({x['raw_start']}) 구간이 안 덮였다")


def main(a: str, b: str, force_strict: bool) -> int:
    src, dst = parse(pathlib.Path(a)), parse(pathlib.Path(b))
    mode = "strict" if (force_strict or len(src) == len(dst)) else "merged"
    problems: list[str] = []

    check_common(dst, problems)
    (strict if mode == "strict" else merged)(src, dst, problems)

    if problems:
        print(f"✗ 검증 실패 ({mode} 모드) — {len(problems)}건")
        for p in problems[:40]:
            print("  -", p)
        if len(problems) > 40:
            print(f"  ... 외 {len(problems) - 40}건")
        return 1

    print(f"✓ 검증 통과 ({mode} 모드) — 원본 {len(src)} → 번역 {len(dst)} 엔트리")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--strict"]
    if len(args) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(args[0], args[1], "--strict" in sys.argv))
