---
name: youtube-caption-translator
description: Translate an editor-exported SRT (CapCut, Premiere, etc.) into foreign-language YouTube caption tracks, OR transcribe the video with a speech-to-text (STT) engine when there is no SRT, then upload multilingual caption tracks. Includes a guided first-run setup (Google Cloud OAuth, STT key or local model, publish mode). Use when the user asks to add English (or other language) subtitles to a video, translate an SRT, make subtitles from a video that has none, upload a YouTube caption track, set up this skill, localize a video title for another locale, or extract thumbnail candidates from a video.
---

# youtube-caption-translator — SRT (or STT) → translation → YouTube caption tracks

An SRT pulled out of a video editor is **speech chopped into fragments with
timecodes stapled on**. If you translate those fragments 1:1, sentences get
cut in half mid-thought ("My approach is," / "since this recipe is called
X," / "the point is to show"). So this skill's default approach is to
**merge fragments into full sentences, translate those, and re-lay the
timecodes** rather than translating line by line.

The person using this skill is usually a YouTuber, not a developer. Talk in
plain language, one question at a time, and **you** (the agent) run every
command. The user only makes choices, clicks in their own browser, and types
API keys into a hidden prompt in their own terminal. Unfamiliar words
(OAuth, quota unit, SRT...) are explained in the glossary at the end of
[references/setup-wizard.md](references/setup-wizard.md#glossary) — link it
to the user when they ask.

## Pipeline

```
Phase 0  check_setup.py       first-run wizard (once) — see "Phase 0" below
Ⓐ  source SRT                 per video: editor SRT first; if none, STT (transcribe.py)
video.mp4 ──⓪   frames_at.py + context_pack.py   identify speakers & context (required before translating)
source.srt ──①   sentence-merged translation ──▶ <name>.<lang>.srt
          ①.5 flag_incomplete.py    detect truncated utterances (Korean sources only)
          ②   verify_srt.py         mechanical check (boundaries, overlap, line length)
          ③   align_pairs.py        source↔target side-by-side table for nuance review
          ④   upload_caption.py     create/update the YouTube caption track
          ⑤   set_localization.py   set a locale-specific title (e.g. "(ENG SUB)")
          ⑥   extract_thumbnails.py extract & gallery candidate thumbnail frames
```

The circled markers (Ⓐ ⓪ ① ...) are only labels. If a console does not draw
them, read them as A, 0, 1, 1.5, 2 ...

## Phase 0 — first-run setup wizard

**Which Python?** The docs write `python3`; substitute yours. **Windows: try
`py -3 --version` first, then `python --version`; never `python3`** (on
Windows it can be the Microsoft Store stub). `check_setup.py` prints a NOTE
line when `python` is that Store shortcut. macOS/Linux: `python3`, then
`python`. If none works, Python 3.10+ is missing: ask before installing.
Commands here are one-liners (PowerShell continues lines with a backtick,
cmd with `^`). In PowerShell 5.1 never redirect output with `>` (it writes
UTF-16); use each script's own `-o` / `--out`. Paths with spaces need quotes.

**Run this first, every session.** It takes one command:

```bash
python3 scripts/check_setup.py
```

It prints a checklist (`[OK     ]`, `[MISSING]`, `[optional]`), a line like
`Input: SRT always works; fallback when there is no SRT: <mode|none>
(ready|not ready)`, and ends with one line starting `NEXT STEP:`. Exit code 0
means "the YouTube token is valid AND, if the saved `input_mode` is
`stt_api` or `stt_local`, that fallback is ready" — then skip the wizard and
go to Ⓐ. Exit code 1 means something is missing: follow the `NEXT STEP:`
line. The commands it prints use the interpreter name you ran it with.

If setup is incomplete, read **[references/setup-wizard.md](references/setup-wizard.md)**
and follow it exactly. In short, ask four questions (use AskUserQuestion
when your tool has it — one focused question with 2-4 options each; plain
chat otherwise), and store each answer right away:

1. **Languages** — spoken language of the videos, and which languages to
   translate into.
2. **What to do when a video has no SRT** — `input_mode`. The editor SRT is
   **always used first, per video**; this answer only decides the fallback
   (`srt` = none, `stt_api`, `stt_local`).
3. **Publish mode** — `review` (recommended for the first videos) or `direct`.
4. **YouTube channel** — which channel the captions go to.

```bash
python3 scripts/check_setup.py --set-pref source_language=ko target_languages=en,ja input_mode=stt_api stt_engine=deepgram publish_mode=review
python3 scripts/check_setup.py --show-prefs
```

Then, depending on the answers:

- Need a YouTube token → walk through
  **[references/google-cloud-setup.md](references/google-cloud-setup.md)**, then run
  `reauth_channel.py` (see Credentials below).
- Chose cloud STT → **[references/stt-cloud.md](references/stt-cloud.md)**.
- Chose local STT → **[references/stt-local.md](references/stt-local.md)**.

**API keys never go through chat.** Never ask the user to paste an API key
into the conversation, and never type one into a command yourself. Tell the
user to run `python3 scripts/check_setup.py --set-key <deepgram|openai|soniox>`
**in their own terminal** (exact Windows steps are in the wizard): it shows a
hidden prompt (nothing is echoed) and saves the key to a private file
(`chmod 600` on macOS/Linux; Windows does not restrict it, so keep it in the
user profile folder). It needs a real keyboard, so it refuses to run inside an
agent shell (exit code 2). The user types or pastes the key **into that
prompt only**. Then you re-run `check_setup.py` to confirm the key is present
(it reports yes/no only, never the value).

**Prices, model names and console menu labels go stale.** The reference
files carry a "verified 2026-09-22" stamp and a source URL for each. Before
you recommend anything from them, re-open the source URL and confirm it
still holds. If it does not, say so and use what the page says today. Never
name a model from memory.

Prefs live in `$YTCAPTION_HOME` if that variable is set, otherwise in
`~/.claude/credentials/` (on Windows `%USERPROFILE%\.claude\credentials\`);
files `ytcaption_prefs.json` and `ytcaption.env`. Keys in the environment
override the env file. Never point `YTCAPTION_HOME` into a Dropbox / Google
Drive / OneDrive folder: the keys would sync to the cloud.

## Ⓐ Get a source SRT (per video)

The rest of the pipeline needs one source-language SRT per video.
**Per video, the editor SRT always comes first.** `input_mode` in prefs only
says what to do **when there is no SRT**. Mixed batches are normal: for
example one video with an SRT and three without. Use the SRT for the first
one and the fallback for the other three. Ask the user which videos have an
SRT before you start.

**Path 1 — SRT from the editor (recommended).** The editor (CapCut,
Premiere, etc.) already timed the words against the real audio, so timing
is best and it costs nothing. If the video has one, go straight to ⓪.

**No SRT — try the free options first, then STT.** In this order:

1. **The editor's own auto-caption export** (for example CapCut auto
   captions, then export as SRT). *Not verified by this project; check your
   editor.*
2. **YouTube's auto-generated captions**, downloaded from YouTube Studio.
   The video must already be uploaded (private or unlisted is fine). *Not
   verified by this project; check Studio.* Quality varies.
3. **Path 2 — cloud STT API** (small cost). Recommend one provider from
   [references/stt-cloud.md](references/stt-cloud.md) (after re-verifying it
   there; Deepgram first when the user has no account, because of its
   free credit with no card). Get a key with `check_setup.py --set-key
   <provider>`, then:

```bash
# preview first: no API call, no cost
python3 scripts/transcribe.py --input "<video.mp4>" --engine deepgram --lang ko --out source.srt --dry-run
python3 scripts/transcribe.py --input "<video.mp4>" --engine deepgram --lang ko --out source.srt --prompt "channel name, guest names, product terms"
```

4. **Path 3 — local STT (free).** Run `python3 scripts/detect_hardware.py`
   (add `--json` for machine-readable output). **If it says
   `recommended.prefer_cloud` is true, recommend cloud STT instead** and say
   plainly that Korean quality on small local models is unverified and likely
   poor. Otherwise follow [references/stt-local.md](references/stt-local.md),
   including its "re-verify the best model today" recipe:

```bash
python3 scripts/transcribe.py --input "<video.mp4>" --engine local --lang ko --out source.srt --dry-run
python3 scripts/transcribe.py --input "<video.mp4>" --engine local --lang ko --out source.srt --yes
```

`transcribe.py` options: `--engine {deepgram,openai,soniox,local}`,
`--model MODEL`, `--runtime {auto,whisper.cpp,mlx-whisper,faster-whisper}`
(local only), `--keep-audio`, `--dry-run`, `--prompt "proper nouns..."`,
`--yes`, `--force`, `--diarize`.

- `--dry-run` checks prerequisites and prints the plan without any API call.
  For a local engine that would download a model on first use
  (mlx-whisper, faster-whisper) it says "will download ~N MB". **Tell the user
  the size and ask; only then re-run with `--yes`**, which is required to
  allow that download.
- `--diarize` (optional, off by default; only for multi-speaker videos and
  only with `deepgram` or `soniox`, other engines exit 2) also writes
  `<out stem>.speakers.json`, which you can pass to
  `context_pack.py build --speakers`. Deepgram's diarization is a paid add-on
  whose price this project has not verified (not in the cost estimate);
  Soniox includes it. Details: [references/stt-cloud.md](references/stt-cloud.md).
- `--force`: by default the script refuses to overwrite an existing `--out`
  file, kept audio file or speakers sidecar (exit 2). Pass `--force` only when you mean to replace it.
- For a local whisper.cpp binary that is not on PATH, set the environment
  variable `YTCAPTION_WHISPER_CLI` to the full path of `whisper-cli`.
- Exit codes: 0 ok · 2 bad arguments (including refusing to overwrite) · 3
  missing dependency or key (for a missing local runtime it prints the exact
  install command; **do not install it silently — show the command and ask
  the user**) · 4 engine/API error.

STT output is a **draft, not ground truth**:

- Read it before translating. Proper nouns, product names and English
  terms inside another language are the usual mistakes.
- Accuracy for Korean has **not** been benchmarked by us and no verified
  winner exists among the providers. If the user cares, suggest running two
  engines on one short video and comparing by eye.
- Then continue with ⓪ as usual, treating the STT SRT as the source SRT
  (`flag_incomplete.py` and the rest work on it the same way).

## Publish mode and quota (applies to ④ and ⑤)

`publish_mode` in prefs decides who approves an upload:

- **review** — translate → mechanical verify → show the human review table →
  upload **only after explicit approval** for that video. Recommended for
  the first videos. **For the very first upload, use `--draft`** (a private
  draft): captions are public once published, and replacing a track later
  costs 450-500 quota units. The user checks the draft in YouTube Studio,
  then publishes it there.
- **direct** — translate → mechanical verify (must pass) → upload
  immediately, no per-video approval. The user chose this at setup time and
  **that choice is the approval**. It does **not** remove the safety gates:
  stop and tell the user if `verify_srt.py` fails, or if subject-ambiguous
  blocks remain that the frames could not resolve (never ship a coin flip).
  Direct mode covers caption upload only; a localized title (⑤) still needs
  the user to pick the wording.

**Quota rule.** YouTube allows roughly 10,000 API units per day per Google
Cloud project by default. `upload_caption.py` first lists existing tracks
(50 units), so a **new** track costs about 450 and a **replaced** track about
500. Running `--list` yourself first costs another 50, and each
`check_setup.py` run spends 1. That is about **22 new tracks per day**.
**Before any batch, tell the user the estimate.** Worked example: 4 videos ×
2 languages = 8 tracks ≈ 3,600 units (plus about 50 per localized title
update), which fits in one day. If an estimate is over the day's budget,
split it across days. Quota resets at midnight Pacific Time, which is
approximately 16:00-17:00 in Korea (depends on daylight saving). Details and
sources: [references/google-cloud-setup.md](references/google-cloud-setup.md).

### ⓪ Look at the video first — the subtitles alone don't tell you the subject

Many languages (Korean, Japanese, etc.) freely drop grammatical subjects.
"Yeah, should do that" could be *I should* or *you should*; "actually going
to cook it live" could mean cooking on camera or just pressing play on a
recording. That
information isn't in the text. Writing it into English forces you to invent
a subject that was never stated. So look at the video **before** you start
translating.

The video file is usually next to the SRT. `frames_at.py` works on a
**local file only** (not a URL); if the video is online, download it first
(for example with `yt-dlp`) or ask the user for the file. **If you can't get
the video, or you can't view images, say so up front** — don't guess on
blocks where the subject is ambiguous; list them as questions for the user
instead of putting a coin-flip translation into the captions.

```bash
# 1) Evenly sample the whole video first to establish who's who, where, and how the shots are framed
python3 scripts/frames_at.py --video "<video.mp4>" --sample 20 --out "<working folder>/frames"

# 2) Recommended: build a context pack for the ambiguous blocks (auto-selected), one contact sheet per block
python3 scripts/context_pack.py build --video "<video.mp4>" --srt "<source.srt>" --out "<working folder>/pack" --auto --crops left,right
```

Then open `<working folder>/pack/index.md` and look at each block's sheet
(rows = stills in time order, columns = person crops). Fill
`<working folder>/pack/speaker_map.json` with who speaks to whom, what each
deictic word points at, whether the action is real, and a `confidence`. Add
blocks the auto-selection missed with `--blocks 12,45,50-53`. If the frames
cannot settle a block, set `confidence` to `low` instead of guessing. Layout,
scoring, schema and limits: **[references/context-pack.md](references/context-pack.md)**.
If your agent has a video-understanding tool it may use it for long spans; the
pack works without one. Manual alternative for a single spot:
`python3 scripts/frames_at.py --video "<video.mp4>" --at "4:26,9:12.5" --crop left,right --out "<working folder>/frames"`.

(`<working folder>` is any folder you can write to, for example next to the
video. Commands here use `python3`; on Windows use `py -3` or `python`.)

Read the extracted frames directly. Check four things:

1. **Who is who** — pin a stable per-person identifier (seat, build, a hat
   that doesn't change) rather than clothing, which can change mid-shoot.
2. **Register / formality level vs. what's on screen** — a default mapping
   (e.g. casual speech = older person, polite speech = younger person) can
   flip when someone gets excited. **If the verb ending and the frame disagree, trust
   the frame.**
3. **Cuts** — an intro hook is often pulled from later in the same video.
   If the clothing differs, it's a different point in time; don't stitch
   the surrounding context together as if it were continuous.
4. **What a deictic word refers to** — "here", "this", "that pan" pointing
   at something on screen. If it's not visible in frame, don't guess —
   flag it as a question instead.

Write the result down once as a **speaker map** (`speaker_map.json` from the
pack, or by hand as below) and refer back to it throughout translation.
Example:

```
Left  = Person A · white cap · standing at the stove · casual register · owns the cooking
Right = Person B · moved from the couch to the counter · polite register · owns the recipe idea
00:00-00:06 intro hook = pulled from the 04:16-04:26 segment
```

Only the blocks where the subject is ambiguous need a cropped check — that's
usually 5-15 spots per video, not the whole runtime frame by frame. The pack
selects them for you; run `context_pack.py check --map ... --srt <source.srt>`
after translating and **before ②**. It exits 1 and prints a numbered list of
blocks still unresolved (empty speaker/addressee, low confidence): ask the user
about **those only**.

### ① Translation — meaning before timecodes

1. Read the source SRT and **ignore the timecodes at first** — understand
   the whole conversation as a unit.
2. Group source entries into semantic units (one sentence / one breath).
3. Merged-group start = first fragment's start, end = last fragment's end.
   **Only place boundaries on original boundaries** (never invent a new
   timecode) — the verification step checks exactly this.
4. **Don't auto-merge adjacent fragments just because they're close in
   time.** In multi-speaker footage with irregular interruptions (vlogs,
   interviews), neighboring fragments can belong to a different speaker, a
   different scene, or a different cut (e.g. an intro hook pulled from
   elsewhere). Time adjacency alone doesn't imply continuity.
5. Mechanically pre-screen for truncated utterances using particle/ending
   patterns (see ①.5 below). For flagged blocks, or blocks where the
   speaker/scene connection is unclear, **check the frames from ⓪ first**
   (translate each block that has an entry in `speaker_map.json` with that
   entry's speaker, addressee and referent), and only escalate to the user
   what the frames can't resolve (unresolved = `confidence: low`). Don't fill
   gaps with a guess. Keep the question list short — resolve what frames
   can resolve before asking a person.
6. Merge rules
   - one block is at most ~7 seconds (verification warns past 9s), max 2
     lines, roughly 42 characters per line
   - don't merge across a gap of more than 1 second (the caption would sit
     over silence)
   - mark speaker changes with `—` or split into separate blocks
7. Translation principles
   - No literal translation. Write what a native speaker of the target
     language would actually say in that situation
   - Keep industry jargon and proper nouns as-is: software names, brand
     names, artist names, technical terms
   - Render source-language idioms as the functionally equivalent target
     idiom, not a literal rendering
   - Keep interjections and verbal tics, but in the target language's own
     rhythm

### ①.5 Detect truncated utterances (before translating, required)

```bash
python3 scripts/flag_incomplete.py <source.srt>
```

**Korean sources only** (it prints a skip line for other source languages).
Run this bare form on the source SRT **before** you translate. Once a
translation exists you can also re-check with
`python3 scripts/flag_incomplete.py <source.srt> --merged <translation.srt>`.

Uses particle/ending patterns to mechanically catch "there was more speech
after this, but the editor cut it" (e.g. a connective particle followed
directly by a predicate instead of the noun it requires — a sign the
in-between span was cut). For flagged blocks, don't fill the gap from
context — go back and confirm the actual utterance (from the frames, or
by asking).

### ② Mechanical verification (never skip)

```bash
python3 scripts/verify_srt.py <source.srt> <translation.srt>
```

If entry counts differ, it automatically switches to merged mode. Checks
whether boundaries sit on original boundaries, don't overlap, fully cover
the original spoken spans, and whether any line/duration is excessive.
Use `--strict` for a 1:1 translation.

### ③ Nuance review

```bash
python3 scripts/align_pairs.py <source.srt> <translation.srt> -o "<working folder>/review.md"
```

(`-o` needs an existing folder.)

Produces a side-by-side table of source and target text per block. Check
three things:

1. **Distortion / omission of meaning** — added information not in the
   source, a dropped clause, a flipped nuance
2. **Naturalness** — actual spoken language, not translation-ese
3. **Term preservation** — are proper nouns / product / technical terms kept
   as-is?

**A grammatically complete sentence can still be a mistranslation.**
`flag_incomplete.py` only catches particle/ending truncation. The
following is the kind of error a machine can't catch and a reader can miss
without knowing the shoot itself:

- **Speaker/addressee direction** — a subject-dropping sentence like "yeah,
  should do that" is easy to translate backwards without a stated pronoun.
  When ambiguous, don't default to "you" or "I" — confirm who actually said
  it.
- **Whether an action is real, and its tense** — "actually going to cook it
  live" could mean cooking on camera (not pre-recorded) or just pressing
  play, and that only resolves with context about what's being filmed.
- **Physical deixis** — "if you fall from here it's serious" where "here"
  might be an off-camera balcony. Don't guess at a referent that's not
  visible on screen.
- **Idioms taken literally** — a phrase like "melting" used metaphorically
  (someone looking exhausted) vs. literally. Pin down who's being talked
  about before translating the idiom.
- **Place names / proper nouns mistaken for common nouns** — a restaurant
  or shop name that looks like an ordinary word; match the preposition to
  the convention ("at Name" not "on Name" or vice versa, depending on
  usage).

These five categories can look grammatically fine and still be a coin flip
without knowing the people, the blocking, and the industry context of the
shoot. The first four are usually resolved by the frames from ⓪ — pull that
timestamp again with `frames_at.py --crop left,right` and check mouth
movement, hand gesture, gaze. Only flag what the frames genuinely can't
resolve (knowledge not visible on screen — a dish or product name, a quote's
source, industry slang's English equivalent) for the user — don't fabricate a
plausible-sounding guess.

For an important video, run this through a second independent model (or a
second reviewer) once more here. Fix only the flagged blocks and return to
②.

**Present the review as a browsable artifact**, not a wall of text in
chat — a long list (100+ blocks) doesn't scan well inline. A two-column
source/target table with timecodes and a search box works well; if your
tooling supports publishing a live page, republish to the same URL after
revisions rather than creating a new link each round.

### ④ Upload

```bash
# check current tracks (costs 50 quota units; skip it if you don't need it)
python3 scripts/upload_caption.py --video <videoId> --list

# upload (updates if a track in that language already exists, else creates one)
python3 scripts/upload_caption.py --video <videoId> --srt "<translation.srt>" --lang en --name "English" --draft
```

`--dry-run` lists the existing tracks and uploads nothing (the list call
still spends 50 units). `--token` is optional: it defaults to `token_file` from prefs, else
`my_channel_token.json` (resolved in the config folder). Drop `--draft` to
publish. The **video ID** is the part after `v=` in a watch URL, the part
after `youtu.be/`, or the ID in the Studio video-details URL. The video must
already be uploaded to YouTube (private or unlisted is fine).

`--draft` uploads as a private draft invisible to viewers, for review
before publishing from Studio. **Uploading changes public content — in
`review` mode get explicit approval before running this, and use `--draft` for the first
upload; in `direct` mode
the setup-time choice is the approval (see "Publish mode and quota").
Either way, show the quota estimate first for batches.**

### ⑤ Title localization — show a localized title only to that locale

If you've added a foreign-language caption track, localizing the title to
match is the natural next step. **Principle: a title suffix like "(ENG
SUB)" should only be visible to English-locale viewers.** Never touch the
default title — only add/replace the localization for that language. Don't append a suffix
directly to the default title, or it becomes visible to every viewer
regardless of locale.

```bash
# inspect current state (default title / defaultLanguage / existing localizations)
python3 scripts/set_localization.py --video <videoId> --show

# set locale-specific titles; --title LANG=TEXT is repeatable
python3 scripts/set_localization.py --video <videoId> --title "en=Cooking a Full Korean Meal in 15 Minutes (ENG SUB)" --title "ja=15分で作る韓国料理フルコース (日本語字幕)"
```

Put the quotes around the **whole** `LANG=TEXT` argument (that works in
PowerShell, cmd and bash). `--en-title X` still works as an alias for
`--title "en=X"`. `--token` is
optional here too (same default as ④).

**Nothing appends a suffix like "(ENG SUB)" automatically.** Propose a
handful of title options per language that summarize the situation
concisely, get the final choice, and pass it verbatim (suffix included).
Tone varies per video, so this isn't automated.

**The `defaultLanguage` trap:** YouTube decides which locale sees the `en`
localization by the `localizations` map, not by `defaultLanguage`. A
channel can end up with `defaultLanguage=en` while its default title is
still in the original language — in that state, English-locale viewers see
the *original-language* title instead of the localization (the matching
gets inverted). By default `set_localization.py` corrects
`defaultLanguage` to the original spoken language (default: `source_language`
from prefs, else `ko`). If the
footage was actually shot in another language, pass
`--original-language <code>`; if you've already confirmed
`defaultLanguage` is set correctly, pass `--keep-default-language` to skip
the correction.

**Set this once per channel's convention, not per video from scratch.**
After a caption upload, it's worth asking whether the title should also be
localized, rather than waiting for the user to bring it up separately.

### ⑥ Thumbnail candidate extraction — decode the source instead of screen-capturing the player

Pausing the player and screenshotting caps resolution at playback quality
and captures player UI (scrubber, etc.). `extract_thumbnails.py` has
ffmpeg decode the original source directly — frame extraction, not a
screen capture.

**Step 0 — pick candidate timestamps.** Don't guess timestamps. Review the
whole video frame-by-frame first (dialogue alone won't tell you framing,
expression, or color), and pick timestamps where a face is sharp and a
single subject fills the frame, plus timestamps that prove something
specific about this video (a distinctive location, production scale,
etc.).

```bash
# 1) burst-extract several frames per candidate timestamp (to filter out motion blur)
python3 scripts/extract_thumbnails.py grab --source "https://youtu.be/<videoId>" --timestamps "0:06,2:38,3:03,4:53,5:01,7:30,8:33" --out "<working folder>/thumb_candidates"

# 2) review each burst folder (sharpness, expression, number of elements in frame)
#    and copy the best pick from each into picks/ as "01_label.png"

# 3) bundle the picks into a base64-inlined HTML gallery for side-by-side review
python3 scripts/extract_thumbnails.py gallery --picks-dir "<working folder>/thumb_candidates/picks" --out "<working folder>/thumb_candidates/gallery.html" --title "<video title> thumbnail candidates"
```

`grab` needs `yt-dlp` only when given a URL; it downloads the best available quality (1080p cap by
default) — don't reuse a lower-resolution copy you may have
downloaded for analysis elsewhere. Pass `--keep-source` to reuse the
downloaded file across multiple calls in the same session instead of
re-downloading every time.

**Why a base64-inlined HTML gallery**: sending several full-resolution PNGs
(megabytes each) as separate file attachments can hit network timeouts and
deliver inconsistently across devices. Resizing to JPEG and inlining into
one HTML page renders reliably everywhere from one link.

**Final selection should come from the original PNG (1080p), not the JPEG
gallery** — the gallery's JPEGs are a compressed review copy, not what you
upload.

Copy the chosen PNG from `<working folder>/thumb_candidates/picks/` to
wherever you upload thumbnails from (any file manager, or `cp` on macOS/Linux
/ `Copy-Item` in PowerShell).

## Platform-native A/B testing is not exposed via API

YouTube Studio's built-in title/thumbnail A/B test feature has no public
YouTube Data API v3 endpoint — it's Studio-web-UI only, gated behind
Advanced features, and only configurable from a computer. `upload_caption.py`
/ `set_localization.py` automate captions and title localization; a
platform-native title/thumbnail experiment has to be set up manually in
Studio.

## Credentials

Setting up the Google Cloud project and OAuth client from scratch is a
guided walkthrough: **[references/google-cloud-setup.md](references/google-cloud-setup.md)**.
The `token_file` and `client_secret` names are stored in prefs (defaults
`my_channel_token.json` and `client_secret.json`).

Each channel needs its own OAuth refresh-token file (created via
`reauth_channel.py`), while the OAuth client (`client_secret.json`,
downloaded from Google Cloud Console) can be shared across multiple
channels/projects — it's just an API calling-card, not a channel identity.
`reauth_channel.py` cross-checks the channel ID after auth, so pointing the
same client_secret at multiple channels is safe and won't silently
overwrite the wrong token.

```bash
python3 scripts/reauth_channel.py --token my_channel_token.json --secret client_secret.json --expect-channel UCxxxxxxxxxxxxxxxxxxxxxx
```

The script opens the browser, listens on `http://127.0.0.1:<port>` and waits
**up to 10 minutes** (it ignores stray requests). Run it in the background or
with a 10-minute timeout: an agent shell's default 120-second timeout would
kill it while the user is still clicking. Tell the user they have about 10
minutes.

The "Choose account or brand account" step on the consent screen is the
one place a mistake sends the token to the wrong channel — that's exactly
what `--expect-channel` guards against.

**If the OAuth app is in "Testing" status, the refresh token expires after
7 days.** If re-authorizing that often gets tedious, publish the GCP OAuth
consent screen's Audience to "In production" (the "Publish app" button).
Google documents the 7-day limit for Testing only; it does not explicitly
guarantee that tokens then last forever for an unverified app that uses a
sensitive scope, so if `invalid_grant` returns, just re-run
`reauth_channel.py`. Never paste tokens or `client_secret.json` contents
into chat.

## Fallback if the API is unavailable

If you can't get a fresh token quickly, use a logged-in real browser to
upload the caption file manually at
`https://studio.youtube.com/video/<videoId>/translations`.

## Gotchas

- **The caption file's name is meaningless.** Language is set by the API's
  `language` field, not the filename
- This is a separate track from YouTube's auto-translate. A manual track
  takes priority when one exists
- Don't put URLs or hashtags in captions (policy risk)
- The parser handles a BOM and CRLF line endings in the source SRT. Source
  SRTs must be UTF-8 (UTF-8 with BOM and UTF-16 with BOM are also accepted;
  cp949 / ANSI files are refused with an instruction: in Notepad, File > Save
  As > Encoding: UTF-8). Save your own files as UTF-8
- A video can only have one track per language. Re-uploading auto-updates
  (PUT) the existing one
- **Studio's Translations page can fail to show a caption-only track
  uploaded via the API.** That page is bundled with title/description/audio
  localization, so a track added purely through `captions.insert` may not
  appear there. Check real state with `upload_caption.py --list` (the API),
  not the UI. An empty UI list doesn't mean the upload failed.
- **Right after uploading or updating a caption, the CC button on the
  actual watch page can show "captions unavailable" for anywhere from a
  few minutes to a couple of hours** — sometimes affecting other,
  already-published language tracks too (looks like serving-cache
  reindexing). The API reporting `status=serving` doesn't mean the player
  has caught up yet. This delay doesn't get fixed by re-uploading —
  don't retry in a loop, just wait and recheck the CC button. If you need
  to publish urgently regardless of the delay, publishing itself (without
  `--draft`) is fine — it becomes visible automatically once serving
  catches up.
