# ytcaption

A [Claude Code](https://claude.com/claude-code) skill that translates
editor-exported SRT files (CapCut, Premiere, etc.) into foreign-language
YouTube caption tracks — and uploads them.

Editor-exported subtitles chop speech into short timecoded fragments. If
you translate those 1:1, sentences get cut in half mid-thought. **ytcaption
merges fragments into full sentences before translating**, re-lays the
timecodes on the original boundaries, and runs the result through both a
mechanical check and a human-nuance review pass before anything gets
uploaded. It can also localize the video title per-locale and pull
thumbnail candidates straight from the source video.

## What it does

```
video.mp4 ──⓪  identify speakers & context from the actual footage
source.srt ──①  merge fragments into sentences, translate
          ①.5 flag truncated/cut-off utterances
          ②   verify timecode boundaries mechanically
          ③   generate a source↔target review table
          ④   upload the caption track to YouTube
          ⑤   set a locale-specific video title ("(ENG SUB)" etc.)
          ⑥   extract & gallery thumbnail candidates
```

See [SKILL.md](SKILL.md) for the full workflow and the reasoning behind
each step — it's written as the actual instructions an AI agent follows,
not just a summary.

**Translation itself (step ①) is done by the AI agent reading SKILL.md's
instructions, not by a script calling a translation API.** The scripts
in this repo handle mechanical verification, upload, and localization —
they don't call any LLM or translation service themselves, so there's no
per-call cost or provider to configure beyond whatever agent you're
running this skill with.

**Steps ④ and ⑤ mutate public YouTube metadata (captions, title).** SKILL.md
instructs the agent to get explicit human approval before running them,
but that's workflow guidance for the agent, not a hard technical lock —
review the diff/translation yourself before approving an upload.

## Requirements

- Python 3.10+ (standard library only — no pip installs needed for the
  core scripts)
- `ffmpeg` / `ffprobe` (thumbnail and frame extraction)
- `yt-dlp` (only if extracting frames/thumbnails from a URL instead of a
  local file)
- `sips` for gallery JPEG resizing (macOS; swap for `ffmpeg -vf scale` on
  other platforms)
- A Google Cloud OAuth client with the YouTube Data API v3 enabled and the
  `youtube.force-ssl` scope, if you want to use the upload/localization
  scripts

## OAuth setup (for upload / localization)

Only needed for steps ④ and ⑤ — verification, review, and thumbnail
extraction work without any of this.

1. In [Google Cloud Console](https://console.cloud.google.com/), create a
   project and enable the **YouTube Data API v3**.
2. Under **APIs & Services → OAuth consent screen**, configure it (Testing
   is fine to start; refresh tokens expire after 7 days in Testing, so
   publish to Production if you don't want to re-auth weekly).
3. Under **Credentials**, create an **OAuth client ID** of type "Desktop
   app" and download the JSON as `client_secret.json`.
4. For each channel you want to caption, run:
   ```bash
   python3 scripts/reauth_channel.py --token <channel>_token.json \
       --secret client_secret.json --expect-channel <channel's UC... ID>
   ```
   This opens a browser consent screen and writes a per-channel refresh
   token. One `client_secret.json` can authorize multiple channels — the
   client is just an API calling-card, not a channel identity.
5. By default, token filenames given without a path resolve against
   `~/.claude/credentials/`. Pass an absolute path if you'd rather store
   them elsewhere. Either way, keep this directory out of any git repo
   (see `.gitignore`).

## Using it as a Claude Code skill

```bash
git clone https://github.com/<you>/ytcaption.git ~/.claude/skills/ytcaption
```

Claude Code picks it up automatically. Ask something like "translate this
SRT to English and upload it to this YouTube video" and it loads the
workflow in `SKILL.md`.

## Using it with other agents (Codex, etc.)

`SKILL.md`'s auto-loading is a Claude Code convention — other agents don't
pick it up on their own. [AGENTS.md](AGENTS.md) is a small bridge file
that points a non-Claude-Code agent (Codex CLI, etc.) at `SKILL.md` and
tells it to follow that workflow. The underlying scripts are plain
stdlib Python either way, so they work standalone regardless of which
agent (or no agent) is driving them.

## Using the scripts standalone

Each script under `scripts/` is a self-contained CLI and works outside
Claude Code too:

```bash
# authorize a channel (one-time, per channel)
python3 scripts/reauth_channel.py --token my_channel_token.json \
    --secret client_secret.json --expect-channel UCxxxxxxxxxxxxxxxxxxxxxx

# verify a translated SRT against the source before uploading
python3 scripts/verify_srt.py source.ko.srt translation.en.srt

# check for truncated source utterances before you even start translating
python3 scripts/flag_incomplete.py source.ko.srt

# generate a side-by-side review table
python3 scripts/align_pairs.py source.ko.srt translation.en.srt -o review.md

# upload / update the caption track
python3 scripts/upload_caption.py --video VIDEO_ID \
    --srt translation.en.srt --lang en --name English \
    --token my_channel_token.json

# set an English-only title
python3 scripts/set_localization.py --video VIDEO_ID \
    --token my_channel_token.json --en-title "Your Localized Title"

# pull thumbnail candidates from specific timestamps
python3 scripts/extract_thumbnails.py grab --source VIDEO.mp4 \
    --timestamps "0:06,2:38,3:03" --out /tmp/thumbs
python3 scripts/extract_thumbnails.py gallery \
    --picks-dir /tmp/thumbs/picks --out /tmp/thumbs/gallery.html
```

Token files default to resolving against `~/.claude/credentials/` if you
pass a bare filename, or use an absolute path.

## Why merge before translating

Take a source SRT chopped like this:

```
1) "My approach is,"
2) "since the album's called Horizon,"
3) "the point is to show"
```

Translated fragment-by-fragment, each piece reads like a sentence and
each one is wrong on its own. ytcaption reads the whole block first,
merges it into one sentence, and only then translates — with the merged
block's timecode boundaries still anchored to the original fragment
boundaries, so `verify_srt.py` can confirm nothing was invented.

## License

MIT — see [LICENSE](LICENSE).
