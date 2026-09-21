# Local speech-to-text (STT) — free, on your own computer

> **Agent: re-verify before you recommend.** Model names, versions, file
> sizes and speeds below were checked against official pages, PyPI and GitHub
> on **2026-09-22** (source URL per item). New speech models appear often. Run
> the "re-verify the best model today" recipe (section 5) before you pick, and
> never name a model from memory. `detect_hardware.py` carries a built-in
> table dated 2026-09-22 and reports `table_stale: true` after 120 days; if it
> says so, treat its recommendation as a starting point and re-verify.
>
> Anything marked **UNVERIFIED** was not confirmed on an official page.
> Speeds come from different machines and clips; they are only rough
> order-of-magnitude hints and are not comparable across rows.
>
> **Never install anything silently.** `transcribe.py` prints the exact
> install command and exits with code 3 if a runtime is missing. Show the
> command to the user, explain what it installs, and ask first.

## When to use local STT, and the honest limits

Use it when there is **no editor SRT and no API account** (or the audio must
stay on the machine). Trade-offs:

- Free, private, works offline after the model is downloaded.
- Slower than cloud, and on small computers the results are worse.
- **Korean accuracy on small models is UNVERIFIED and likely poor.** We found
  no public benchmark that measures Korean speech mixed with English terms on
  YouTube-style audio. Always spot-check a minute of output.
- Whisper-family models are still the practical default for Korean, Japanese
  and English with timestamps; no successor to `large-v3-turbo` was found as
  of the check date (absence of evidence from secondary sources, not an
  official statement).

## 1. Step 1 for the agent: detect the machine

```bash
python3 scripts/detect_hardware.py          # readable summary and a recommendation
python3 scripts/detect_hardware.py --json   # same, machine-readable
```

The JSON has `recommended.prefer_cloud` (true or false) and
`prefer_cloud_reason`. **If `prefer_cloud` is true, recommend cloud STT
instead** (Deepgram first, see `stt-cloud.md`) and say plainly that Korean
quality on small local models is unverified and likely poor on this computer.
The JSON also has `recommended.install_steps` (a list of steps; `install_cmd`
is the same steps joined into one string). Machines with neither an Apple chip
nor an NVIDIA card get faster-whisper, installed with
`python -m pip install faster-whisper`.

Explain the result in plain words ("your computer has 16 GB of memory and an
Apple chip, so a fast, good model fits"). If it prints a warning (low memory,
or Korean on a small model), pass the warning on to the user.

## 2. Tier table (verified 2026-09-22)

Which runtime and model fit which computer. "Disk" is the download size.
Speed is "times faster than the audio length"; "UNVERIFIED" means we have no
sourced number.

| Computer | Runtime | Model | Disk | Speed hint |
|---|---|---|---|---|
| Apple Silicon, 16 GB or more | whisper.cpp (Metal) | `ggml-large-v3-turbo.bin` (or `-q8_0`, 874 MB) | 1.62 GB | UNVERIFIED for this model |
| Apple Silicon, 16 GB or more (Python alternative) | mlx-whisper | `mlx-community/whisper-large-v3-turbo` | 1.61 GB | UNVERIFIED |
| Apple Silicon, 32 GB or more | same as above; can afford `ggml-large-v3.bin` (3.1 GB) or `-q5_0` (1.08 GB) | slower than turbo | 1.08 to 3.1 GB | UNVERIFIED |
| Apple Silicon, 8 GB | whisper.cpp | `ggml-large-v3-turbo-q5_0.bin`; fallback `ggml-small-q5_1.bin` | 574 MB / 190 MB | UNVERIFIED (vendor blog: small on M1 about 6x, English) |
| NVIDIA, 8 GB VRAM or more | faster-whisper, `float16` | `large-v3` or `turbo` | turbo 1.62 GB; large-v3 about 3 GB (UNVERIFIED) | about 12x, or about 46x with batching (older large-v2 benchmark) |
| NVIDIA, 4 to 6 GB VRAM | faster-whisper, `int8_float16` or `int8` | `turbo` (or `large-v3` with int8) | 1.62 GB | about 13x (large-v2 benchmark) |
| NVIDIA, 12 to 24 GB | faster-whisper with `BatchedInferencePipeline` | `large-v3` | about 3 GB | see the 8 GB row |
| CPU only (Windows, Linux, Intel Mac), 8 GB or more | faster-whisper `int8`, or whisper.cpp | Whisper `small` (or `ggml-small-q5_1.bin`) | 0.2 to 0.5 GB | about 6 to 8x for small (small-model benchmark) |
| CPU only, 16 GB or more, patient user | whisper.cpp | `ggml-large-v3-turbo-q5_0.bin` | 574 MB | UNVERIFIED (slower than small) |
| Low memory (4 GB or less) | whisper.cpp | `ggml-small-q5_1.bin` or `ggml-base` | 190 MB | UNVERIFIED |
| AMD or Intel GPU (including built-in) | whisper.cpp built with Vulkan | as in the CPU rows | as chosen | about 3-4x better than CPU-only (relative) |

Sources: <https://huggingface.co/ggerganov/whisper.cpp/tree/main> (file
sizes), <https://github.com/ggml-org/whisper.cpp> (memory, build flags),
<https://github.com/SYSTRAN/faster-whisper> (benchmarks, model ids),
<https://huggingface.co/mlx-community/whisper-large-v3-turbo>,
<https://www.phoronix.com/news/Whisper-cpp-1.8.3-12x-Perf> (Vulkan),
<https://justvoice.ai/blog/whisper-benchmark-apple-silicon-m3-m4> (vendor
blog).

**RAM thresholds are tolerant.** `detect_hardware.py` reads the memory the
operating system reports, and Windows reports less than what is installed
(part is shared with the graphics chip). So the 16 GB tier starts at **11.5 GB
reported** and the 8 GB tier at **6.5 GB reported**. A machine sold as "16 GB"
that reports 12 GB is still treated as 16 GB.

**Low memory (4 GB or less) and small models:** warn the user that Korean
accuracy on `small` and `base` is UNVERIFIED and probably poor. If quality
matters, suggest the cloud route (`stt-cloud.md`) instead.

## 3. Runtimes and exact commands (verified 2026-09-22)

First convert the video's audio to a 16 kHz mono WAV (the script does this
for you; shown here so you know what it does):

```bash
ffmpeg -y -i "input.mp4" -vn -ac 1 -ar 16000 -c:a pcm_s16le audio16k.wav
```

### whisper.cpp (Mac, CPU, NVIDIA, AMD/Intel via Vulkan)

- Latest release checked: v1.9.4 (2026-09-11).
  Source: <https://api.github.com/repos/ggml-org/whisper.cpp/releases/latest>
- Install on **macOS only**: `brew install whisper.cpp` (the command is
  `whisper-cli`). The old formula name was `whisper-cpp`. On Windows and
  Linux there is no brew; see the next point.
- If `whisper-cli` is installed but not on PATH, set the environment variable
  `YTCAPTION_WHISPER_CLI` to its full path (for example
  `$env:YTCAPTION_WHISPER_CLI = "C:\tools\whisper\whisper-cli.exe"` in
  PowerShell, or `export YTCAPTION_WHISPER_CLI=/opt/whisper/whisper-cli` on
  macOS/Linux; adjust the path).
- Windows or Linux: build from source, or check the release assets for a
  prebuilt binary (availability of official prebuilt Windows CUDA binaries is
  UNVERIFIED; check <https://github.com/ggml-org/whisper.cpp/releases> at run
  time). **On Windows a build needs** Git, CMake and the Visual Studio Build
  Tools with the "Desktop development with C++" workload (exact installer
  wording UNVERIFIED). A prebuilt release zip from the project's GitHub
  releases page may be easier, but that is **NOT verified** (a Windows zip may
  not exist for the current release): check the assets first. **On Windows
  prefer the faster-whisper pip path below: it needs no compiler.** Build flags: `-DGGML_CUDA=1` (NVIDIA), `-DGGML_VULKAN=1` (AMD, Intel,
  NVIDIA), `-DWHISPER_COREML=1` (Apple).
  Source: <https://github.com/ggml-org/whisper.cpp>
- Model download: `sh ./models/download-ggml-model.sh large-v3-turbo`
  (macOS/Linux; on Windows this script needs Git Bash, so simply download the
  file from <https://huggingface.co/ggerganov/whisper.cpp/tree/main> in the
  browser).
- Example (Korean, SRT, with silence filtering):

```bash
whisper-cli -m models/ggml-large-v3-turbo.bin -f audio16k.wav -l ko -osrt -of out --vad -vm models/ggml-silero-v6.2.0.bin -sns
```

  Flags confirmed in the project's CLI docs:
  <https://raw.githubusercontent.com/ggml-org/whisper.cpp/master/examples/cli/README.md>.
  Where to download the Silero VAD file is UNVERIFIED; look in the repo's
  `models/` folder. An equivalent prompt flag (`--prompt`) is not confirmed in
  what we read; check `whisper-cli -h`.

### mlx-whisper (Apple Silicon only)

- `pip install mlx-whisper` (macOS on Apple Silicon only; 0.4.3, released 2025-08-29; still fine, just not
  evolving quickly).
- Example:

```bash
mlx_whisper audio16k.wav --model mlx-community/whisper-large-v3-turbo --language ko --output-format srt --output-dir out --condition-on-previous-text False --word-timestamps True
```

  How boolean flags are spelled (`False` versus a bare flag) is UNVERIFIED;
  run `mlx_whisper -h` once. No built-in silence filter (see pitfalls).
  Source: <https://raw.githubusercontent.com/ml-explore/mlx-examples/main/whisper/mlx_whisper/cli.py>

### faster-whisper (NVIDIA or CPU)

- `python -m pip install faster-whisper` (1.2.1, released 2025-10-31; stable but slow
  moving). It needs Python 3.9 or newer, and does not need a separate ffmpeg
  install.
- Built-in model names: `large-v3`, and `turbo` (also `large-v3-turbo`).
  Source: <https://raw.githubusercontent.com/SYSTRAN/faster-whisper/master/faster_whisper/utils.py>
- Compute types: `float16` on NVIDIA; `int8` on CPU; `int8_float16` to cut
  memory (the `int8_float16` behavior is UNVERIFIED in the docs we read).
  GPU requires compute capability 3.5 or newer.
  Source: <https://opennmt.net/CTranslate2/hardware_support.html>
- No command-line tool ships with it; `transcribe.py` calls it from Python.
  A ready-made alternative CLI exists (`whisper-ctranslate2`), but its README
  still says CUDA 11 for GPU, which looks out of date; do not rely on that
  line.

### Not recommended here

- **WhisperX:** no default alignment model for Korean or Japanese, heavier
  install, unnecessary for SRT. <https://github.com/m-bain/whisperX>
- **WhisperKit:** no SRT output. <https://github.com/argmaxinc/WhisperKit>
- **Cohere Transcribe:** no timestamps. **NVIDIA Parakeet / Canary:** no
  Korean or Japanese. **Distil-Whisper:** English-oriented.
  <https://huggingface.co/CohereLabs/cohere-transcribe-03-2026>
- **Korean fine-tunes of Whisper turbo:** trained on read speech; gains may
  not carry over to casual talking. Do not swap them in silently.
- **Qwen3-ASR (1.7B / 0.6B) with the Qwen3 forced aligner:** the best
  credible optional challenger for Korean and Japanese (vendor-reported Korean
  numbers are good), but we found **no independent comparison with Whisper**,
  and its timestamp path needs PyTorch and chunking (aligner limit 300
  seconds per call). Treat it as an optional second engine, not the default.
  <https://huggingface.co/Qwen/Qwen3-ASR-1.7B>,
  <https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B>

## 4. Pitfalls (Korean and Whisper in general)

No Korean-specific hallucination study was found; these are general Whisper
failure modes that apply to Korean too. Sources:
<https://github.com/openai/whisper/discussions/1606>,
<https://github.com/SYSTRAN/faster-whisper/issues/843>,
<https://huggingface.co/openai/whisper-large-v3-turbo> (model card:
"prone to hallucination" and to repetitive text).

1. **Silence, music, intro and outro produce invented lines** or a loop of
   the same sentence. Turn on voice-activity detection (VAD): `vad_filter` in
   faster-whisper, `--vad` in whisper.cpp. mlx-whisper has none.
2. **Repeated loops:** set `condition_on_previous_text` off (`False`). The
   trade-off is slightly less consistent spelling across segments. Keep the
   default `no_speech_threshold` 0.6 and `compression_ratio_threshold` 2.4.
3. **VAD can clip soft word starts.** Faster-whisper's default only drops
   silence over 2 seconds; tighten with `min_silence_duration_ms=500` and
   spot-check.
4. **Names and English terms inside Korean speech:** give an
   `initial_prompt` with the channel name, guests and product terms spelled
   the way you want them (`--prompt` in `transcribe.py`). Keep it short.
   Always set the language explicitly (`ko`) instead of auto-detect, because
   auto-detect on a noisy first 30 seconds can pick the wrong language.
5. **Timestamps on `turbo` are less reliable than on `large-v3`** (user
   reports, <https://github.com/openai/whisper/discussions/2363>). Always run
   `verify_srt.py` and glance at the block boundaries.
6. **Do not score Korean with word error rate.** Spacing in Korean is
   inconsistent, so character error rate is used instead.

## 4b. Windows and NVIDIA gotchas (faster-whisper)

Sources: <https://github.com/SYSTRAN/faster-whisper> (README),
<https://github.com/OpenNMT/CTranslate2/releases>, and issues #1230, #1276,
#1080 in the faster-whisper repository.

| Symptom | Cause | Fix |
|---|---|---|
| `Library cublas64_12.dll is not found or cannot be loaded` | Newer CTranslate2 needs CUDA 12 libraries, which are not in the pip package | Install the NVIDIA CUDA Toolkit 12.x and put its `bin` folder on PATH; or use whisper.cpp instead |
| `Could not locate cudnn_ops64_9.dll` | cuDNN 9 missing or mismatched | `pip install -U ctranslate2 faster-whisper` (a newer version made cuDNN optional); if it persists install cuDNN 9 for CUDA 12 and add its `bin` to PATH |
| The README's `pip install nvidia-cublas-cu12 ...` line does not work | That route is for Linux only | On Windows use the Toolkit route above |
| Old guides that rename DLLs to `..._11.dll` | Outdated (CUDA 11) | Update the NVIDIA driver and use CUDA 12 |
| GPU silently not used | Old driver, wrong wheel, or GPU below compute capability 3.5 | `python -c "import ctranslate2;print(ctranslate2.get_cuda_device_count())"` must print 1 or more |
| Python too new for a dependency | Very new Python versions may lack wheels (UNVERIFIED) | Use Python 3.11 or 3.12 |

For a non-technical Windows user the least painful path is faster-whisper
installed with `py -3 -m pip install faster-whisper`, because it needs **no
compiler** (whisper.cpp needs Git, CMake and the Visual Studio C++ build
tools). On CPU it works out of the box. With an NVIDIA card the DLL fixes in
the table above may be needed; if that becomes a time sink, run on CPU or use
cloud STT. Purfview's prepackaged `faster-whisper-xxl.exe`
(<https://github.com/Purfview/whisper-standalone-win>) bundles the NVIDIA
libraries, but it is a third-party binary: tell the user and let them decide.
Do not auto-download it.

## 5. Re-verify "best model today" (run before you pick)

Do this each time the wizard reaches local STT, in this order.

1. **Whisper successor check:** <https://github.com/openai/whisper/releases>
   and <https://huggingface.co/openai>. Anything newer than
   `large-v3-turbo`?
2. **New challengers:** open
   <https://huggingface.co/models?pipeline_tag=automatic-speech-recognition&sort=trending>
   and, for each new candidate, open its model card and check three boxes:
   Korean is listed? Timestamps (or an aligner) available? A ready runtime in
   whisper.cpp, faster-whisper, mlx or llama.cpp?
3. **Leaderboard (context only):** the Open ASR Leaderboard
   (<https://huggingface.co/spaces/hf-audio/open_asr_leaderboard>, background:
   <https://github.com/huggingface/open_asr_leaderboard>) has **no Korean or
   Japanese track** as of the March 2026 snapshot, so it cannot pick a Korean
   winner. The page is rendered by JavaScript, so a plain fetch shows nothing;
   use a browser.
4. **Freshness of the runtimes:**

macOS / Linux (Windows 10 and 11 also ship `curl.exe`; in PowerShell type
`curl.exe`, not `curl`):

```bash
curl -s https://api.github.com/repos/ggml-org/whisper.cpp/releases/latest   # tag_name, published_at
curl -s https://pypi.org/pypi/faster-whisper/json                            # info.version
curl -s https://pypi.org/pypi/mlx-whisper/json
curl -s https://pypi.org/pypi/ctranslate2/json
```

Or, in PowerShell, one line per address:
`(Invoke-RestMethod https://pypi.org/pypi/faster-whisper/json).info.version`.

Three search queries to run if the checks above are unclear:

1. `best open source speech recognition model Korean Japanese 2026 word timestamps whisper alternative`
2. `Open ASR Leaderboard new number one model <current month> 2026 open weights`
3. `<candidate model name> Korean CER benchmark vs whisper-large-v3 timestamps whisper.cpp OR faster-whisper OR mlx`

### Decision rule

Promote a challenger over Whisper `large-v3` / `large-v3-turbo` **only if all
four are true**:

1. Its model card lists Korean and Japanese.
2. It can produce timestamps.
3. A ready local runtime exists for this user's hardware tier.
4. There is at least one **independent** Korean number better than Whisper's.

Otherwise keep Whisper. Also run a 60-second Korean self-check on the user's
own audio and compare two engines by eye rather than trusting a leaderboard.

## 6. How `transcribe.py` runs local STT

```bash
python3 scripts/transcribe.py --input "<video.mp4>" --engine local --lang ko --out source.srt --dry-run
python3 scripts/transcribe.py --input "<video.mp4>" --engine local --lang ko --out source.srt --yes
```

- `--runtime auto` (default) picks a runtime from `detect_hardware.py`; force
  one with `--runtime whisper.cpp | mlx-whisper | faster-whisper`.
- `--model` overrides the model chosen from the tier table.
- Settings applied: silence filtering on, `condition_on_previous_text` off,
  language set explicitly.
- Missing runtime: prints the exact install command and exits 3. Show it to
  the user, ask, and only then run it (or let the user run it).
- First-run model download (mlx-whisper, faster-whisper): `--dry-run` reports
  "will download ~N MB". Tell the user the size, ask, and re-run with `--yes`
  only after they agree; without `--yes` the script will not download.
- The script refuses to overwrite an existing `--out` file (exit 2) unless you
  pass `--force`.
- Audio is extracted into an ASCII-only temporary folder. `--keep-audio` copies
  the extracted audio out of that temp folder next to the output (an existing
  kept file needs `--force` too).
- On Windows, whisper-cli is given ASCII file names inside the work folder.
  This avoids failures when the folder name is non-ASCII (for example Korean
  folder names on a non-Korean Windows locale).
- `--diarize` (speaker labels) is not supported for local engines (exit 2);
  see `stt-cloud.md`.
- Then check the output the same way as any STT draft: read it, then
  continue to step ⓪ in `SKILL.md`.
