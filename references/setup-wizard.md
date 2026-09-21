# Setup wizard — exact script for the agent

> **Agent, read this first.**
> - **You run the commands.** The user answers questions, clicks in their own
>   browser (Google Cloud, provider sign-up pages), and types API keys into a
>   hidden prompt in their own terminal (see Question 2).
> - **Never ask the user to paste an API key, token, or `client_secret.json`
>   contents into chat.** Keys go through `check_setup.py --set-key`, which
>   shows a hidden prompt. If the user pastes one into chat anyway, tell them
>   it is now exposed, and that they should revoke it at the provider and
>   create a new one.
> - **Which Python?** The docs write `python3`; substitute yours. **On
>   Windows try `py -3 --version` first, then `python --version`; never
>   `python3`** (it can be the Microsoft Store stub; `check_setup.py` prints a
>   NOTE line when `python` is that shortcut). On macOS/Linux try `python3`,
>   then `python`. If none works, Python 3.10 or newer is missing: ask the
>   user before installing anything.
> - Commands in these docs are one-liners. PowerShell continues a line with a
>   backtick and cmd with `^`, so avoid multi-line forms. In PowerShell 5.1
>   never redirect script output with `>` (it writes UTF-16); use the scripts'
>   own `-o` / `--out` options. Paths with spaces need quotes; `%USERPROFILE%`
>   is accepted in script paths.
> - Words the user may not know (OAuth, quota unit, SRT...) are explained in
>   the [glossary](#glossary) at the end of this file.
> - Prices, model names and menu labels in the other reference files are
>   dated **2026-09-22**. Re-verify each against its source URL before you
>   recommend it.
> - Ask **one question at a time**. Use AskUserQuestion when your tool has
>   it (2-4 options, recommended option first); otherwise ask in plain chat.
> - Save each answer immediately with `--set-pref`, so a stopped session can
>   resume.

## Step 0 — Look at the current state

```bash
python3 scripts/check_setup.py
```

Read the checklist aloud in one or two sentences (what is ready, what is
missing). Its markers are `[OK     ]`, `[MISSING]` and `[optional]`. It prints
`Input: SRT always works; fallback when there is no SRT: <mode|none>
(ready|not ready)` and ends with a `NEXT STEP:` line. If it exits 0, setup is
done: tell the user "you are ready" and continue with step Ⓐ in `SKILL.md`.

If `ffmpeg` shows `[MISSING]`: on Windows install it with `choco install
ffmpeg`, or download it from <https://ffmpeg.org/download.html> (the winget
package id is not verified, so do not guess one), then **open a NEW terminal**
so PATH updates. On macOS `brew install ffmpeg`.

Also useful: `python3 scripts/check_setup.py --show-prefs` shows saved answers.
`check_setup.py --json` gives the same checks in machine-readable form.

## Question 1 — Languages

Ask: "What language do you speak in your videos, and which languages should
the subtitles be translated into?"

- Explain why it matters: the spoken language tells the speech recognizer (if
  needed) which language to expect, and it is used as the video's default
  language when localizing titles.
- Recommend: start with **one** target language (usually English). Each extra
  language costs quota on every video (400 units per track, see
  `google-cloud-setup.md`), and you can add more later.

Store (use two-letter language codes such as `ko`, `en`, `ja`, `es`):

```bash
python3 scripts/check_setup.py --set-pref source_language=ko target_languages=en
```

If the user names several targets, separate them with commas, for example
`target_languages=en,ja`. (The comma format follows the script's current
validator; if it rejects the value, run `check_setup.py --help`.)

## Question 2 — What should happen when a video has no SRT?

Explain first: "For **every** video, if you already have an SRT from your
editor, I use that. Always. This question is only about what I do for a video
that **has no** SRT." Mixed batches are normal. Example: you give me 4 videos,
1 has an SRT and 3 do not. I use the SRT for the first one and the answer
below for the other three.

Ask: "For videos without an SRT, how should I get the words?"

| Answer (`input_mode`) | In plain words | Recommendation |
|---|---|---|
| **`srt`**: SRT only | I never transcribe. If a video has no SRT, I stop and ask you to export one from your editor. | Choose this if you always have an SRT. It costs nothing and gives the best timing. |
| **`stt_api`**: cloud speech-to-text (Deepgram, OpenAI whisper-1, Soniox) | A paid online service listens to the video and writes the SRT. Cheap (list prices in `stt-cloud.md`, verified 2026-09-22, are under 10 US cents for a 15-minute video), but you need an account and a key. | **Recommended for videos without an SRT**, especially on a small or old computer. Deepgram first, because its free credit needs no card. |
| **`stt_local`**: local speech-to-text | Your own computer listens and writes the SRT. Free, and the audio never leaves your machine. Slower, and small computers give worse results. | Choose this if you do not want an API account, or want the audio to stay on your machine, **and** your computer is strong enough. |

Before offering the two paid or heavy options, mention the free ones for a
missing SRT, labelled **not verified by this project (check your editor)**:
the editor's own auto-caption export (for example CapCut auto captions, then
export as SRT), and downloading the video's auto-generated captions from
YouTube Studio (the video must already be uploaded).

**Decision rule for local vs cloud:** run `python3 scripts/detect_hardware.py`
(`--json` shows `recommended.prefer_cloud` and `prefer_cloud_reason`). If
`prefer_cloud` is true, **recommend cloud STT (Deepgram first) over local**, and
say plainly: "Korean quality on small local models is unverified and likely
poor on this computer."

Store:

```bash
python3 scripts/check_setup.py --set-pref input_mode=srt
# or: input_mode=stt_api stt_engine=deepgram      (deepgram | openai | soniox)
# or: input_mode=stt_local stt_engine=local
```

What to do after the answer:

- **`srt`** — nothing else to set up for this step.
- **`stt_api`** — open `references/stt-cloud.md`. Re-verify the chosen
  provider on its source URL and walk the user through creating an account
  and key. Then the user enters the key themselves (next section).
- **`stt_local`** — tell the user in plain words what the computer can handle,
  then open `references/stt-local.md` and follow its "re-verify the best model
  today" recipe. If a runtime is missing, `transcribe.py` prints the exact
  install command and stops (exit code 3). **Show the command and ask before
  running it.** Never install silently. A first-run model download needs the
  user's OK too (`transcribe.py --dry-run` says "will download ~N MB"; re-run
  with `--yes` only after they agree).

### Entering an API key (the user does this, not you)

The command needs a real keyboard prompt and an agent's shell has none, so it
refuses to run there (exit code 2). Say: "Open a terminal, go to this folder,
and run this. A hidden prompt will appear. Paste your key there, **not in this
chat**. Nothing is shown on screen; that is normal."

**Windows (exact steps):**

1. Open **PowerShell**, cmd or Windows Terminal (Start menu, type
   PowerShell). Not Git Bash.
2. Go to the skill folder (adjust if your agent keeps skills somewhere else):
   ```powershell
   cd $env:USERPROFILE\.claude\skills\youtube-caption-translator
   ```
3. Run:
   ```powershell
   py -3 scripts\check_setup.py --set-key deepgram
   ```
   (`deepgram` can be `openai` or `soniox`. If `py` is not found, use
   `python scripts\check_setup.py --set-key deepgram`. Do not use `python3`.)

**macOS / Linux:**

```bash
cd ~/.claude/skills/youtube-caption-translator
python3 scripts/check_setup.py --set-key deepgram
```

**Which terminals work.** Supported: PowerShell, cmd, Windows Terminal, and the
macOS / Linux Terminal. **Not supported** (no hidden prompt is possible, so the
command stops): Git Bash / mintty, PowerShell ISE, IDLE, the terminal pane
inside an IDE, and piped input. If the key contains non-English characters
(for example a Korean input method pasted it in full-width form), the key is
refused: switch the keyboard to English input and paste again.

Afterwards run `python3 scripts/check_setup.py` yourself and confirm the key
shows as present (it only reports present or missing).

Two safety notes for the user:

- On Windows the saved key file is not permission-restricted (macOS and Linux
  use `chmod 600`). Keep the config folder in your user profile folder.
- If `YTCAPTION_HOME` points into a Dropbox, Google Drive or OneDrive folder,
  your keys sync to that cloud. Do not do that.

## Question 2b — Speaker labels (ask only for multi-speaker videos)

Ask this **only** if the user's videos have more than one speaker
(interviews, podcasts, two-person vlogs). Skip it for solo videos.

Explain in plain words: "'Who spoke' can be worked out from the audio. If you
switch it on, the speech-to-text step also saves a small file saying which
speaker talked when. Later, when I look at the video to work out who is
talking to whom, that file helps me decide." It is **optional and off by
default**, and it only works for videos that go through speech-to-text (not
for an editor SRT).

Facts to state honestly:

- Only **Deepgram** and **Soniox** support it. OpenAI `whisper-1` and local
  engines do not (`transcribe.py --diarize` exits with code 2 for them).
- **Deepgram** charges for diarization as a paid add-on. This project has
  **not verified** the price: check <https://deepgram.com/pricing> before
  recommending it. The cost estimate `transcribe.py` prints does **not**
  include it.
- **Soniox** includes diarization at no extra cost, according to its pricing
  page (verified 2026-09-22, <https://soniox.com/pricing>).
- The SRT itself gets no speaker names or prefixes, and a subtitle block never
  mixes two speakers. The speaker labels (`S1`, `S2`, ...) go into a separate
  file, `<out stem>.speakers.json`.

How it is used: `python3 scripts/transcribe.py ... --diarize` writes the file,
then `python3 scripts/context_pack.py build ... --speakers <out stem>.speakers.json`
(see `references/context-pack.md`). There is no `--set-pref` for it; ask per
run.

## Question 3 — Publish mode

Ask: "Should I stop and show you each translation before it goes live, or
publish automatically once it passes the checks?"

| Option | What happens | Recommendation |
|---|---|---|
| **review** | I translate, run the mechanical checks, show you a side-by-side table, and upload **only after you say yes** for that video. | **Recommended for your first videos.** Machine checks catch timing errors, but only a human catches a translation that reads fine and means the wrong thing. |
| **direct** | I translate, run the mechanical checks (they must pass), then upload right away. No per-video approval. | Choose this only once you have seen the review-mode results a few times and trust them. Your choice here counts as your approval. |

Be honest about `direct`: it still **stops** if the checks fail or if some
lines are ambiguous and the video frames cannot settle them, but it will not
ask you to read the translation before it is public. Captions can be edited
or deleted afterwards, but viewers may see a mistake in the meantime.

Store:

```bash
python3 scripts/check_setup.py --set-pref publish_mode=review
```

Only set `direct` if the user explicitly asks for it. The default
recommendation is `review`. The user can change this later with the same
command.

Also tell them, for `review` mode: the **first upload should be a private
draft** (`upload_caption.py ... --draft`). Why: captions are public once
published, and replacing a track later costs about 450-500 quota units. They
check the draft in YouTube Studio and publish it there.

## Question 4 — Which YouTube channel?

Ask: "Which YouTube channel should the subtitles be added to? If you manage
more than one channel (for example a personal one and a brand one), tell me
which."

- Explain why it matters: the login step in the browser has an account
  picker. Picking the wrong account or channel attaches the permission to the
  wrong channel. Use the account that **owns** the videos.
- Ask them to have the channel's ID handy if they know it (it starts with
  `UC`). If they do not, that is fine: after login, `check_setup.py` shows the
  channel title and ID so they can confirm it is the right one.
- Not confirmed on an official Google page: exactly how the account and
  brand-channel picker behaves at the consent step. If it looks different
  from what you expect, describe what you see and let the user decide.

Choose file names for this channel (defaults are fine for one channel; for
several channels give each its own token file):

```bash
python3 scripts/check_setup.py --set-pref token_file=my_channel_token.json client_secret=client_secret.json
```

## After the four questions — get a working YouTube token

Run `python3 scripts/check_setup.py`. If the token line is missing or
invalid, open `references/google-cloud-setup.md` and walk the user through it
**one numbered step at a time**, asking them to say "done" after each. The
end result is a token file that `check_setup.py` can use to look up the
channel (`channels.list`, `mine=true`) and print its title and ID.

Tell the user up front what to expect, so nothing scares them:

- Plan for a focused first session; it has several console steps.
- We have not confirmed whether Google asks for a payment card at any point.
  If a screen asks for one, stop and tell the user what it says; do not
  guess or enter card details for them.
- Their browser will show a warning that the app is "unverified". That is
  expected for a personal app; the steps explain how to continue.
- They will be asked to download a file **once**, and it cannot be
  downloaded again later.

## Final check

```bash
python3 scripts/check_setup.py
```

Expect the checklist (plain-text markers `[OK     ]` / `[MISSING]` /
`[optional]`) to show `[OK     ]` for:

- Python 3.10 or newer
- ffmpeg and ffprobe
- client secret file found
- YouTube token valid (channel title and ID printed)
- the STT fallback, **only if** `input_mode` is `stt_api` or `stt_local`
  (a saved key, or a local runtime). With `input_mode=srt` nothing is needed.

It prints `Input: SRT always works; fallback when there is no SRT: <mode|none>
(ready|not ready)` and ends with a `NEXT STEP:` line. Exit code 0 means the
wizard is finished: the token is valid and the chosen fallback is ready.
Summarize back to the user in four short lines: languages, what happens when
there is no SRT, publish mode, channel. Then continue with step Ⓐ in
`SKILL.md`.

## If the user wants to change something later

Re-run the relevant `--set-pref` command. Do not repeat the whole wizard.
To rotate an STT key: create a new key at the provider, run
`check_setup.py --set-key <provider>` again (it replaces the old one), then
revoke the old key at the provider.

## Glossary

Short answers for a non-technical YouTuber. Use these when a word comes up.

- **SRT** — a plain text subtitle file. Each block has a number, a start and
  end time, and the words shown in that time. CapCut, Premiere and most
  editors can export one.
- **STT (speech-to-text)** — software that listens to your video and writes
  the words down with times. It makes a draft that you should read.
- **API key** — a long secret password a company gives you so a program can
  use its service on your account. Anyone who has it can spend your money, so
  it never goes into chat.
- **Runtime** — the program that actually runs a speech model on your own
  computer (for example whisper.cpp). "Missing runtime" means it is not
  installed yet.
- **VAD (voice activity detection)** — a filter that skips silence and music
  so a local model does not invent words there.
- **Google Cloud project** — a folder in Google's developer console that holds
  your private "app" and its quota. You make your own once.
- **OAuth** — Google's way to let a program act on your YouTube channel
  without knowing your password. You log in once in the browser and click
  Allow.
- **Consent screen** — the Google page that says "this app wants permission
  to do X". You fill in its name and contact email during setup.
- **Scope** — one specific permission on that screen. This tool asks for one:
  managing your YouTube videos' captions.
- **Client secret file** — the app's ID card (`client_secret.json`). Google
  lets you download it only once, when you create it.
- **Refresh token** — the saved permission (the token file) that lets the tool
  upload later without asking you to log in again. Guard it like a password.
- **Quota unit** — YouTube's daily allowance for programs. Each action costs
  units; adding one caption track costs a few hundred. The default is about
  10,000 per day and it resets at midnight Pacific Time.
