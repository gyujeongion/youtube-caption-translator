"""Build a review table: attach the source text of each interval to every translated block.

    python3 align_pairs.py <source.srt> <translated.srt> [-o review_table.md]

When the translation was merged into sentences, several source fragments map to
one translated block. This prints that mapping as a table so you can check it by
eye (nuance, omissions, distortions) before uploading.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _common as C  # noqa: E402
from verify_srt import parse  # noqa: E402


def build(src_path: str, dst_path: str) -> str:
    src, dst = parse(C.expand_path(src_path)), parse(C.expand_path(dst_path))
    lines = [
        "# Translation review table",
        "",
        f"- Source: `{src_path}` ({len(src)} entries)",
        f"- Translation: `{dst_path}` ({len(dst)} entries)",
        "",
        "For each block check (1) meaning distortion or omission (2) natural spoken style",
        "(3) preservation of proper nouns and terms. Report problems by block number.",
        "",
    ]
    for y in dst:
        covered = [x for x in src if x["start"] >= y["start"] and x["end"] <= y["end"]]
        if not covered:
            covered = [x for x in src
                       if x["start"] < y["end"] and x["end"] > y["start"]]
        source_text = " / ".join(x["text"].replace("\n", " ") for x in covered)
        target_text = y["text"].replace("\n", " ")
        lines += [
            f"## {y['index']}  {y['raw_start']} -> {y['raw_end']}",
            f"- SRC: {source_text}",
            f"- TGT: {target_text}",
            "",
        ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    C.setup_utf8_io()
    args = list(sys.argv[1:] if argv is None else argv)
    if any(a in ("-h", "--help") for a in args):
        print(__doc__)
        return 0
    out = None
    if "-o" in args:
        i = args.index("-o")
        if i + 1 >= len(args):
            print(__doc__)
            return 2
        out = args[i + 1]
        args = args[:i] + args[i + 2:]
    if len(args) != 2:
        print(__doc__)
        return 2
    try:
        text = build(args[0], args[1])
    except (OSError, ValueError) as e:  # missing file, or an SRT saved in a legacy encoding
        C.eprint(f"error: {e}")
        return 2
    if out:
        out_path = C.expand_path(out)
        C.write_text_lf(out_path, text + "\n")  # UTF-8, LF on every OS (Path.write_text would write CRLF on Windows)
        print(f"Review table saved: {out_path}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
