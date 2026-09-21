#!/usr/bin/env python3
"""Find unfinished-utterance blocks in an editor SRT (Korean sources only).

Auto-captions from CapCut/Premiere cut speech into pieces and drop sentence
endings. Reading only the leftover fragment makes it look like a finished
sentence, and the translation then invents meaning that was never said.

  source:      "형이 자주 플레이하는 거 중에" / "터진다 그냥"
  actually:    "형이 자주 플레이하는 거 중에 진짜 터지는 거 없어?"
  mistranslated: "It's one of the ones you play a lot -- it just tears the place up."

In Korean, whether a sentence has ended shows in its ending. A block that ends
in a connective ending or an adnominal form means more words followed. This
script lists those blocks so they can be checked before translating.

LIMITATION: the heuristics understand KOREAN sources only (they match Korean
verb endings). Run on an SRT with no Hangul, it prints
"Korean-source heuristics only; skipping" and exits 0.

Usage:
    python3 flag_incomplete.py <source.srt> [--merged <translated.srt>] [--weak]

    --merged   judge on the merged translation blocks (source fragments grouped by
               the translated SRT's boundaries) instead of the raw source blocks
    --weak     also print weak signals (default: strong signals only)
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _common as C  # noqa: E402
from verify_srt import parse  # noqa: E402

# Ending in one of these signals that more words followed.
# Strong = an ending that cannot end a sentence by itself.
DANGLING_STRONG = [
    "중에", "중에서", "가운데", "다가", "면서", "려고", "러", "느라",
    "지만", "는데도", "든지", "거나", "든가", "커녕", "밖에",
    "에서", "부터", "까지", "처럼", "보다", "말고", "대신",
    "의", "와", "과", "랑", "이랑", "하고",
]
# Adnominal forms (a noun must follow)
ADNOMINAL = re.compile(r"(하는|되는|있는|없는|같은|[가-힣]+[은는을]) *$")
# Connective endings -- can also end a sentence depending on context, so a weak signal
DANGLING_WEAK = ["고", "서", "니까", "는데", "인데", "라서", "며", "자"]

# Endings and punctuation that confirm a finished sentence
TERMINAL = re.compile(
    r"([.?!…]|다|요|까|죠|네|군|자|래|야|음|함|임|잖아|거든|는걸|더라|구나|세요|십시오)\s*$"
)


def classify(text: str):
    """Decide whether one block of Korean text is complete."""
    t = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
    if not t:
        return None
    t = re.sub(r"[ㅎㅋㅠㅜ~]+$", "", t).strip()
    if not t:
        return None

    last = t.split()[-1]

    for suf in DANGLING_STRONG:
        if last.endswith(suf):
            return ("strong", f"ends in '{suf}' -- more words followed")
    if ADNOMINAL.search(t):
        return ("strong", "ends in an adnominal form -- the following noun was cut off")
    if TERMINAL.search(t):
        return None
    for suf in DANGLING_WEAK:
        if last.endswith(suf):
            return ("weak", f"connective ending '{suf}' -- may be a real ending or cut off")
    return ("weak", "no sentence-final ending")


# Particles / dependent nouns that must be followed by a noun phrase
NOUN_REQUIRED = [
    "중에", "중에서", "가운데", "처럼", "보다", "말고", "대신",
    "의", "와", "과", "랑", "이랑", "하고", "밖에",
]


def check_seams(ko: str):
    """Check the seams between fragments inside a merged group for cut-off speech.

    "형이 자주 플레이하는 거 중에" + "터진다 그냥"
    -> a noun must follow '중에' but a predicate came instead, meaning the words in
       between ("진짜 터지는 거 없어?") were cut out during editing.
    """
    frags = [f.strip() for f in ko.split(" / ") if f.strip()]
    for a, b in zip(frags, frags[1:]):
        last = a.split()[-1] if a.split() else ""
        if not any(last.endswith(suf) for suf in NOUN_REQUIRED):
            continue
        first = b.split()[0] if b.split() else ""
        if TERMINAL.search(first):
            return (f"a noun must follow '{last}' but '{first}' came instead "
                    "-- the words in between were cut off")
    return None


def is_korean(text: str) -> bool:
    """True if a meaningful share of the letters are Hangul syllables."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    hangul = sum(1 for c in letters if "\uac00" <= c <= "\ud7a3")
    return hangul / len(letters) >= 0.2


def main():
    C.setup_utf8_io()
    ap = argparse.ArgumentParser(
        description="Flag unfinished-utterance blocks in a Korean editor SRT. "
                    "Understands Korean sources only (Korean verb-ending heuristics).")
    ap.add_argument("source", help="source SRT exported from the editor (Korean)")
    ap.add_argument("--merged", help="merged translated SRT -- if given, judge on the merged blocks")
    ap.add_argument("--weak", action="store_true", help="also print weak signals")
    args = ap.parse_args()

    try:
        src = parse(C.expand_path(args.source))
    except (OSError, ValueError) as e:  # missing file, or an SRT saved in a legacy encoding
        C.eprint(f"error: {e}")
        raise SystemExit(2) from None
    if not is_korean(" ".join(s["text"] for s in src)):
        print("Korean-source heuristics only; skipping")
        return

    if args.merged:
        # Group source fragments by the translated block boundaries and judge the merged text.
        try:
            tgt = parse(C.expand_path(args.merged))
        except (OSError, ValueError) as e:
            C.eprint(f"error: {e}")
            raise SystemExit(2) from None
        groups = []
        for i, blk in enumerate(tgt, 1):
            inside = [s for s in src
                      if s["start"] >= blk["start"] - 1 and s["end"] <= blk["end"] + 1]
            ko = " / ".join(s["text"].replace("\n", " ") for s in inside)
            groups.append((i, blk["start"], ko, blk["text"].replace("\n", " ")))
    else:
        groups = [(i, s["start"], s["text"], "") for i, s in enumerate(src, 1)]

    hits = []
    for idx, start, ko, en in groups:
        verdict = classify(ko)
        if verdict and (args.weak or verdict[0] == "strong"):
            hits.append((idx, start, ko, en, verdict))
            continue
        # Also check seams between fragments in a merged group: if a predicate follows
        # "...거 중에" instead of a noun, the words in between were cut out of the edit.
        gap = check_seams(ko)
        if gap:
            hits.append((idx, start, ko, en, ("strong", gap)))

    if not hits:
        print("✓ No blocks look unfinished")
        return

    print(f"⚠ {len(hits)} block(s) need checking -- confirm the actual speech before translating\n")
    for idx, start, ko, en, (level, why) in hits:
        sec = start / 1000
        ts = f"{int(sec // 60):02d}:{sec % 60:06.3f}"
        print(f"[{level}] #{idx}  {ts}")
        print(f"   KO: {ko}")
        if en:
            print(f"   EN: {en}")
        print(f"   → {why}\n")


if __name__ == "__main__":
    main()
