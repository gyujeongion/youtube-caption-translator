# AGENTS.md — youtube-caption-translator

This repo is a [Claude Code skill](https://claude.com/claude-code)
(`SKILL.md` with frontmatter) that Claude Code auto-discovers. Other
agents (Codex, etc.) don't read `SKILL.md` automatically — this file is
the bridge.

## When to use this

Any task involving: translating an editor-exported SRT (CapCut, Premiere)
into another language, making subtitles with speech-to-text when there is no
SRT, uploading a YouTube caption track, first-time setup of this skill
(Google Cloud, STT key, publish mode), localizing a video title per-locale,
or extracting thumbnail candidates from a video.

## What to do

Read **[SKILL.md](SKILL.md)** in full before starting. It is the actual
workflow spec:

- **Phase 0** — first-run setup wizard. Run `python3 scripts/check_setup.py`
  first, every session. If it exits non-zero, follow
  [references/setup-wizard.md](references/setup-wizard.md).
- **Ⓐ** — get a source SRT, **per video**: the editor SRT is always used
  first; `input_mode` in prefs (`srt` / `stt_api` / `stt_local`) only says
  what to do when a video has none. Free editor/YouTube auto-captions are
  suggested first (unverified by this project), then cloud STT, then local
  STT via `scripts/transcribe.py` (`--yes` to allow a model download,
  `--force` to overwrite the output, optional `--diarize` for multi-speaker
  videos on Deepgram or Soniox only; it writes a speakers sidecar file).
- **ⓞ Context pack** (part of step ⓪) — for the blocks whose speaker,
  addressee or "here/this" is ambiguous, run
  `scripts/context_pack.py build` to cut one contact sheet per block plus an
  `index.md` and a blank `speaker_map.json` (add `--speakers <stem>.speakers.json`
  if `--diarize` was used). Look at the sheets, fill the map, translate with
  it, and run `context_pack.py check --map ... --srt ...` **before step ②**; it
  exits 1 while any block is unresolved. Details:
  [references/context-pack.md](references/context-pack.md).
- **Steps ⓪ through ⑥** — the merge-before-translate rule, the
  mechanical/nuance verification gates, and the upload/localization CLI
  usage. Follow them as written; don't skip ⓪ (frame inspection) or ②
  (mechanical verification) even under time pressure — both exist because
  skipping them produces silently wrong captions.

Reference docs (open on demand):
[references/google-cloud-setup.md](references/google-cloud-setup.md),
[references/stt-cloud.md](references/stt-cloud.md),
[references/stt-local.md](references/stt-local.md).

Tests: `python -m unittest discover tests` and `python tests/smoke_help.py`
(runs `--help` of every script). CI runs both on Linux, Windows and macOS via
`.github/workflows/test.yml`.

The `scripts/` directory is plain Python (standard library only for the
core scripts; local STT engines are optional installs) — run any script
directly:

```bash
python3 scripts/verify_srt.py source.srt translation.srt
```

## Guardrails

- **Never ask the user to paste an API key, token or `client_secret.json`
  contents into chat.** Keys are entered only through
  `python3 scripts/check_setup.py --set-key <deepgram|openai|soniox>` (hidden
  prompt, private file). You run the other commands; the user answers
  questions, clicks in their own browser, and types keys into that prompt.
- **API keys are typed by the user in their own terminal** (the command
  refuses to run in an agent shell); exact Windows/macOS steps are in
  `references/setup-wizard.md`.
- **Which Python:** docs write `python3`; substitute yours. On **Windows try
  `py -3` first, then `python`; never `python3`** (it can be the Microsoft
  Store stub; `check_setup.py` prints a NOTE line when `python` is that
  shortcut). On macOS/Linux try `python3`, then `python`.
- **Windows shell rules:** use one-line commands (PowerShell continues with a
  backtick, cmd with `^`); quote paths with spaces; in PowerShell 5.1 never
  redirect script output with `>` (UTF-16), use `-o` / `--out`; source SRTs
  must be UTF-8 (UTF-8/UTF-16 with BOM accepted, cp949 refused). `--set-key`
  works only in PowerShell, cmd, Windows Terminal or a macOS/Linux Terminal,
  not Git Bash, PowerShell ISE, IDLE, IDE terminal panes or piped input.
- **Never install software silently.** If `transcribe.py` exits with code 3
  because a local runtime is missing, show the printed install command,
  explain it, and ask first.
- **Re-verify before recommending.** Prices, model names and console menu
  labels in `references/` are stamped "verified 2026-09-22" with a source URL.
  Open the URL and confirm before you recommend anything. Never name an STT
  model from memory. OpenAI: only `whisper-1` is supported (newer OpenAI
  speech models return no timestamps).
- Run `reauth_channel.py` in the background or with a 10-minute timeout;
  it waits up to 10 minutes for the browser login.
- Steps ④ (`upload_caption.py`) and ⑤ (`set_localization.py`) mutate
  public YouTube metadata. Respect `publish_mode` in the saved prefs:
  in `review` mode get explicit user approval before each upload; in
  `direct` mode the user's setup-time choice is the approval, but you must
  still stop if `verify_srt.py` fails or ambiguous blocks remain. A
  localized title (⑤) always needs the user's chosen wording.
- Show the quota estimate before a batch: a new caption track is about 450
  units (list 50 + insert 400), a replaced one about 500, and the default is
  about 10,000 units per day (about 22 new tracks per day).
- In `review` mode, make the first upload a private draft (`--draft`).
- Never commit OAuth tokens, `client_secret.json`, `ytcaption.env` or
  `ytcaption_prefs.json` — see `.gitignore`.
