# Cloud speech-to-text (STT) — when you have no SRT

> **Agent: re-verify before you recommend.** Every price, model name and
> menu label below was read from official provider pages on **2026-09-22**
> (source URL given per item). Providers change these often, and newer models
> appear. Before recommending a provider, open its pricing and docs URLs and
> confirm the price, the model name and, most importantly, that the model still
> returns **timestamps**. Never name a model from memory. If the page now says
> something different, tell the user and use what the page says.
>
> Anything marked **UNVERIFIED** or "not confirmed" was not confirmed on an
> official page. Some pages were read through a summarizing tool, so exact
> numbers deserve a second look at the URL.
>
> Keys are entered with `python3 scripts/check_setup.py --set-key <provider>`
> (hidden prompt) **run by the user in their own terminal**; exact Windows and
> macOS steps are in `setup-wizard.md`, section "Entering an API key".
> Supported terminals: PowerShell, cmd, Windows Terminal, macOS/Linux Terminal.
> Not supported: Git Bash/mintty, PowerShell ISE, IDLE, IDE terminal panes,
> piped input. A non-English (for example full-width Korean-IME) key is
> refused; switch to English input and paste again.
> **Never ask the user to paste a key into chat.**

## The honest picture first

- **There is no verified accuracy winner for Korean.** We found no
  independent Korean benchmark from official sources. Vendor "most accurate"
  claims are marketing. What we can compare is documented features (does it
  return timestamps, does it support your language, what does it cost).
- **The best way to choose is a 5-minute A/B test on one of your own
  videos.** Run two engines on the same short clip and compare the two SRT
  files by eye (or with a diff). A 10-minute test costs about 2 to 6 US cents
  at the list prices below.
- STT output is always a **draft**. Read it before translating; names and
  product terms are the usual mistakes. The `--prompt` option lets you list
  proper nouns.

## Provider matrix (prices verified 2026-09-22)

| Provider (model) | List price per hour | 15-min video | Free credit for new users | Timestamps | Card needed |
|---|---|---|---|---|---|
| **Deepgram** (`nova-3`) | $0.258 | about $0.065 | **$200, no card required** | Yes: word and utterance level | No for the free credit |
| **Soniox** (`stt-async-v5`) | about $0.10 | about $0.025 | **None** (ended 2025-10-27) | Yes: sub-word tokens (script builds the lines) | Not stated; expect yes |
| **OpenAI** (`whisper-1` only) | $0.36 | $0.09 | None documented | Yes: segments and words | Yes: prepaid purchase, minimum $5 |

Sources (verified 2026-09-22): Deepgram <https://deepgram.com/pricing> and
<https://developers.deepgram.com/docs/models-languages-overview>; Soniox
<https://soniox.com/pricing> and
<https://soniox.com/blog/2025-10-27-free-credits-update-for-soniox-api>; OpenAI
<https://developers.openai.com/api/docs/models/whisper-1> and
<https://developers.openai.com/api/docs/pricing>.

## One recommendation per situation

- **You want to start with no payment at all:** Deepgram `nova-3`. The $200
  free credit needs no card and is about 775 hours of audio at list price (our arithmetic). One
  request returns text with timings, so it is also the simplest to run.
  Known unknown: `language=multi` (mixed languages) does **not** include
  Korean, so Korean speech with English words is transcribed as Korean only;
  test it on your video.
- **You want the cheapest per hour and are fine adding money first:** Soniox.
  It is about 2.6 times cheaper than Deepgram at list price, it says it
  handles switching languages mid-sentence, and audio is not used for
  training. Cost: no free credits, and the script has to rebuild lines from
  small text pieces.
- **You already have an OpenAI API key:** OpenAI `whisper-1`. It is the only
  OpenAI speech model that gives timestamps (see below).
- **You want to know which is most accurate for your voice:** run the A/B test
  above. Best documented fit for Korean plus English terms is Soniox (explicit
  mixed-language support and Korean listed), but that is documented capability,
  not measured accuracy.

## Deepgram

- **Model and language:** `model=nova-3&language=ko`. Always pass both;
  omitting `model` falls back to an older default and omitting `language`
  assumes English. Korean is supported on `nova-3`, `nova-2`, `enhanced`,
  `base`.
  Source (verified 2026-09-22): <https://developers.deepgram.com/docs/models-languages-overview>
- **What the script calls:** one synchronous `POST https://api.deepgram.com/v1/listen`
  with `smart_format=true&utterances=true&punctuate=true&mip_opt_out=true`,
  header `Authorization: Token <key>`, and reads `results.utterances[]`
  (start, end, transcript).
  Sources: <https://developers.deepgram.com/docs/pre-recorded-audio>,
  <https://developers.deepgram.com/docs/utterances>
- **Limits:** max file 2 GB; a 10-20 minute video is far below the limit.
  Requests taking over 10 minutes to process can time out (504).
  Source: <https://developers.deepgram.com/docs/pre-recorded-audio>
- **Getting a key (steps, verified 2026-09-22):**
  1. Sign up at <https://console.deepgram.com> (no card needed for the free
     credit).
  2. Pick your project (Projects dropdown, top left).
  3. Settings > **API Keys** > **Create a New API Key**.
  4. Give it a name, keep the default permissions, choose an expiration.
  5. **Copy the key immediately; it is shown only once.** Then put it in the
     tool with `check_setup.py --set-key deepgram` (not in chat).
  Source: <https://developers.deepgram.com/docs/create-additional-api-keys>.
  If your screen differs, look for an API Keys page in Settings. Where the
  remaining free-credit balance is shown in the UI: UNVERIFIED (usage is
  under the **Usage** tab).
- **Privacy:** the script always sends `mip_opt_out=true`, which excludes the
  request from Deepgram's model-improvement program; Deepgram says opted-out
  data is kept only as long as needed to process the request. Whether the
  list price assumes participation in that program is not confirmed on
  official pages.
  Source: <https://developers.deepgram.com/docs/the-deepgram-model-improvement-partnership-program>
- **Not confirmed:** how well `language=ko` handles embedded English terms;
  whether the term-boosting option (`keyterm`) works for Korean.

## OpenAI — `whisper-1` only

> **Important:** the newest OpenAI speech models (`gpt-transcribe`,
> `gpt-4o-transcribe`, `gpt-4o-mini-transcribe`) return **only plain text with
> no timestamps**. A subtitle file needs timestamps, so this tool supports
> **only `whisper-1`**. If you pass another OpenAI model, `transcribe.py`
> exits with a clear message. Re-check this at
> <https://developers.openai.com/api/docs/guides/speech-to-text>; if a newer
> model starts returning timestamps, that is a change to make in the tool, not
> a reason to ignore the limit today.
> (Verified 2026-09-22; the docs say timestamp options are exclusive to
> `whisper-1`.)

- **What the script calls:** `POST https://api.openai.com/v1/audio/transcriptions`,
  `model=whisper-1`, `response_format=verbose_json`, reads `segments[]`
  (start, end, text). Header `Authorization: Bearer <key>`.
  Source: <https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create>
- **File size:** 25 MB per file. The script compresses audio to small mono
  mp3 first and splits and re-times long files. Formats include mp3, mp4,
  mpeg, mpga, m4a, wav, webm.
  Source: <https://developers.openai.com/api/docs/guides/speech-to-text>
- **Getting a key (verified 2026-09-22):**
  1. Sign up at <https://platform.openai.com>. This is separate from a
     ChatGPT subscription.
  2. Add billing: Billing > **Buy credits**, minimum $5. A card is required.
     Credits expire after 1 year and are non-refundable.
  3. Open <https://platform.openai.com/api-keys> > **Create new secret key**.
  4. Copy it right away (shown once), then use
     `check_setup.py --set-key openai`.
  Sources: <https://help.openai.com/en/articles/8264644-how-can-i-set-up-prepaid-billing>,
  <https://help.openai.com/en/articles/4936850-where-do-i-find-my-openai-api-key>
  (the second page could not be opened directly during research; menu labels
  UNVERIFIED, so if your screen differs, look for API keys under the platform
  settings).
- **Privacy:** API data is not used to train models by default (unless you
  opt in); inputs and outputs may be kept up to 30 days for abuse monitoring.
  Source: <https://developers.openai.com/api/docs/guides/your-data>
- **Not confirmed:** how `whisper-1` treats mixed-language speech.

## Soniox

- **Model:** `stt-async-v5` (file transcription).
  Source (verified 2026-09-22): <https://soniox.com/docs/stt/models>
- **What the script does (4 steps, all with header `Authorization: Bearer <key>`):**
  upload the file, create the transcription with
  `language_hints` set to your language plus English, wait until it is
  completed, download the transcript. Then it **always deletes** the
  uploaded file and the transcription, because Soniox does not delete them on
  completion (they auto-expire after 30 days).
  Sources: <https://soniox.com/docs/stt/async/async-transcription>,
  <https://soniox.com/docs/stt/async/limits-and-quotas>
- **Timestamps:** Soniox returns small text pieces (tokens) with start and end
  times in milliseconds, not sentences and not SRT. The script joins them into
  lines. How Korean is split into tokens is not confirmed; check the result.
  Source: <https://soniox.com/docs/stt/concepts/timestamps>
- **Limits:** max 300 minutes per file. Storage limits exist (files and
  total size), which is one more reason the script cleans up. Formats include
  mp3, wav, m4a, mp4, and more.
  Source: <https://soniox.com/docs/stt/async/limits-and-quotas>
- **Language:** Korean is listed; language hints only nudge the model and do
  not restrict it.
  Sources: <https://soniox.com/docs/stt/concepts/supported-languages>,
  <https://soniox.com/docs/stt/concepts/language-hints>
- **Free credits: none.** New accounts pay as they go (ended 2025-10-27).
  Source: <https://soniox.com/blog/2025-10-27-free-credits-update-for-soniox-api>
- **Getting a key (verified 2026-09-22):**
  1. Sign up at <https://console.soniox.com/signup>, then log in.
  2. Open "My First Project" > **API Keys** > create a key.
  3. Copy it, then `check_setup.py --set-key soniox`.
  4. Add funds: <https://console.soniox.com/org/billing/overview> (top up a
     prepaid balance or turn on autopay). Card requirement and minimum
     top-up are not confirmed in public docs. Soniox says it "may require
     payment information, identity verification, or additional approval".
  Sources: <https://soniox.com/docs/stt/get-started>. Menu labels and the
  billing page could not be checked without logging in (UNVERIFIED); if your
  screen differs, look for API Keys and Billing in the console.
- **Privacy:** audio and transcripts are stored only if you use the async
  API (auto-deleted after 30 days; you can delete any time), and are not used
  to train models. Of the three, this is the strongest published privacy
  posture.
  Source: <https://soniox.com/docs/security-and-privacy>

## Speaker labels (`--diarize`, optional, off by default)

`transcribe.py --diarize` also writes `<out stem>.speakers.json`, a list like
`[{"start": 12.3, "end": 15.8, "speaker": "S1"}]` (seconds). Why: "who spoke"
comes from the audio, and that later helps the context-pack step decide who is
talking to whom on screen:
`python3 scripts/context_pack.py build ... --speakers <out stem>.speakers.json`.

- **Only Deepgram and Soniox.** OpenAI `whisper-1` and the local engines exit
  with code 2 if you pass `--diarize`.
- **Deepgram:** diarization is a **paid add-on**; this project has **not
  verified its price**. Check <https://deepgram.com/pricing> first. The cost
  estimate `transcribe.py` prints **excludes** it.
- **Soniox:** diarization is included at no extra cost, per its pricing page
  (verified 2026-09-22): <https://soniox.com/pricing>.
- The SRT has no speaker prefixes, and a subtitle block never mixes two
  speakers.
- Use it for interviews, podcasts and two-person vlogs; skip it for solo
  videos.

## Other providers considered (not supported by the script)

- **ElevenLabs Scribe v2** (`scribe_v2`): about $0.22/h, word timestamps,
  Korean supported but placed in the "Good" accuracy tier by ElevenLabs
  itself, free plan is small, weakest privacy of the set (data retained by
  default). SRT export is not confirmed. Sources (verified 2026-09-22):
  <https://elevenlabs.io/docs/overview/capabilities/speech-to-text>,
  <https://elevenlabs.io/pricing/api>.
- **AssemblyAI:** its newest model does not support Korean; only an older one
  does, and its Korean quality was not evaluated. Not recommended.
  Sources: <https://www.assemblyai.com/pricing>,
  <https://www.assemblyai.com/docs/pre-recorded-audio/supported-languages>.
- **Gladia:** more expensive at pay-as-you-go than the three above.
  Source: <https://www.gladia.io/pricing>.
- **Google Cloud speech-to-text:** not researched; it needs a Cloud project
  and is heavier for a non-technical user.

## How the tool prepares audio

Before upload it extracts the audio with ffmpeg as a small mono file (mp3,
about 64 kbps, 16 kHz). That keeps every provider under its limit (OpenAI's
25 MB is the tightest) and makes uploads fast. The video itself is not sent.
Use `--keep-audio` if you want to keep the extracted audio file.

## Cost estimate for a 15-minute video (list prices, verified 2026-09-22)

| Provider | About |
|---|---|
| Soniox | $0.025 |
| Deepgram `nova-3` | $0.065 |
| OpenAI `whisper-1` | $0.09 |

`transcribe.py` prints an estimated cost for cloud engines at the end of a
run. Tell the user the estimate before running on a long video.

## Checklist for the agent (tick each one before you run the real transcription)

1. Re-verified price, model and timestamps on the provider pages today.
2. Told the user the cost, the card requirement, and the privacy note.
3. Key entered by the user via `check_setup.py --set-key` in their own terminal, not in chat.
4. Ran `transcribe.py ... --dry-run` first.
5. Suggested an A/B test if Korean quality matters to the user.
