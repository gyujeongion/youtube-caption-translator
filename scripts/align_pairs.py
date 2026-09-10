"""검수용 대조표를 만든다 — 번역 블록마다 그 구간의 원본 한국어를 붙인다.

    python3 align_pairs.py <원본.srt> <번역.srt> [-o 대조표.md]

번역이 문장 단위로 병합돼 있으면 원본 조각 여러 개가 한 블록에 대응한다.
그 대응을 눈으로 확인할 수 있게 표로 뽑아, 검수(뉘앙스·누락·왜곡)를 한다.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from verify_srt import parse  # noqa: E402


def build(src_path: str, dst_path: str) -> str:
    src, dst = parse(pathlib.Path(src_path)), parse(pathlib.Path(dst_path))
    lines = [
        f"# 번역 검수 대조표",
        "",
        f"- 원본: `{src_path}` ({len(src)} 엔트리)",
        f"- 번역: `{dst_path}` ({len(dst)} 엔트리)",
        "",
        "각 블록마다 (1) 의미 왜곡·누락 (2) 구어 자연스러움 (3) 고유명사·전문용어",
        "보존을 확인한다. 문제가 있으면 블록 번호로 지적한다.",
        "",
    ]
    for y in dst:
        covered = [x for x in src if x["start"] >= y["start"] and x["end"] <= y["end"]]
        if not covered:
            covered = [x for x in src
                       if x["start"] < y["end"] and x["end"] > y["start"]]
        ko = " / ".join(x["text"].replace("\n", " ") for x in covered)
        en = y["text"].replace("\n", " ")
        lines += [
            f"## {y['index']}  {y['raw_start']} → {y['raw_end']}",
            f"- KO: {ko}",
            f"- EN: {en}",
            "",
        ]
    return "\n".join(lines)


if __name__ == "__main__":
    args = sys.argv[1:]
    out = None
    if "-o" in args:
        i = args.index("-o")
        out = args[i + 1]
        args = args[:i] + args[i + 2:]
    if len(args) != 2:
        print(__doc__)
        raise SystemExit(2)
    text = build(args[0], args[1])
    if out:
        pathlib.Path(out).write_text(text, encoding="utf-8")
        print(f"대조표 저장: {out}")
    else:
        print(text)
