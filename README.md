# youtube-caption-translator

유튜브 다국어 자막 자동화 스킬

[한국어 README](README.ko.md)

An AI-agent skill that turns your video's speech into foreign-language
YouTube caption tracks, and uploads them. Works with
[Claude Code](https://claude.com/claude-code) and other coding agents. Built
for YouTubers, not developers: your agent walks you through setup in plain
language and runs the commands for you.

Editor-exported subtitles chop speech into short timecoded fragments. If you
translate those 1:1, sentences get cut in half mid-thought. **This skill
merges fragments into full sentences before translating**, re-lays the
timecodes on the original boundaries, and runs the result through a
mechanical check and a human-nuance review before anything is uploaded.

## Where the source subtitles come from (decided per video)

```
                     ┌── has an SRT from your editor ──────────── always used first (best timing, free)
each of your videos ─┤
                     └── has no SRT ── free: editor auto-captions or YouTube auto-captions (not verified by us)
                                       ── or cloud speech-to-text (Deepgram / OpenAI whisper-1 / Soniox)
                                       ── or local speech-to-text (free, runs on your computer)
                                              │
                                              ▼
              translate as full sentences ─▶ mechanical check ─▶ review table ─▶ upload to YouTube
                                                                  (or "direct" mode: upload after checks pass)
```

Mixed batches are normal: for example one video with an SRT and three without.

## Quick start

1. Get the files into your agent's skills folder. **Option A: git**

   macOS / Linux (Claude Code):

   ```bash
   git clone https://github.com/gyujeongion/youtube-caption-translator.git ~/.claude/skills/youtube-caption-translator
   ```

   Windows (PowerShell, Claude Code):

   ```powershell
   git clone https://github.com/gyujeongion/youtube-caption-translator.git "$env:USERPROFILE\.claude\skills\youtube-caption-translator"
   ```

   **Option B: no git.** On the GitHub page click **Code > Download ZIP**,
   unzip it, and put the folder at
   `%USERPROFILE%\.claude\skills\youtube-caption-translator` (Windows) or
   `~/.claude/skills/youtube-caption-translator` (macOS / Linux). The folder
   must contain `SKILL.md` directly.

   Other agents (Codex and others) keep skills in their own folder; use that
   agent's skills directory instead, or see [AGENTS.md](AGENTS.md).

2. Tell your agent:

   > set up youtube-caption-translator

3. Answer its four questions. It does the rest and tells you when to click
   something in your browser.

New to the words (OAuth, quota unit, SRT...)? See the
[glossary](references/setup-wizard.md#glossary).

## What the setup wizard does

The agent runs a checklist (`scripts/check_setup.py`), then asks:

1. **Languages** — what you speak, and which languages to translate into.
2. **What to do when a video has no SRT** — nothing (SRT only), cloud
   speech-to-text, or local speech-to-text. An editor SRT is always used
   first, per video.
3. **Publish mode** — `review` (you approve each upload; recommended for the
   first videos) or `direct` (uploads right after the checks pass; your
   setup choice is the approval).
4. **YouTube channel** — which channel gets the captions.

Then it walks you through Google Cloud (create a project, turn on the YouTube
Data API, set up the consent screen, create a Desktop OAuth client, publish
the app, log in) until a working token exists, and helps you get a
speech-to-text key or pick a local model if you need one. Details:
[references/setup-wizard.md](references/setup-wizard.md).

## Honest limits

- **Daily quota.** YouTube gives a new Google Cloud project 10,000 units per
  day by default. Adding a caption track costs 400 units, and the tool checks
  existing tracks first (50), so a new track is about 450 and a replaced one
  about 500. That is about **22 new tracks per day**. Example: 4 videos x 2
  languages = 8 tracks, about 3,600 units, fits in one day. The agent shows
  an estimate before every batch. The quota resets at midnight Pacific Time
  (approximately 16:00-17:00 in Korea). Numbers verified against Google's
  docs on 2026-09-22.
- **Google login has friction.** You create your own private Google Cloud app.
  Google shows an "unverified app" warning for it; that is expected for
  personal use. The client secret can be downloaded **only once**, at
  creation. Some screen labels could not be confirmed on official pages, so
  the walkthrough says "if your screen differs...". See
  [references/google-cloud-setup.md](references/google-cloud-setup.md).
- **Tokens may still expire.** Publishing the app removes the 7-day expiry
  that applies to apps in Testing, but Google does not explicitly guarantee
  tokens last forever for an unverified app. If uploads start failing with
  `invalid_grant`, run the login step again.
- **Speech-to-text accuracy is not benchmarked here.** There is no verified
  winner among the providers for Korean. Try two engines on one short video
  and compare. The output is a draft to read, not a final transcript.
- **OpenAI: only `whisper-1`.** OpenAI's newest speech models return no
  timestamps, so they cannot make subtitles.
- **Soniox has no free credits** (they ended 2025-10-27); Deepgram gives a
  $200 credit with no card. Deepgram's mixed-language mode does not include
  Korean.
- **Local models on small computers can be poor**, especially for Korean on
  4 GB of memory.
- **Prices and model names change.** Reference docs are stamped
  "verified 2026-09-22" and your agent is told to re-check the source URL
  before recommending anything.
- Translation is done by your AI agent following [SKILL.md](SKILL.md). It can
  still be wrong on subject-dropping sentences and on unseen context; that is
  why review mode exists.

## Credential safety

- **Never paste an API key into your agent chat.** Your agent will give you
  one command, `check_setup.py --set-key <provider>`, to run in your own
  terminal (on Windows: PowerShell, not Git Bash). It shows a hidden prompt
  and saves the key to a private file (permissions 600 on macOS and Linux;
  Windows does not restrict the file, so keep it in your user profile
  folder). Type or paste the key only there.
- Do not put the config folder inside a Dropbox, Google Drive or OneDrive
  folder: your keys would sync to that cloud.
- Keys, tokens and `client_secret.json` live in your config folder
  (`$YTCAPTION_HOME` if set, else `~/.claude/credentials/`; on Windows
  `%USERPROFILE%\.claude\credentials\`), outside any git
  repo, and are covered by `.gitignore`. Never commit them.
- The YouTube permission this tool asks for lets it edit and delete your
  videos' captions and more (Google's wording: "See, edit, and permanently
  delete your YouTube videos, ratings, comments and captions"). Guard the
  token file like a password.
- Uploads change public content. In `review` mode nothing is uploaded until
  you say yes.

## Requirements

- Python 3.10+ (standard library only for the core scripts; no pip installs).
  The docs write `python3`; **on Windows use `py -3` (or `python`), never
  `python3`**.
- `ffmpeg` / `ffprobe`. On Windows install with `choco install ffmpeg` or
  download from <https://ffmpeg.org/download.html>, then **open a NEW
  terminal** so PATH updates. (The winget package id is unverified, so none is
  named here.)
- `yt-dlp` (optional; only to pull thumbnails from a URL, or to download a
  video before extracting frames, which needs a local file)
- A Google account that owns the channel; a Google Cloud project you create
  during setup
- Optional: a speech-to-text API key, or a local runtime (whisper.cpp,
  mlx-whisper or faster-whisper). Local runtimes are never installed without
  asking you.
- Works on macOS, Windows and Linux.

## Using it with other agents

`SKILL.md`'s auto-loading is a Claude Code convention. [AGENTS.md](AGENTS.md)
is a small bridge file that points other agents (Codex CLI, etc.) at
`SKILL.md`. Those agents use their own skills folder. The scripts are plain
Python, so they also work on their own.

## Using the scripts standalone

Commands use `python3`; on Windows use `py -3` (or `python`), never `python3`,
and `scripts\` paths work in PowerShell too. `<working folder>` is any folder
you can write to; folders you name after `-o` or `--out` must already exist.
Commands are one-liners (PowerShell continues a line with a backtick, cmd with
`^`). **Paths with spaces need quotes**; the scripts accept quoted paths and
`%USERPROFILE%`. **In PowerShell 5.1 do not redirect script output with `>`**
(it writes UTF-16): use the scripts' own `-o` / `--out` options. Input SRT
files must be UTF-8 (UTF-8 with BOM and UTF-16 with BOM are accepted; cp949 /
ANSI files are refused with an instruction. In Notepad: File > Save As >
Encoding: UTF-8).

```bash
# preflight checklist, and the safe way to store a key
python3 scripts/check_setup.py
python3 scripts/check_setup.py --set-key deepgram
python3 scripts/check_setup.py --set-pref publish_mode=review
python3 scripts/check_setup.py --show-prefs

# what can this computer run for local speech-to-text?
python3 scripts/detect_hardware.py --json

# no SRT? make one (engine: deepgram | openai | soniox | local)
python3 scripts/transcribe.py --input video.mp4 --engine deepgram --lang ko --out source.ko.srt --dry-run
# multi-speaker video? add --diarize (Deepgram or Soniox only; writes source.ko.speakers.json; Deepgram's diarization is a paid add-on we have not priced)
# add --yes to allow a first-run local model download, --force to overwrite --out

# authorize a channel (one-time, per channel)
python3 scripts/reauth_channel.py --token my_channel_token.json --secret client_secret.json --expect-channel UCxxxxxxxxxxxxxxxxxxxxxx

# verify a translated SRT against the source before uploading
python3 scripts/verify_srt.py source.ko.srt translation.en.srt

# check for truncated source utterances before translating (Korean sources only)
python3 scripts/flag_incomplete.py source.ko.srt

# context pack for ambiguous speaker/addressee blocks: build, fill speaker_map.json, then check
python3 scripts/context_pack.py build --video video.mp4 --srt source.ko.srt --out "<working folder>/pack" --auto --crops left,right
python3 scripts/context_pack.py check --map "<working folder>/pack/speaker_map.json" --srt source.ko.srt

# side-by-side review table
python3 scripts/align_pairs.py source.ko.srt translation.en.srt -o "<working folder>/review.md"

# upload / update the caption track
python3 scripts/upload_caption.py --video VIDEO_ID --srt translation.en.srt --lang en --name English --draft
# --token is optional: defaults to prefs token_file, else my_channel_token.json

# set locale-specific titles (repeatable; --en-title X = --title "en=X")
python3 scripts/set_localization.py --video VIDEO_ID --title "en=Your English Title" --title "ja=あなたの日本語タイトル"

# pull thumbnail candidates
python3 scripts/extract_thumbnails.py grab --source VIDEO.mp4 --timestamps "0:06,2:38,3:03" --out "<working folder>/thumbs"
python3 scripts/extract_thumbnails.py gallery --picks-dir "<working folder>/thumbs/picks" --out "<working folder>/thumbs/gallery.html"
```

Token files given as a bare filename resolve against the config folder;
you can also pass an absolute path.

## Why merge before translating

Take a source SRT chopped like this:

```
1) "My approach is,"
2) "since this recipe is called Bibimbap,"
3) "the point is to show"
```

Translated fragment by fragment, each piece reads like a sentence and each
one is wrong on its own. This skill reads the whole block first, merges it
into one sentence, and only then translates, with the merged block's
timecodes still anchored to the original fragment boundaries, so
`verify_srt.py` can confirm nothing was invented.

## Contents

| Path | What it is |
|---|---|
| [SKILL.md](SKILL.md) | The workflow the agent follows: Phase 0 setup, step Ⓐ (get a source SRT), steps ⓪ to ⑥ |
| [AGENTS.md](AGENTS.md) | Bridge for agents that do not auto-load skills |
| [references/setup-wizard.md](references/setup-wizard.md) | The exact first-run question script |
| [references/google-cloud-setup.md](references/google-cloud-setup.md) | Google Cloud walkthrough, quota, troubleshooting |
| [references/stt-cloud.md](references/stt-cloud.md) | Cloud speech-to-text providers, keys, costs |
| [references/stt-local.md](references/stt-local.md) | Local speech-to-text tiers, runtimes, pitfalls |
| [references/context-pack.md](references/context-pack.md) | Context pack: contact sheets, speaker map schema, scoring, limits |
| `scripts/check_setup.py` | Preflight checklist, safe key entry, preferences |
| `scripts/detect_hardware.py` | Hardware summary and local-model recommendation |
| `scripts/transcribe.py` | Speech-to-text to SRT (cloud or local); optional `--diarize` speaker sidecar (Deepgram, Soniox) |
| `scripts/frames_at.py` | Frames from the video to identify speakers and context |
| `scripts/context_pack.py` | Contact sheets and a speaker-map form for the ambiguous blocks; `check` lists what is unresolved |
| `scripts/flag_incomplete.py` | Find truncated utterances in the source SRT |
| `scripts/verify_srt.py` | Mechanical timecode and line-length verification |
| `scripts/align_pairs.py` | Source/target review table |
| `scripts/reauth_channel.py` | Authorize a channel and save its token |
| `scripts/upload_caption.py` | Create or update a caption track |
| `scripts/set_localization.py` | Locale-specific video title |
| `scripts/extract_thumbnails.py` | Thumbnail candidate frames and gallery |

## License

MIT. See [LICENSE](LICENSE).
