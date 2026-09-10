---
name: ytcaption
description: Translate an editor-exported SRT (CapCut, Premiere, etc.) into a foreign-language caption track and upload it to a YouTube video. Use when the user asks to add English (or other language) subtitles to a video, translate an SRT, upload a YouTube caption track, localize a video title for another locale, or extract thumbnail candidates from a video.
---

# ytcaption — SRT translation → YouTube caption track

An SRT pulled out of a video editor is **speech chopped into fragments with
timecodes stapled on**. If you translate those fragments 1:1, sentences get
cut in half mid-thought ("My approach is," / "since the album's called
X," / "the point is to show"). So this skill's default approach is to
**merge fragments into full sentences, translate those, and re-lay the
timecodes** rather than translating line by line.

## Pipeline

```
video.mp4 ──⓪   frames_at.py         identify speakers & context (required before translating)
source.srt ──①   sentence-merged translation ──▶ <name>.<lang>.srt
          ①.5 flag_incomplete.py    detect truncated utterances
          ②   verify_srt.py         mechanical check (boundaries, overlap, line length)
          ③   align_pairs.py        source↔target side-by-side table for nuance review
          ④   upload_caption.py     create/update the YouTube caption track
          ⑤   set_localization.py   set a locale-specific title (e.g. "(ENG SUB)")
          ⑥   extract_thumbnails.py extract & gallery candidate thumbnail frames
```

### ⓪ Look at the video first — the subtitles alone don't tell you the subject

Many languages (Korean, Japanese, etc.) freely drop grammatical subjects.
"Yeah, should do that" could be *I should* or *you should*; "actually going
to play it live" could mean a live performance or just pressing play. That
information isn't in the text. Writing it into English forces you to invent
a subject that was never stated. So look at the video **before** you start
translating.

The video file is usually next to the SRT. If it's not available, ask for
a URL. **If you can't get the video, say so up front** — don't guess on
blocks where the subject is ambiguous; list them as questions instead of
putting a coin-flip translation into the captions.

```bash
# 1) Evenly sample the whole video first to establish who's who, where, and how the shots are framed
python3 scripts/frames_at.py --video "<video.mp4>" --sample 20 --out /tmp/frames

# 2) At moments where the speaker is ambiguous, crop per-person and check mouth movement
python3 scripts/frames_at.py --video "<video.mp4>" \
    --at "4:26,9:12.5" --crop left,right --out /tmp/frames
```

Read the extracted frames directly. Check four things:

1. **Who is who** — pin a stable per-person identifier (seat, build, a hat
   that doesn't change) rather than clothing, which can change mid-shoot.
2. **Register / formality level vs. what's on screen** — a default mapping
   (e.g. casual speech = senior, polite speech = junior) can flip when
   someone gets excited. **If the verb ending and the frame disagree, trust
   the frame.**
3. **Cuts** — an intro hook is often pulled from later in the same video.
   If the clothing differs, it's a different point in time; don't stitch
   the surrounding context together as if it were continuous.
4. **What a deictic word refers to** — "here", "this", "that synth" pointing
   at something on screen. If it's not visible in frame, don't guess —
   flag it as a question instead.

Write the result down once as a **speaker map** and refer back to it
throughout translation. Example:

```
Left  = Person A · white cap · seated at the DAW · casual register · owns arrangement/mix
Right = Person B · moved from couch to mic · polite register ("hyung") · owns the song idea
00:00-00:06 intro hook = pulled from the 04:16-04:26 segment
```

Only the blocks where the subject is ambiguous need a cropped check — that's
usually 5-15 spots per video, not the whole runtime frame by frame.

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
   speaker/scene connection is unclear, **check the frames from ⓪ first**,
   and only escalate to the user what the frames can't resolve. Don't fill
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
python3 scripts/flag_incomplete.py <source.srt> --merged <translation.srt>
```

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
python3 scripts/align_pairs.py <source.srt> <translation.srt> -o /tmp/review.md
```

Produces a side-by-side table of source and target text per block. Check
three things:

1. **Distortion / omission of meaning** — added information not in the
   source, a dropped clause, a flipped nuance
2. **Naturalness** — actual spoken language, not translation-ese
3. **Term preservation** — are proper nouns / gear / genre terms kept
   as-is?

**A grammatically complete sentence can still be a mistranslation.**
`flag_incomplete.py` only catches particle/ending truncation. The
following is the kind of error a machine can't catch and a reader can miss
without knowing the shoot itself:

- **Speaker/addressee direction** — a subject-dropping sentence like "yeah,
  should do that" is easy to translate backwards without a stated pronoun.
  When ambiguous, don't default to "you" or "I" — confirm who actually said
  it.
- **Whether an action is real, and its tense** — "actually going to play it
  live" could mean a live performance (not pre-recorded) or just pressing
  play, and that only resolves with context about what's being filmed.
- **Physical deixis** — "if you fall from here it's serious" where "here"
  might be an off-camera rooftop. Don't guess at a referent that's not
  visible on screen.
- **Idioms taken literally** — a phrase like "melting" used metaphorically
  (someone looking exhausted) vs. literally. Pin down who's being talked
  about before translating the idiom.
- **Venue names / proper nouns mistaken for common nouns** — a club or
  venue name that looks like an ordinary word; match the preposition to
  the convention ("at Venue" not "on Venue" or vice versa, depending on
  usage).

These five categories can look grammatically fine and still be a coin flip
without knowing the people, the blocking, and the industry context of the
shoot. The first four are usually resolved by the frames from ⓪ — pull that
timestamp again with `frames_at.py --crop left,right` and check mouth
movement, hand gesture, gaze. Only flag what the frames genuinely can't
resolve (knowledge not visible on screen — a song title, a quote's source,
industry slang's English equivalent) for the user — don't fabricate a
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
# check current tracks
python3 scripts/upload_caption.py --video <videoId> --list

# upload (updates if a track in that language already exists, else creates one)
python3 scripts/upload_caption.py --video <videoId> \
    --srt "<translation.srt>" --lang en --name "English" \
    --token youtube_token.json
```

`--draft` uploads as a private draft invisible to viewers, for review
before publishing from Studio. **Uploading changes public content — get
explicit approval before running this.**

### ⑤ Title localization — show a localized title only to that locale

If you've added a foreign-language caption track, localizing the title to
match is the natural next step. **Principle: a title suffix like "(ENG
SUB)" should only be visible to English-locale viewers.** Never touch the
default title — only add/replace `localizations.en`. Don't append a suffix
directly to the default title, or it becomes visible to every viewer
regardless of locale.

```bash
# inspect current state (default title / defaultLanguage / existing localizations)
python3 scripts/set_localization.py --video <videoId> \
    --token youtube_token.json --show

# set the English-locale title only
python3 scripts/set_localization.py --video <videoId> \
    --token youtube_token.json \
    --en-title "Shooting a DJ Set on Top of Seoul (ENG SUB)"
```

**The script never auto-appends a suffix like "(ENG SUB)".** Propose a
handful of title options that summarize the situation concisely, get the
final choice, and pass it verbatim (suffix included) to `--en-title`. Tone
varies per video, so this isn't automated.

**The `defaultLanguage` trap:** YouTube decides which locale sees the `en`
localization by the `localizations` map, not by `defaultLanguage`. A
channel can end up with `defaultLanguage=en` while its default title is
still in the original language — in that state, English-locale viewers see
the *original-language* title instead of the localization (the matching
gets inverted). By default `set_localization.py` corrects
`defaultLanguage` to the original spoken language (`ko` by default). If the
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
python3 scripts/extract_thumbnails.py grab \
    --source "https://youtu.be/<videoId>" \
    --timestamps "0:06,2:38,3:03,4:53,5:01,7:30,8:33" \
    --out /tmp/thumb_candidates

# 2) review each burst folder (sharpness, expression, number of elements in frame)
#    and copy the best pick from each into picks/ as "01_label.png"

# 3) bundle the picks into a base64-inlined HTML gallery for side-by-side review
python3 scripts/extract_thumbnails.py gallery \
    --picks-dir /tmp/thumb_candidates/picks \
    --out /tmp/thumb_candidates/gallery.html \
    --title "<video title> thumbnail candidates"
```

`grab` downloads the best available quality (1080p cap by default) via
yt-dlp when given a URL — don't reuse a lower-resolution copy you may have
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

```bash
cp /tmp/thumb_candidates/picks/01_hero.png ~/Downloads/<filename>.png
```

## Platform-native A/B testing is not exposed via API

YouTube Studio's built-in title/thumbnail A/B test feature has no public
YouTube Data API v3 endpoint — it's Studio-web-UI only, gated behind
Advanced features, and only configurable from a computer. `upload_caption.py`
/ `set_localization.py` automate captions and title localization; a
platform-native title/thumbnail experiment has to be set up manually in
Studio.

## Credentials

Each channel needs its own OAuth refresh-token file (created via
`reauth_channel.py`), while the OAuth client (`client_secret.json`,
downloaded from Google Cloud Console) can be shared across multiple
channels/projects — it's just an API calling-card, not a channel identity.
`reauth_channel.py` cross-checks the channel ID after auth, so pointing the
same client_secret at multiple channels is safe and won't silently
overwrite the wrong token.

```bash
python3 scripts/reauth_channel.py --token my_channel_token.json \
    --secret client_secret.json \
    --expect-channel UCxxxxxxxxxxxxxxxxxxxxxx
```

The "Choose account or brand account" step on the consent screen is the
one place a mistake sends the token to the wrong channel — that's exactly
what `--expect-channel` guards against.

**If the OAuth app is in "Testing" status, the refresh token expires after
7 days.** If re-authorizing that often gets tedious, publish the GCP OAuth
consent screen's Audience to "In production".

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
- The parser handles a BOM and CRLF line endings in the source SRT. Save
  your own files as UTF-8
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
