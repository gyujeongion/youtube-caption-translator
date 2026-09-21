# Context pack — who is speaking to whom, and what "here" points at

Reference for step ⓪ in [SKILL.md](../SKILL.md). Script: `scripts/context_pack.py`.

Korean and Japanese drop the grammatical subject. "Should do that" can be *I*, *you* or
*we*; "look here" points at something only the video shows. English forces a subject, so
the translator invents one. The context pack lets the agent look at exactly the
ambiguous blocks, in one image per block, and record what it concluded in a structured
file that the translation step reads.

It works with plain image viewing. It does not need a video-understanding tool. (If your
agent has one, it may still be useful for long spans; the pack does not depend on it.)

## Workflow

```bash
# 0) coarse pass first: who is who, where, how the shots are framed
python3 scripts/frames_at.py --video "<video.mp4>" --sample 20 --out "<working folder>/frames"

# 1) build the pack (auto-selects the risky blocks)
python3 scripts/context_pack.py build --video "<video.mp4>" --srt "<source.srt>" --out "<working folder>/pack" --auto --crops left,right

# 2) read <working folder>/pack/index.md, look at each sheet, fill speaker_map.json

# 3) translate (step ①) using the map; then, before ②:
python3 scripts/context_pack.py check --map "<working folder>/pack/speaker_map.json" --srt "<source.srt>"
```

Commands use `python3`; on Windows use `py -3` (or `python`), never `python3`.

`build` options:

| Option | Meaning |
|---|---|
| `--blocks 12,45,50-53` | Explicit block ids. Ids are **1-based positions** in the SRT (the same numbering `flag_incomplete.py` prints). Always included. |
| `--auto` | Add blocks by scoring (below). Default when `--blocks` is not given. With both, explicit blocks are kept and auto fills up to `--max-blocks` in total. |
| `--max-blocks N` | Cap (default 30). Highest score first, ties by time. |
| `--frames N` | Stills per block, 1-6 (default 3). Times are spread from 15% to 85% of the block: 3 -> 15%, 50%, 85%; 1 -> 50%. Clamped inside the video. |
| `--crops` | `none` (default), `left,right` or `left,center,right`. Same regions as `frames_at.py --crop`. |
| `--context-lines N` | Neighbouring subtitle lines shown each side (default 3). |
| `--width W` | Total sheet width in pixels (default 960). |
| `--speakers FILE` | Diarization sidecar (below). Optional. |
| `--lang` | `auto` (default), `ko`, `ja`, `en`: which word lists to use. |
| `--force` | Replace an existing `speaker_map.json`. Without it the file is never touched. |

Files written to `--out`:

- `index.md` — one section per block (see below).
- `sheets/block_0012.jpg` — one contact sheet per block.
- `speaker_map.template.json` — always rewritten. The blank form.
- `speaker_map.json` — created from the template **only if it does not exist**. If you re-run
  `build` with more blocks, your filled map is left alone and the new block ids are listed;
  copy their entries from the template.
- `pack.json` — machine manifest (selected ids, scores, reasons, image paths). `check` uses it
  to know which blocks belong to the pack.

## Contact-sheet layout (fixed)

- **Rows** = the still frames in time order, top row earliest.
- **Columns** = crops from left to right in the fixed order left, center, right (the order you
  type them in `--crops` does not matter). With `--crops none` there is one column, the full frame.
- Cell width = `width / columns` (rounded down to an even number). Cell height comes from the
  tallest crop's aspect ratio; other crops are padded with black, never stretched.
- Built only with ffmpeg filters (crop, scale, pad, hstack, vstack). **No text is drawn** on the
  image, because many ffmpeg builds lack `drawtext`. The timecode of every row is in `index.md`.
- Example: 320x240 video, `--frames 3 --crops none --width 320` -> a 320x720 sheet.

## index.md

Each block section lists: block id, timecode range, the block text, the sidecar speaker label
(if any), the sheet path and the timecode of every row, **why it was selected** (with score),
the neighbouring subtitle lines (`>>` marks the block, labels in `[ ]` come from the sidecar),
and the questions to answer:

1. Who is speaking? 2. To whom? 3. What does each deictic word point at? 4. Is the action real
or hypothetical (a plan, a recording being played)? 5. Is this a cut from another point in time?

## Auto-selection scoring

All numbers and word lists are constants at the top of `scripts/context_pack.py`
(`LANG_RULES` and the `SCORE_*` values). A block is auto-selected when its score is at least 2.
Every selected block records its reasons.

| Signal | Score | Notes |
|---|---|---|
| Deictic / demonstrative word | +3 | ko: 여기 저기 거기 이거 저거 그거 이쪽 저쪽 이게 저게 그게 이건 저건 그건 이것 저것 그것 (matched at the start of a word). ja: ここ そこ あそこ これ それ あれ こっち そっち あっち (substring match, so expect some false positives). en: this one, that one, these/those ones, over here, over there, right here, right there. |
| Very short utterance | +2 after a gap of 1.0 s or more, or at a sidecar speaker change; +1 otherwise | ko/ja: 6 letters or fewer. Other languages: 4 words or fewer. Subject-drop hotspot. |
| Truncated utterance | +3 | Korean sources only: `flag_incomplete.py`'s strong signals, plus a cut-off seam with the next block. Skipped silently for other languages. |
| Speaker change | +2 | Only with a sidecar: the block's speaker differs from the previous block's. |
| Question or imperative ending | +1 | The addressee is often ambiguous. ko: `?` 까요 니까 나요 냐 니 죠 / 줘 봐 세요 해라 하자 가자 보자. ja: か かな でしょ / ください なさい して 見て 来て. en: a final `?`, or a first word like let's, please, come, look, try, give, take, wait. |

A question ending alone (+1) does not qualify; it does when it combines with another signal.
To add a language, add an entry to `LANG_RULES` and extend `detect_lang`.

## Diarization sidecar (optional, read-only here)

```json
[{"start": 12.3, "end": 15.8, "speaker": "S1"}, {"start": 15.9, "end": 17.0, "speaker": "S2"}]
```

Times in seconds. A block gets the speaker with the largest time overlap (at least 0.05 s).
Missing or malformed files produce a warning and are ignored; bad entries are skipped.
`transcribe.py --diarize` (Deepgram or Soniox only) writes this file next to the SRT; without it, the pack works the same, just without speaker labels.

## speaker_map.json schema

```json
{
  "people": {"A": {"description": "", "register": "", "evidence": []}},
  "cuts": [{"start": "00:00:00,000", "end": "00:00:06,000", "note": "intro hook pulled from later"}],
  "blocks": {
    "12": {"speaker": "", "addressee": "", "referent": "", "real_or_hypothetical": "",
           "confidence": "high|medium|low", "note": ""}
  }
}
```

- `people`: a key per person you identified (add more than "A"). `description` = stable
  identifiers (seat, build, a hat that does not change), `register` = polite / casual and how it
  can shift, `evidence` = frames or block ids you used.
- `cuts`: spans that are not continuous with their surroundings (SRT timecodes).
- `blocks`: keyed by block id (string). `speaker` / `addressee` = a key from `people` or a plain
  description. `referent` = what a deictic word points at. `real_or_hypothetical` = `real`,
  `hypothetical` or `unclear`.
- **Unresolved means `confidence: "low"`** with the reason in `note`. Do not write "unknown"
  and mark it high.

## check

`context_pack.py check --map ... --srt ...` exits 0 when every block of the pack is resolved and 1
otherwise. It flags, per block: no entry in the map, empty `speaker`, empty `addressee`,
`confidence` low or not set. It prints a numbered list with the block timecode and text. That
list is the **only** thing you put to the user: first try to settle each block from the frames
again, then ask about what remains. In `direct` publish mode, unresolved blocks still stop the
upload (see "Publish mode" in SKILL.md).

## Viewing the sheets efficiently

- Open `index.md` first, then the sheet of one block at a time. One image already shows all
  the stills, so you do not need to grab frames one by one.
- Use `--crops left,right` for two people side by side, and `left,center,right` for three. For a
  single speaker or a screen recording use `none`.
- Use `--frames 5` for a long block, or when the shot changes inside it.
- If you cannot view images at all, say so and turn the listed blocks into user questions.
  Do not fill the map from the text alone.

## Honest limits

- **Mouth movement from stills is unreliable.** Three frames rarely catch a mouth mid-word.
  Treat mouth shape as weak evidence; gaze, gesture, who is holding the object, and who is
  facing the camera are stronger. Use `confidence: medium` when it is the only clue.
- **Off-screen speakers cannot be seen.** A voice from outside the frame looks like silence on
  everyone. Say so in `note` and ask the user.
- The crop regions are fixed proportional boxes, not face detection. If people stand in the
  middle of the frame, use `left,center,right` or `none`.
- Speaker labels only exist if the transcript came from `transcribe.py --diarize` (Deepgram or
  Soniox). For an editor SRT the sidecar is absent, and the labels come from the frames alone.
- Auto-selection is a heuristic. It over-selects on Japanese substring matches and can miss an
  ambiguous block with none of the signals; add those with `--blocks`.
- Block ids are positions in the source SRT. If you edit the SRT (adding or removing blocks)
  after building, rebuild the pack.
