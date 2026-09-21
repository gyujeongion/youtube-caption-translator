#!/usr/bin/env python3
"""Build a "context pack" for the ambiguous subtitle blocks of a video, and check the answers.

Languages that drop the grammatical subject (Korean, Japanese) are mistranslated when the
translator does not know WHO is speaking to WHOM and what a word like "here" or "this"
points at. This tool picks the blocks where that is a risk, cuts one contact-sheet JPEG
per block from the video, writes an index.md the agent reads, and a speaker_map.json the
agent fills in. The translation step then consumes that map.

  # 1) build (auto-select the risky blocks, 3 stills per block, left/right person crops)
  python3 context_pack.py build --video V.mp4 --srt source.srt --out pack --auto --crops left,right

  # explicit blocks (ids are 1-based positions in the SRT): 12, 45 and 50 to 53
  python3 context_pack.py build --video V.mp4 --srt source.srt --out pack --blocks 12,45,50-53

  # 2) after the agent filled pack/speaker_map.json: list what is still unresolved
  python3 context_pack.py check --map pack/speaker_map.json --srt source.srt

`check` exits 0 when every block of the pack is resolved, 1 when questions remain
(it prints them as a numbered list to put to the user). Details: references/context-pack.md.
Standard library only; needs ffmpeg and ffprobe for `build`.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _common as C  # noqa: E402

try:  # helpers from the sibling scripts (kept optional so this file still imports without them)
    from frames_at import probe as _probe_video, crop_filter as _crop_filter  # noqa: E402
except Exception:  # pragma: no cover - only if frames_at is broken
    _probe_video = None
    _crop_filter = None
try:
    from flag_incomplete import classify as _classify, check_seams as _check_seams  # noqa: E402
except Exception:  # pragma: no cover
    _classify = None
    _check_seams = None

# --------------------------------------------------------------------------
# Data-driven word lists. Extend a language by editing this table only.
# --------------------------------------------------------------------------

LANG_RULES: dict[str, dict] = {
    "ko": {
        # demonstratives; matched at the start of an eojeol (space-delimited word)
        "deictic": ["여기", "저기", "거기", "이거", "저거", "그거", "이쪽", "저쪽",
                    "이게", "저게", "그게", "이건", "저건", "그건", "이것", "저것", "그것"],
        "deictic_mode": "word_start",
        "question": r"([?？]|까요|니까|나요|냐|니|죠)\s*$",
        "imperative": r"(줘|줘봐|해봐|봐|세요|십시오|해라|하자|가자|보자)\s*$",
    },
    "ja": {
        "deictic": ["ここ", "そこ", "あそこ", "これ", "それ", "あれ", "こっち", "そっち", "あっち"],
        "deictic_mode": "substring",   # Japanese has no spaces; expect some false positives
        "question": r"([?？]|か|かな|かい|でしょ|だろう)[。]?\s*$",
        "imperative": r"(ください|くれ|なさい|てよ|して|見て|来て|行こう|しよう)[。]?\s*$",
    },
    "en": {
        "deictic": ["this one", "that one", "these ones", "those ones",
                    "over here", "over there", "right here", "right there"],
        "deictic_mode": "phrase",
        "question": r"\?\s*$",
        "imperative": r"^\s*(let's|please|come|look|try|give|take|put|go|wait|check|watch|hold|use|show|tell|stop|don't)\b",
    },
}

SHORT_MAX_CJK_CHARS = 6      # ko/ja: <= this many letters counts as a very short utterance
SHORT_MAX_WORDS = 4          # other languages: <= this many words
GAP_SECONDS = 1.0            # silence before a block that makes a short one a subject-drop hotspot
AUTO_MIN_SCORE = 2           # blocks scoring below this are not auto-selected
SCORE_DEICTIC = 3
SCORE_TRUNCATED = 3
SCORE_SPEAKER_CHANGE = 2
SCORE_SHORT_IN_CONTEXT = 2   # short AND (after a gap OR at a speaker change)
SCORE_SHORT_ALONE = 1
SCORE_QUESTION_OR_IMPERATIVE = 1

CROP_ORDER = ["left", "center", "right"]   # sheet columns are always laid out in this order
MAX_FRAMES = 6
CONFIDENCES = ("high", "medium", "low")

Q_TEXT = [
    "Who is speaking in this block?",
    "Who is it said to (the addressee)?",
    "What does each deictic word (here, this, that ...) point at? If it is not visible, say so.",
    "Is the action real (happening now) or hypothetical / a plan / a recording being played?",
    "Is this a cut from another point in time (different clothes, place, lighting)?",
]


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------

def parse_block_spec(spec: str, total: int) -> list[int]:
    """'12,45,50-53' -> [12, 45, 50, 51, 52, 53]. Ids are 1-based positions. Raises ValueError."""
    ids: set[int] = set()
    for part in (p.strip() for p in spec.split(",")):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", part)
        if not m:
            raise ValueError(f"cannot parse block spec {part!r} (use e.g. 12,45,50-53)")
        lo = int(m.group(1))
        hi = int(m.group(2)) if m.group(2) else lo
        if lo > hi:
            raise ValueError(f"descending range {part!r}")
        ids.update(range(lo, hi + 1))
    bad = sorted(i for i in ids if i < 1 or i > total)
    if bad:
        raise ValueError(f"block id(s) out of range 1..{total}: {bad[:10]}")
    if not ids:
        raise ValueError("no block ids given")
    return sorted(ids)


def load_sidecar(path: str | pathlib.Path | None) -> list[dict]:
    """Read the diarization sidecar [{start,end,speaker}]. Warn (stderr) and skip bad entries; never raise."""
    if not path:
        return []
    p = pathlib.Path(path)
    try:
        data = C.read_json(p)
    except (OSError, ValueError) as exc:   # TextDecodeError is a ValueError
        C.eprint(f"warning: cannot read speaker sidecar {p}: {exc}; continuing without speakers")
        return []
    if not isinstance(data, list):
        C.eprint(f"warning: speaker sidecar {p} is not a JSON list; continuing without speakers")
        return []
    out, skipped = [], 0
    for item in data:
        try:
            s, e = float(item["start"]), float(item["end"])
            spk = str(item["speaker"]).strip()
            if not spk or e <= s:
                raise ValueError
            out.append({"start": s, "end": e, "speaker": spk})
        except (KeyError, TypeError, ValueError):
            skipped += 1
    if skipped:
        C.eprint(f"warning: skipped {skipped} malformed entr{'y' if skipped == 1 else 'ies'} in {p}")
    return out


def speaker_for(block: dict, segments: list[dict], min_overlap: float = 0.05) -> str:
    """Speaker label with the largest time overlap with the block ('' if none)."""
    best, best_ov = "", 0.0
    for seg in segments:
        ov = min(block["end"], seg["end"]) - max(block["start"], seg["start"])
        if ov > best_ov + 1e-9:
            best, best_ov = seg["speaker"], ov
    return best if best_ov >= min_overlap else ""


def detect_lang(blocks: list[dict]) -> str:
    text = " ".join(b["text"] for b in blocks)
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "en"
    hangul = sum("가" <= c <= "힣" for c in letters)
    kana = sum("぀" <= c <= "ヿ" for c in letters)
    if hangul / len(letters) >= 0.2:
        return "ko"
    if kana / len(letters) >= 0.1:
        return "ja"
    return "en"


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\n", " ")).strip()


def find_deictic(text: str, lang: str) -> list[str]:
    rules = LANG_RULES.get(lang)
    if not rules:
        return []
    t = _flat(text)
    found = []
    for w in rules["deictic"]:
        mode = rules["deictic_mode"]
        if mode == "word_start":
            hit = re.search(r"(?<![가-힣])" + re.escape(w), t)
        elif mode == "phrase":
            hit = re.search(r"(?<![A-Za-z])" + re.escape(w) + r"(?![A-Za-z])", t, re.IGNORECASE)
        else:
            hit = w in t
        if hit:
            found.append(w)
    return found


def _short_len(text: str, lang: str) -> tuple[int, str]:
    if lang in ("ko", "ja"):
        return len(re.sub(r"[\s\W_]+", "", _flat(text), flags=re.UNICODE)), "chars"
    return len(re.findall(r"[^\W_]+(?:'[^\W_]+)?", _flat(text), flags=re.UNICODE)), "words"


def score_blocks(blocks: list[dict], lang: str | None = None,
                 speakers: list[str] | None = None) -> list[dict]:
    """Score every block. Returns [{id, score, reasons, deictic}] in block order (ids are 1-based positions)."""
    lang = lang or detect_lang(blocks)
    rules = LANG_RULES.get(lang, LANG_RULES["en"])
    speakers = speakers or [""] * len(blocks)
    out = []
    for i, b in enumerate(blocks):
        bid = i + 1
        text = _flat(b["text"])
        score, reasons = 0, []

        dw = find_deictic(text, lang)
        if dw:
            score += SCORE_DEICTIC
            reasons.append("deictic word(s): " + ", ".join(dw))

        prev = blocks[i - 1] if i else None
        gap = (b["start"] - prev["end"]) if prev else None
        changed = bool(i and speakers[i] and speakers[i - 1] and speakers[i] != speakers[i - 1])
        if changed:
            score += SCORE_SPEAKER_CHANGE
            reasons.append(f"speaker change per sidecar ({speakers[i - 1]} -> {speakers[i]})")

        n, unit = _short_len(text, lang)
        limit = SHORT_MAX_CJK_CHARS if unit == "chars" else SHORT_MAX_WORDS
        if 0 < n <= limit:
            after_gap = gap is not None and gap >= GAP_SECONDS
            if after_gap or changed:
                score += SCORE_SHORT_IN_CONTEXT
                why = f"after a {gap:.1f}s gap" if after_gap else "at a speaker change"
                reasons.append(f"very short utterance ({n} {unit}) {why}")
            else:
                score += SCORE_SHORT_ALONE
                reasons.append(f"very short utterance ({n} {unit})")

        if lang == "ko" and _classify is not None:
            verdict = _classify(b["text"])
            seam = None
            if _check_seams is not None and i + 1 < len(blocks):
                seam = _check_seams(_flat(b["text"]) + " / " + _flat(blocks[i + 1]["text"]))
            if verdict and verdict[0] == "strong":
                score += SCORE_TRUNCATED
                reasons.append("truncated utterance: " + verdict[1])
            elif seam:
                score += SCORE_TRUNCATED
                reasons.append("cut-off seam with the next block: " + seam)

        if re.search(rules["question"], text, re.IGNORECASE) or re.search(rules["imperative"], text, re.IGNORECASE):
            score += SCORE_QUESTION_OR_IMPERATIVE
            reasons.append("question or imperative ending (addressee may be ambiguous)")

        out.append({"id": bid, "score": score, "reasons": reasons, "deictic": dw})
    return out


def select_blocks(scored: list[dict], explicit: list[int] | None, auto: bool, max_blocks: int) -> list[dict]:
    """Chosen entries in time order. Explicit ids are always kept; auto fills up to max_blocks in total."""
    by_id = {s["id"]: dict(s) for s in scored}
    chosen: dict[int, dict] = {}
    for bid in explicit or []:
        e = by_id[bid]
        e["reasons"] = ["requested explicitly"] + e["reasons"]
        chosen[bid] = e
    if auto:
        room = max(0, max_blocks - len(chosen))
        cands = [s for s in scored if s["id"] not in chosen and s["score"] >= AUTO_MIN_SCORE]
        cands.sort(key=lambda s: (-s["score"], s["id"]))
        for s in cands[:room]:
            chosen[s["id"]] = dict(s)
    return [chosen[k] for k in sorted(chosen)]


# --------------------------------------------------------------------------
# Contact sheets (ffmpeg filters only: crop / scale / pad / hstack / vstack)
# --------------------------------------------------------------------------

def frame_times(start: float, end: float, n: int, duration: float | None) -> list[float]:
    """n stills spread from 15% to 85% of the block (n=3 -> 15%, 50%, 85%), clamped inside the video."""
    n = max(1, min(MAX_FRAMES, n))
    fr = [0.5] if n == 1 else [0.15 + 0.70 * k / (n - 1) for k in range(n)]
    out = []
    for f in fr:
        t = start + (end - start) * f
        if duration is not None:
            t = min(t, max(duration - 0.1, 0.0))
        out.append(round(max(t, 0.0), 3))
    return out


def _even(x: float) -> int:
    v = int(x)
    return max(2, v - (v % 2))


def sorted_crops(crops: list[str]) -> list[str]:
    return [c for c in CROP_ORDER if c in crops]


def parse_crops(spec: str) -> list[str]:
    spec = (spec or "none").strip().lower()
    if spec in ("none", "full", ""):
        return []
    names = [c.strip() for c in spec.split(",") if c.strip()]
    bad = [c for c in names if c not in CROP_ORDER]
    if bad:
        raise ValueError(f"unknown crop name(s) {bad}; use none or a list of {CROP_ORDER}")
    return sorted_crops(names)


def _crop_box(name: str, vw: int, vh: int) -> tuple[int, int, int, int]:
    """(w, h, x, y) in pixels, taken from frames_at.crop_filter so the boxes stay consistent."""
    if _crop_filter is None:
        raise RuntimeError("frames_at.crop_filter is unavailable")
    m = re.fullmatch(r"crop=(\d+):(\d+):(\d+):(\d+)", _crop_filter(name, vw, vh))
    if not m:
        raise RuntimeError("unexpected crop filter format from frames_at.crop_filter")
    return tuple(int(g) for g in m.groups())  # type: ignore[return-value]


def layout(vw: int, vh: int, crops: list[str], rows: int, width: int) -> dict:
    """Deterministic sheet geometry: rows = timestamps, columns = crops left-to-right."""
    cols = max(1, len(crops))
    cell_w = _even(width // cols)
    if crops:
        ratio = max(_crop_box(c, vw, vh)[1] / _crop_box(c, vw, vh)[0] for c in crops)
    else:
        ratio = vh / vw
    cell_h = _even(cell_w * ratio)
    return {"cols": cols, "rows": rows, "cell_w": cell_w, "cell_h": cell_h,
            "width": cell_w * cols, "height": cell_h * rows}


def sheet_filter(vw: int, vh: int, crops: list[str], rows: int, lay: dict) -> str:
    cw, ch, cols = lay["cell_w"], lay["cell_h"], lay["cols"]
    fit = f"scale={cw}:{ch}:force_original_aspect_ratio=decrease,pad={cw}:{ch}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
    parts: list[str] = []
    for r in range(rows):
        if not crops:
            parts.append(f"[{r}:v]{fit}[r{r}]")
            continue
        if cols == 1:
            w, h, x, y = _crop_box(crops[0], vw, vh)
            parts.append(f"[{r}:v]crop={w}:{h}:{x}:{y},{fit}[r{r}]")
            continue
        parts.append(f"[{r}:v]split={cols}" + "".join(f"[s{r}_{c}]" for c in range(cols)))
        for c, name in enumerate(crops):
            w, h, x, y = _crop_box(name, vw, vh)
            parts.append(f"[s{r}_{c}]crop={w}:{h}:{x}:{y},{fit}[c{r}_{c}]")
        parts.append("".join(f"[c{r}_{c}]" for c in range(cols)) + f"hstack=inputs={cols}[r{r}]")
    tail = "format=yuvj420p[out]"
    if rows == 1:
        parts.append(f"[r0]{tail}")
    else:
        parts.append("".join(f"[r{r}]" for r in range(rows)) + f"vstack=inputs={rows},{tail}")
    return ";".join(parts)


def make_sheet(video: str, times: list[float], crops: list[str], lay: dict,
               vw: int, vh: int, out_path: pathlib.Path) -> None:
    cmd = [shutil.which("ffmpeg") or "ffmpeg", "-nostdin", "-v", "error"]
    for t in times:
        cmd += ["-ss", f"{t:.3f}", "-i", C.media_arg(video)]
    cmd += ["-filter_complex", sheet_filter(vw, vh, crops, len(times), lay),
            "-map", "[out]", "-frames:v", "1", "-q:v", "3", "-y", C.media_arg(out_path)]
    subprocess.run(cmd, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
                   stdin=subprocess.DEVNULL)


# --------------------------------------------------------------------------
# index.md, template, manifest
# --------------------------------------------------------------------------

def _tc(sec: float) -> str:
    return C.format_ts(sec)


def render_index(blocks: list[dict], entries: list[dict], speakers: list[str], sheets: dict[int, dict],
                 lay: dict | None, crops: list[str], context_lines: int, srt_name: str, lang: str) -> str:
    L: list[str] = []
    L += ["# Context pack", "",
          f"- Source SRT: `{srt_name}` ({len(blocks)} blocks, language guess: {lang})",
          f"- Blocks in this pack: {len(entries)} -> " + ", ".join(str(e['id']) for e in entries), ""]
    L += ["## How to read the sheets", "",
          "Each block has ONE contact-sheet image. Layout is fixed:", "",
          "- **Rows** = still frames in time order, top row earliest (the timecode of every row is listed under each block).",
          ("- **Columns** = person crops from left to right: " + ", ".join(crops) + ".") if crops else
          "- **Columns** = one column (the full frame).",
          "- No text is drawn on the image; use the row timecodes below.", ""]
    if lay:
        L += [f"- Each cell is {lay['cell_w']}x{lay['cell_h']} px; the whole sheet is {lay['width']}x{lay['height']} px.", ""]
    L += ["## What to do", "",
          "1. Open each sheet and answer the questions under its block.",
          "2. Write the answers into `speaker_map.json` (same folder). Fill `people`, `cuts` and one entry per block id.",
          "3. If the frames cannot settle it (off-screen speaker, mouth not visible), set `confidence` to `low` "
          "and say why in `note`; do not guess. Those blocks go to the user as questions.",
          "4. Run `context_pack.py check --map speaker_map.json --srt <source.srt>` before verification.", ""]
    for e in entries:
        i = e["id"] - 1
        b = blocks[i]
        L += [f"## Block {e['id']}  {_tc(b['start'])} --> {_tc(b['end'])}", ""]
        if str(b.get("index", "")).strip() and str(b["index"]).strip() != str(e["id"]):
            L.append(f"- SRT index in file: {b['index']}")
        L.append(f"- Text: {_flat(b['text'])}")
        if speakers[i]:
            L.append(f"- Speaker label from sidecar: {speakers[i]}")
        sh = sheets.get(e["id"])
        if sh and sh.get("path"):
            L.append(f"- Image: `{sh['path']}`")
            for r, t in enumerate(sh["times"], 1):
                L.append(f"  - row {r}: {_tc(t)}")
        else:
            L.append("- Image: (not created; see the warning printed by build). Answer from the text, or ask the user.")
        L.append(f"- Selected because (score {e['score']}): " + "; ".join(e["reasons"]) if e["reasons"]
                 else "- Selected because: no specific signal")
        L += ["", "Context:", ""]
        lo, hi = max(0, i - context_lines), min(len(blocks), i + context_lines + 1)
        for j in range(lo, hi):
            mark = ">>" if j == i else "  "
            lab = f" [{speakers[j]}]" if speakers[j] else ""
            L.append(f"    {mark} #{j + 1} {_tc(blocks[j]['start'])} --> {_tc(blocks[j]['end'])}{lab}  {_flat(blocks[j]['text'])}")
        L += ["", "Answer in `speaker_map.json` -> `blocks` -> `" + str(e["id"]) + "`:", ""]
        for q in Q_TEXT:
            extra = ""
            if "deictic" in q and e["deictic"]:
                extra = " (words here: " + ", ".join(e["deictic"]) + ")"
            L.append(f"- {q}{extra}")
        L.append("")
    return "\n".join(L) + "\n"


def make_template(entries: list[dict]) -> dict:
    return {
        "people": {"A": {"description": "", "register": "", "evidence": []}},
        "cuts": [],
        "blocks": {str(e["id"]): {"speaker": "", "addressee": "", "referent": "",
                                  "real_or_hypothetical": "", "confidence": "", "note": ""}
                   for e in entries},
    }


def write_json(path: pathlib.Path, obj) -> None:
    C.write_text_lf(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


# --------------------------------------------------------------------------
# check
# --------------------------------------------------------------------------

def check_map(map_path: pathlib.Path, srt_blocks: list[dict]) -> tuple[list[dict], list[str]]:
    """Return (problems, warnings). Each problem: {id, issues[list of str]}."""
    warnings: list[str] = []
    try:
        data = C.read_json(map_path)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"cannot read speaker map {map_path}: {exc}")
    if not isinstance(data, dict) or not isinstance(data.get("blocks", {}), dict):
        raise SystemExit(f"{map_path} does not look like a speaker map (needs a 'blocks' object)")
    entries = data.get("blocks", {})
    selected: list[str] = []
    manifest = map_path.parent / "pack.json"
    if manifest.is_file():
        try:
            selected = [str(b["id"]) for b in C.read_json(manifest)["blocks"]]
        except (OSError, ValueError, KeyError, TypeError):
            warnings.append(f"{manifest} is unreadable; using the block ids found in the map")
    if not selected:
        selected = sorted(entries, key=lambda k: (not k.isdigit(), int(k) if k.isdigit() else 0, k))
    problems = []
    for bid in selected:
        e = entries.get(bid)
        issues: list[str] = []
        if not isinstance(e, dict):
            issues.append("no entry in speaker_map.json")
        else:
            if not str(e.get("speaker", "")).strip():
                issues.append("speaker is empty")
            if not str(e.get("addressee", "")).strip():
                issues.append("addressee is empty")
            conf = str(e.get("confidence", "")).strip().lower()
            if conf == "low":
                issues.append("confidence is low")
            elif conf not in CONFIDENCES:
                issues.append("confidence is not set (high|medium|low)")
        if issues:
            problems.append({"id": bid, "issues": issues})
    for bid in entries:
        if bid.isdigit() and not (1 <= int(bid) <= len(srt_blocks)):
            warnings.append(f"map has block {bid} but the SRT has only {len(srt_blocks)} blocks")
    for cut in data.get("cuts", []) if isinstance(data.get("cuts", []), list) else []:
        try:
            C.parse_ts(str(cut["start"])), C.parse_ts(str(cut["end"]))
        except (KeyError, TypeError, ValueError):
            warnings.append(f"cut entry with unreadable start/end: {cut!r}")
    return problems, warnings


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_build(a: argparse.Namespace) -> int:
    if re.match(r"(?i)^https?://", a.video):
        print("--video must be a LOCAL file, not a URL. Download the video first.", file=sys.stderr)
        return 2
    video, srt = C.expand_path(a.video), C.expand_path(a.srt)
    for label, p in (("video", video), ("SRT", srt)):
        if not p.is_file():
            print(f"{label} file not found: {p}", file=sys.stderr)
            return 2
    try:
        crops = parse_crops(a.crops)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not 1 <= a.frames <= MAX_FRAMES:
        print(f"--frames must be 1..{MAX_FRAMES}", file=sys.stderr)
        return 2
    if a.width < 64 or a.max_blocks < 1 or a.context_lines < 0:
        print("--width must be >= 64, --max-blocks >= 1, --context-lines >= 0", file=sys.stderr)
        return 2

    try:
        blocks = C.read_srt(srt)
    except (OSError, ValueError) as exc:   # includes C.TextDecodeError (e.g. a cp949 SRT)
        print(f"cannot read {srt}: {exc}", file=sys.stderr)
        return 2
    if not blocks:
        print(f"no subtitle blocks found in {srt}", file=sys.stderr)
        return 2
    explicit = None
    if a.blocks:
        try:
            explicit = parse_block_spec(a.blocks, len(blocks))
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    auto = a.auto or not explicit

    segments = load_sidecar(C.expand_path(a.speakers) if a.speakers else None)
    speakers = [speaker_for(b, segments) for b in blocks]
    lang = a.lang if a.lang != "auto" else detect_lang(blocks)
    scored = score_blocks(blocks, lang, speakers)
    entries = select_blocks(scored, explicit, auto, a.max_blocks)
    if not entries:
        print("no blocks selected (nothing scored high enough). Use --blocks to pick some explicitly.")

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print(C.ffmpeg_missing_message(), file=sys.stderr)
        return 3
    if _probe_video is None:
        print("frames_at.probe is unavailable", file=sys.stderr)
        return 3
    try:
        vw, vh, dur = _probe_video(str(video))
    except (subprocess.CalledProcessError, OSError, ValueError, KeyError, IndexError) as exc:
        print(f"cannot read the video with ffprobe: {exc}", file=sys.stderr)
        return 3

    out = C.expand_path(a.out)
    (out / "sheets").mkdir(parents=True, exist_ok=True)
    lay = layout(vw, vh, crops, a.frames, a.width) if entries else None

    sheets: dict[int, dict] = {}
    failed = 0
    for e in entries:
        b = blocks[e["id"] - 1]
        times = frame_times(b["start"], b["end"], a.frames, dur)
        rel = f"sheets/block_{e['id']:04d}.jpg"
        dest = out / rel
        ok = False
        last_err = "no frame produced"
        for attempt in (times, [min(t, max(dur - 1.0, 0.0)) for t in times]):
            try:
                make_sheet(str(video), attempt, crops, lay, vw, vh, dest)
                if dest.is_file() and dest.stat().st_size > 0:
                    times, ok = attempt, True
                    break
            except (subprocess.CalledProcessError, RuntimeError) as exc:
                err = getattr(exc, "stderr", "") or str(exc)
                last_err = err.strip().splitlines()[-1] if err.strip() else "unknown error"
        if ok:
            sheets[e["id"]] = {"path": rel, "times": times}
        else:
            failed += 1
            C.eprint(f"warning: block {e['id']}: could not create the sheet ({last_err})")
            sheets[e["id"]] = {"path": "", "times": times}

    C.write_text_lf(out / "index.md",
                    render_index(blocks, entries, speakers, sheets, lay, crops, a.context_lines, srt.name, lang))
    template = make_template(entries)
    write_json(out / "speaker_map.template.json", template)
    write_json(out / "pack.json", {
        "srt": srt.name, "language": lang, "frames": a.frames, "crops": crops,
        "layout": lay, "blocks": [{"id": e["id"], "score": e["score"], "reasons": e["reasons"],
                                   "start": blocks[e["id"] - 1]["start"], "end": blocks[e["id"] - 1]["end"],
                                   "image": sheets[e["id"]]["path"]} for e in entries]})

    map_path = out / "speaker_map.json"
    if map_path.exists() and not a.force:
        missing = [str(e["id"]) for e in entries if str(e["id"]) not in _existing_block_ids(map_path)]
        print(f"kept existing {map_path} (not overwritten; use --force to replace it)")
        if missing:
            print("  blocks in this pack that the existing map has no entry for: " + ", ".join(missing))
            print("  copy them from speaker_map.template.json, or `check` will list them as unfilled")
    else:
        write_json(map_path, template)

    print(f"Context pack: {len(entries)} block(s), {len(entries) - failed} sheet(s) -> {out}")
    print(f"  read      {out / 'index.md'}")
    print(f"  fill in   {map_path}")
    print(f"  then run  {C.python_cmd()} scripts/context_pack.py check --map \"{map_path}\" --srt \"{srt}\"")
    return 4 if entries and failed == len(entries) else 0


def _existing_block_ids(map_path: pathlib.Path) -> set[str]:
    try:
        return set(C.read_json(map_path).get("blocks", {}))
    except (OSError, ValueError, AttributeError):
        return set()


def cmd_check(a: argparse.Namespace) -> int:
    srt = C.expand_path(a.srt)
    if not srt.is_file():
        print(f"SRT file not found: {srt}", file=sys.stderr)
        return 2
    mp = C.expand_path(a.map)
    if not mp.is_file():
        print(f"speaker map not found: {mp}", file=sys.stderr)
        return 2
    try:
        blocks = C.read_srt(srt)
    except (OSError, ValueError) as exc:
        print(f"cannot read {srt}: {exc}", file=sys.stderr)
        return 2
    problems, warnings = check_map(mp, blocks)
    for w in warnings:
        C.eprint(f"warning: {w}")
    if not problems:
        print("OK: every block in the context pack is resolved")
        return 0
    print(f"{len(problems)} block(s) are still unresolved. Ask the user ONLY about these "
          "(after trying the frames again):\n")
    for n, p in enumerate(problems, 1):
        bid = p["id"]
        line = f"{n}. Block {bid}"
        if bid.isdigit() and 1 <= int(bid) <= len(blocks):
            b = blocks[int(bid) - 1]
            line += f"  {_tc(b['start'])} --> {_tc(b['end'])}  \"{_flat(b['text'])}\""
        print(line)
        print("   problem: " + "; ".join(p["issues"]))
        print("   question: who says this, to whom, and what does it refer to (if anything is pointed at)?")
    return 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Context pack for ambiguous subtitle blocks: contact sheets + index.md + speaker_map.json.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="pick blocks, cut contact sheets, write index.md and the speaker map")
    b.add_argument("--video", required=True, help="LOCAL video file")
    b.add_argument("--srt", required=True, help="source-language SRT")
    b.add_argument("--out", required=True, help="output folder (created)")
    b.add_argument("--blocks", help="explicit block ids (1-based positions), e.g. 12,45,50-53")
    b.add_argument("--auto", action="store_true", help="auto-select ambiguous blocks by scoring (default if no --blocks)")
    b.add_argument("--max-blocks", type=int, default=30, help="cap on the number of blocks (default 30)")
    b.add_argument("--frames", type=int, default=3, help="stills per block, 1-6 (default 3)")
    b.add_argument("--crops", default="none", help="none | left,right | left,center,right (default none)")
    b.add_argument("--context-lines", type=int, default=3, help="neighbouring subtitle lines each side (default 3)")
    b.add_argument("--width", type=int, default=960, help="total width of each sheet in pixels (default 960)")
    b.add_argument("--speakers", help="diarization sidecar JSON: [{start,end,speaker}]")
    b.add_argument("--lang", choices=["auto", "ko", "ja", "en"], default="auto",
                   help="language of the source SRT for the word lists (default auto-detect)")
    b.add_argument("--force", action="store_true", help="overwrite an existing speaker_map.json")
    b.set_defaults(func=cmd_build)
    c = sub.add_parser("check", help="list blocks in the pack that are still unresolved (exit 1 if any)")
    c.add_argument("--map", required=True, help="path to speaker_map.json")
    c.add_argument("--srt", required=True, help="source-language SRT")
    c.set_defaults(func=cmd_check)
    return ap


def main(argv: list[str] | None = None) -> int:
    C.setup_utf8_io()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
