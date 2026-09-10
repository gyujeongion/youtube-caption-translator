#!/usr/bin/env python3
"""편집기 SRT의 미완결 발화 블록을 찾아낸다.

캡컷·프리미어 자동자막은 말을 조각내면서 문장 끝을 흘린다. 남은 조각만
읽으면 완결된 문장처럼 보여서, 번역할 때 없는 뜻을 지어내게 된다.

  원본:  "형이 자주 플레이하는 거 중에" / "터진다 그냥"
  실제:  "형이 자주 플레이하는 거 중에 진짜 터지는 거 없어?"
  오역:  "It's one of the ones you play a lot — it just tears the place up."

한국어는 문장이 끝났는지가 어미에 드러난다. 연결어미·관형형으로 끝나면
뒤에 말이 더 있었다는 뜻이다. 그런 블록을 뽑아 번역 전에 확인받는다.

사용:
    python3 flag_incomplete.py <원본.srt> [--srt-merged <번역.srt>]
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from verify_srt import parse  # noqa: E402

# 이걸로 끝나면 뒤에 말이 더 있었다는 신호.
# 강함 = 그 자체로 문장이 될 수 없는 어미.
DANGLING_STRONG = [
    "중에", "중에서", "가운데", "다가", "면서", "려고", "러", "느라",
    "지만", "는데도", "든지", "거나", "든가", "커녕", "밖에",
    "에서", "부터", "까지", "처럼", "보다", "말고", "대신",
    "의", "와", "과", "랑", "이랑", "하고",
]
# 관형형(뒤에 명사가 와야 함)
ADNOMINAL = re.compile(r"(하는|되는|있는|없는|같은|[가-힣]+[은는을]) *$")
# 연결어미 — 문맥에 따라 종결도 되므로 약한 신호
DANGLING_WEAK = ["고", "서", "니까", "는데", "인데", "라서", "며", "자"]

# 종결로 확정되는 어미·부호
TERMINAL = re.compile(
    r"([.?!…]|다|요|까|죠|네|군|자|래|야|음|함|임|잖아|거든|는걸|더라|구나|세요|십시오)\s*$"
)


def classify(text: str):
    """한 블록의 한국어 텍스트가 완결됐는지 판정."""
    t = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
    if not t:
        return None
    t = re.sub(r"[ㅎㅋㅠㅜ~]+$", "", t).strip()
    if not t:
        return None

    last = t.split()[-1]

    for suf in DANGLING_STRONG:
        if last.endswith(suf):
            return ("강함", f"'{suf}'로 끝남 — 뒤에 말이 더 있었다")
    if ADNOMINAL.search(t):
        return ("강함", "관형형으로 끝남 — 뒤에 명사가 잘렸다")
    if TERMINAL.search(t):
        return None
    for suf in DANGLING_WEAK:
        if last.endswith(suf):
            return ("약함", f"연결어미 '{suf}' — 종결일 수도, 잘렸을 수도")
    return ("약함", "종결어미 없음")


# 뒤에 반드시 명사(구)가 와야 하는 조사·의존명사
NOUN_REQUIRED = [
    "중에", "중에서", "가운데", "처럼", "보다", "말고", "대신",
    "의", "와", "과", "랑", "이랑", "하고", "밖에",
]


def check_seams(ko: str):
    """병합 그룹 안 조각 경계에서 말이 잘렸는지 본다.

    "형이 자주 플레이하는 거 중에" + "터진다 그냥"
    → '중에' 뒤에는 명사가 와야 하는데 서술어가 왔다. 사이에 있던 말
      ("진짜 터지는 거 없어?")이 편집에서 사라졌다는 뜻이다.
    """
    frags = [f.strip() for f in ko.split(" / ") if f.strip()]
    for a, b in zip(frags, frags[1:]):
        last = a.split()[-1] if a.split() else ""
        if not any(last.endswith(suf) for suf in NOUN_REQUIRED):
            continue
        first = b.split()[0] if b.split() else ""
        if TERMINAL.search(first):
            return (f"'{last}' 뒤에 명사가 와야 하는데 '{first}'가 왔다 "
                    "— 사이에 있던 말이 잘렸다")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="편집기에서 뽑은 원본 SRT (한국어)")
    ap.add_argument("--merged", help="병합 번역 SRT — 있으면 병합 후 기준으로 판정")
    ap.add_argument("--weak", action="store_true", help="약한 신호도 전부 출력")
    args = ap.parse_args()

    src = parse(Path(args.source))

    if args.merged:
        # 번역 블록 경계로 원본을 묶어, 병합 후 텍스트로 판정한다.
        tgt = parse(Path(args.merged))
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
        if verdict and (args.weak or verdict[0] == "강함"):
            hits.append((idx, start, ko, en, verdict))
            continue
        # 병합 그룹 안의 조각 경계도 본다. "…거 중에" 다음에 명사가 아니라
        # 서술어가 바로 오면, 그 사이에 있던 말이 편집에서 잘려나간 것이다.
        gap = check_seams(ko)
        if gap:
            hits.append((idx, start, ko, en, ("강함", gap)))

    if not hits:
        print("✓ 미완결로 의심되는 블록 없음")
        return

    print(f"⚠ 확인 필요 {len(hits)}개 — 번역 전에 실제 발화를 확인한다\n")
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
