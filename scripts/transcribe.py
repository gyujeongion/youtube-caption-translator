"""Transcribe a video/audio file to a timestamped SRT (for when there is no editor SRT).

    python3 transcribe.py --input video.mp4 --engine deepgram --lang ko --out video.srt
    python3 transcribe.py --input video.mp4 --engine local --lang ko --out video.srt
    python3 transcribe.py --input video.mp4 --engine soniox --dry-run

    options: --model MODEL   --runtime auto|whisper.cpp|mlx-whisper|faster-whisper
             --keep-audio    --dry-run    --prompt "proper nouns, channel name, terms"
             --yes --force   --diarize

Flow: ffmpeg extracts audio (cloud engines: mono 16 kHz mp3 ~64 kbps; local
engine: mono 16 kHz wav) -> engine -> timed words -> sentence/line segmenter
(_common.segment_words) -> UTF-8 SRT -> stats (blocks, duration, engine, model,
estimated cost).

Cloud engines (list prices dated 2026-09-22; re-verify before quoting):
  deepgram  nova-3, utterances+words. $0.258/h
  openai    ONLY whisper-1 (newer OpenAI STT models return no timestamps). $0.36/h
  soniox    stt-async-v5 async flow; uploaded file + transcription are deleted after. $0.10/h
Speaker diarization (--diarize, OFF by default; deepgram or soniox only): also writes
<out stem>.speakers.json next to the SRT: [{"start": 12.3, "end": 15.8, "speaker": "S1"}, ...]
(seconds, consecutive words of one speaker merged, labels S1, S2... in order of first appearance).
The SRT stays plain text -- no speaker prefixes -- but a block never spans two speakers.
Deepgram bills diarization as an add-on that is NOT in the cost estimate; Soniox includes it.
whisper-1 (openai) and the local engines cannot diarize: --diarize exits 2 for them.
Keys come from real env vars or ytcaption.env (see check_setup.py --set-key).
The local engine is free and only INVOKES an already-installed runtime; it never
installs anything -- if one is missing it prints the install command and exits 3.

Exit codes: 0 ok, 2 bad args, 3 missing dependency/key, 4 engine/API error.
Python standard library only (3.10+).
"""
from __future__ import annotations

import argparse
import http.client
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402

EXIT_OK, EXIT_ARGS, EXIT_DEP, EXIT_API = 0, 2, 3, 4

DEFAULT_MODELS = {"deepgram": "nova-3", "openai": "whisper-1", "soniox": "stt-async-v5"}
# list prices, USD per audio hour, checked 2026-09-22
PRICE_PER_HOUR = {"deepgram": 0.258, "openai": 0.36, "soniox": 0.10}
PRICE_DATE = "2026-09-22"

DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"
# Diarization query parameter, per the official Deepgram docs (fetched 2026-09-22):
# https://developers.deepgram.com/docs/diarization -- `diarize_model=latest` enables diarization and picks the
# newest batch diarizer; the boolean `diarize=true` is documented as DEPRECATED (routes to the v1 diarizer) and
# requests that set both are rejected, so we send only diarize_model. Speaker labels come back as an integer
# `speaker` on each entry of words[] (also on words inside utterances[]); see the response example at
# https://developers.deepgram.com/reference/speech-to-text/listen-pre-recorded
DEEPGRAM_DIARIZE_PARAM = ("diarize_model", "latest")
DEEPGRAM_DIARIZE_NOTICE = ("Deepgram diarization is billed as an add-on; not included in the cost estimate above "
                           "\u2014 check https://deepgram.com/pricing")
SONIOX_DIARIZE_NOTE = ("Soniox includes speaker diarization at no extra cost "
                       "(soniox.com/pricing FAQ, checked 2026-09-22)")
OPENAI_URL = "https://api.openai.com/v1/audio/transcriptions"
SONIOX_BASE = "https://api.soniox.com/v1"

OPENAI_MAX_BYTES = 24 * 1024 * 1024      # documented limit is 25 MB; stay under it
OPENAI_CHUNK_SECONDS = 20 * 60
SONIOX_POLL_INTERVAL = 2.0
SONIOX_POLL_TIMEOUT = 3600.0
HTTP_TIMEOUT = 900

RUNTIMES = ("whisper.cpp", "mlx-whisper", "faster-whisper")


class ArgError(Exception):
    pass


class DependencyError(Exception):
    pass


class EngineError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status  # HTTP status when the error came from an API call


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """subprocess.run with captured, UTF-8-decoded output (never trusts the locale codepage).

    Always an argv list (no shell involved); stdin is closed so a tool can never wait on the console.
    """
    kw.setdefault("stdin", subprocess.DEVNULL)
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def ffprobe_duration(path: str | os.PathLike) -> float | None:
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    try:
        r = _run([exe, "-v", "error", "-show_entries", "format=duration",
                  "-of", "default=noprint_wrappers=1:nokey=1", C.media_arg(path)], timeout=60)
        return float(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def extract_audio(src: pathlib.Path, dst: pathlib.Path, *, cloud: bool) -> None:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise DependencyError(C.ffmpeg_missing_message())
    if cloud:
        codec = ["-c:a", "libmp3lame", "-b:a", "64k"]
    else:
        codec = ["-c:a", "pcm_s16le"]
    cmd = [exe, "-y", "-nostdin", "-loglevel", "error", "-i", C.media_arg(src),
           "-vn", "-ac", "1", "-ar", "16000", *codec, C.media_arg(dst)]
    r = _run(cmd)
    if r.returncode != 0 or not dst.exists():
        raise EngineError(f"ffmpeg could not extract audio: {r.stderr.strip()[:400]}")


def http_request(url: str, *, method: str = "GET", headers: dict | None = None,
                 data: bytes | None = None, timeout: float = HTTP_TIMEOUT) -> tuple[int, bytes]:
    """One HTTP call via urllib. Never includes headers (keys) in error text.

    Every transport failure (HTTP error, DNS/refused, timeout, reset, truncated body)
    becomes EngineError so the CLI exits 4 instead of printing a traceback.
    """
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    host = urllib.parse.urlparse(url).netloc
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            return int(status), resp.read()
    except urllib.error.HTTPError as e:
        body = ""
        try:
            if e.code not in (401, 403):  # auth errors can echo a masked key fragment
                body = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            pass
        finally:
            e.close()
        if e.code in (401, 403):
            raise EngineError(f"HTTP {e.code} from {host}: authentication/permission failure "
                              "(check the API key and account billing; response body hidden)",
                              status=e.code) from None
        raise EngineError(f"HTTP {e.code} from {host}: {body}", status=e.code) from None
    except urllib.error.URLError as e:
        raise EngineError(f"network error talking to {host}: {e.reason}") from None
    except (OSError, http.client.HTTPException) as e:  # timeout, connection reset, IncompleteRead, ...
        raise EngineError(f"network error talking to {host}: {type(e).__name__}: {str(e)[:200]}") from None


def _json(body: bytes) -> dict:
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError:
        raise EngineError("provider returned a non-JSON response") from None


def _terms(prompt: str) -> list[str]:
    return [t.strip() for t in re.split(r"[,\n]", prompt or "") if t.strip()]


# --------------------------------------------------------------------------
# Deepgram
# --------------------------------------------------------------------------

def deepgram_transcribe(audio: pathlib.Path, key: str, lang: str, model: str, prompt: str,
                        diarize: bool = False) -> list[dict]:
    terms = _terms(prompt)
    data = audio.read_bytes()

    def call(with_terms: bool) -> bytes:
        params = [("model", model), ("language", lang), ("smart_format", "true"),
                  ("utterances", "true"), ("punctuate", "true"), ("mip_opt_out", "true")]
        if diarize:
            params.append(DEEPGRAM_DIARIZE_PARAM)
        if with_terms:
            for t in terms:
                params.append(("keyterm", t))  # Nova-3 term boosting; whether it works for Korean is UNVERIFIED
        _, body = http_request(DEEPGRAM_URL + "?" + urllib.parse.urlencode(params), method="POST", data=data,
                               headers={"Authorization": f"Token {key}", "Content-Type": "audio/mpeg"})
        return body

    try:
        body = call(bool(terms))
    except EngineError as e:
        if terms and e.status == 400:
            C.eprint("warning: Deepgram rejected the request with keyterms (HTTP 400); retrying once without keyterms.")
            body = call(False)
        else:
            raise
    return deepgram_words(_json(body), diarize)


def deepgram_words(data: dict, diarize: bool = False) -> list[dict]:
    results = data.get("results") or {}
    words: list[dict] = []
    for u in results.get("utterances") or []:
        uw = u.get("words") or []
        if not uw:
            words += C.segments_to_words([{"start": u["start"], "end": u["end"], "text": u.get("transcript", "")}])
            continue
        toks = (u.get("transcript") or "").split()
        # transcript keeps punctuation even when word entries lack it
        use_tr = len(toks) == len(uw)
        for i, w in enumerate(uw):
            text = toks[i] if use_tr else (w.get("punctuated_word") or w.get("word") or "")
            word = {"start": float(w["start"]), "end": float(w["end"]), "text": text}
            if diarize and w.get("speaker") is not None:
                word["speaker"] = w["speaker"]        # integer speaker id (0, 1, ...)
            words.append(word)
    if not words:
        chans = results.get("channels") or []
        alt = ((chans[0].get("alternatives") or [{}])[0]) if chans else {}
        for w in alt.get("words") or []:
            word = {"start": float(w["start"]), "end": float(w["end"]),
                    "text": w.get("punctuated_word") or w.get("word") or ""}
            if diarize and w.get("speaker") is not None:
                word["speaker"] = w["speaker"]
            words.append(word)
    return words


# --------------------------------------------------------------------------
# OpenAI (whisper-1 only)
# --------------------------------------------------------------------------

def split_audio(audio: pathlib.Path, workdir: pathlib.Path,
                chunk_seconds: int = OPENAI_CHUNK_SECONDS) -> list[tuple[pathlib.Path, float]]:
    """Cut audio into chunks; returns [(chunk_path, start_offset_seconds)]."""
    exe = shutil.which("ffmpeg")
    if not exe:
        raise DependencyError(C.ffmpeg_missing_message())
    pattern = workdir / f"chunk_%03d{audio.suffix}"
    r = _run([exe, "-y", "-nostdin", "-loglevel", "error", "-i", C.media_arg(audio), "-f", "segment",
              "-segment_time", str(chunk_seconds), "-c", "copy", "-reset_timestamps", "1", C.media_arg(pattern)])
    chunks = sorted(workdir.glob(f"chunk_*{audio.suffix}"))
    if r.returncode != 0 or not chunks:
        raise EngineError(f"ffmpeg could not split audio: {r.stderr.strip()[:300]}")
    out, offset = [], 0.0
    for i, c in enumerate(chunks):
        if c.stat().st_size > OPENAI_MAX_BYTES:
            raise EngineError(f"chunk {c.name} is still over the 25 MB limit; use a shorter chunk size")
        out.append((c, offset))
        d = ffprobe_duration(c)
        offset += d if d else chunk_seconds
    return out


def openai_transcribe(audio: pathlib.Path, key: str, lang: str, model: str, prompt: str,
                      workdir: pathlib.Path) -> list[dict]:
    if model != "whisper-1":
        raise ArgError(
            f"OpenAI model '{model}' is not supported: the newer OpenAI speech-to-text models "
            "(gpt-transcribe, gpt-4o-transcribe, gpt-4o-mini-transcribe) return NO timestamps, "
            "so they cannot produce subtitles. Use --model whisper-1, or pick deepgram/soniox/local.")
    if audio.stat().st_size > OPENAI_MAX_BYTES:
        parts = split_audio(audio, workdir)
    else:
        parts = [(audio, 0.0)]
    segments: list[dict] = []
    for path, offset in parts:
        fields: dict = {"model": model, "language": lang, "response_format": "verbose_json",
                        "timestamp_granularities[]": "segment"}
        if prompt:
            fields["prompt"] = prompt
        body, ctype = C.encode_multipart(fields, "file", path, "audio/mpeg")
        _, resp = http_request(OPENAI_URL, method="POST", data=body, headers={
            "Authorization": f"Bearer {key}", "Content-Type": ctype})
        for s in _json(resp).get("segments") or []:
            segments.append({"start": float(s["start"]) + offset, "end": float(s["end"]) + offset,
                             "text": s.get("text", "")})
    return C.segments_to_words(segments)


# --------------------------------------------------------------------------
# Soniox
# --------------------------------------------------------------------------

def soniox_tokens_to_words(tokens: list[dict], lang: str = "ko", gap_seconds: float = 0.3) -> list[dict]:
    """Sub-word tokens -> words.

    A new word starts when: the token text begins with whitespace; the previous word ended in
    sentence/clause punctuation; the silence before the token exceeds gap_seconds; or the language
    is written without spaces (ja/zh/...), in which case every token is its own word and
    segment_words(lang=...) glues them back without spaces; or the token's `speaker` differs
    from the current word's (a word never mixes speakers).
    Words carry the token's `speaker` (string, e.g. "1") when diarization was on.
    """
    no_space = lang.split("-")[0].lower() in C.NO_SPACE_LANGS
    punct = C.SENTENCE_END + C.CLAUSE_END
    words: list[dict] = []
    cur: dict | None = None
    prev_end: float | None = None
    force_new = False
    for t in tokens:
        text = t.get("text", "")
        if not text or re.fullmatch(r"<[^>]*>", text.strip()):
            continue  # empty / control markers such as <end>
        if not text.strip():
            force_new = True  # whitespace-only token separates words
            continue
        start, end = t["start_ms"] / 1000.0, t["end_ms"] / 1000.0
        spk = t.get("speaker")
        spk = None if spk is None else str(spk)
        new_word = (cur is None or force_new or no_space or text[0].isspace()
                    or (spk is not None and cur.get("speaker") not in (None, spk))
                    or cur["text"].endswith(punct)
                    or (prev_end is not None and start - prev_end > gap_seconds))
        if new_word:
            if cur is not None:
                words.append(cur)
            cur = {"start": start, "end": end, "text": text.strip()}
            if spk is not None:
                cur["speaker"] = spk
        else:
            cur["text"] += text.strip()
            cur["end"] = max(cur["end"], end)
        prev_end, force_new = end, False
    if cur is not None and cur["text"]:
        words.append(cur)
    return [w for w in words if w["text"]]


def _soniox_delete(kind: str, obj_id: str, key: str) -> None:
    """Best-effort DELETE; never raises.

    DELETE /v1/files/{id} and DELETE /v1/transcriptions/{id} (204) were confirmed in the Soniox API
    reference on 2026-09-22; they have not been exercised against the live API by the tests.
    """
    try:
        status, _ = http_request(f"{SONIOX_BASE}/{kind}/{obj_id}", method="DELETE",
                                 headers={"Authorization": f"Bearer {key}"}, timeout=60)
        if status not in (200, 204):
            C.eprint(f"warning: unexpected status {status} deleting Soniox {kind[:-1]} {obj_id}")
    except EngineError as e:
        if "HTTP 404" not in str(e):
            C.eprint(f"warning: could not delete Soniox {kind[:-1]} {obj_id} ({e}); "
                     "Soniox auto-deletes files after 30 days, or delete it in the console.")
    except Exception as e:  # noqa: BLE001 - cleanup must never fail the run
        C.eprint(f"warning: could not delete Soniox {kind[:-1]} {obj_id} ({e})")


def soniox_transcribe(audio: pathlib.Path, key: str, lang: str, model: str, prompt: str,
                      diarize: bool = False) -> list[dict]:
    auth = {"Authorization": f"Bearer {key}"}
    file_id = tr_id = None
    try:
        body, ctype = C.encode_multipart({}, "file", audio, "audio/mpeg")
        _, resp = http_request(f"{SONIOX_BASE}/files", method="POST", data=body,
                               headers={**auth, "Content-Type": ctype})
        file_id = _json(resp).get("id")
        if not file_id:
            raise EngineError("Soniox upload returned no file id")

        hints = [lang] if lang == "en" else [lang, "en"]
        payload: dict = {"model": model, "file_id": file_id, "language_hints": hints}
        if diarize:
            # official reference: https://soniox.com/docs/api-reference/stt/transcriptions/create_transcription
            # (`enable_speaker_diarization`, checked 2026-09-22); tokens then carry a string `speaker`
            # (get_transcription_transcript reference; concept page /docs/stt/concepts/speaker-diarization)
            payload["enable_speaker_diarization"] = True
        terms = _terms(prompt)
        if terms:
            # context.terms as an array of strings follows the create_transcription schema in the
            # API reference (2026-09-22); not exercised live.
            payload["context"] = {"terms": terms}
        _, resp = http_request(f"{SONIOX_BASE}/transcriptions", method="POST",
                               data=json.dumps(payload).encode("utf-8"),
                               headers={**auth, "Content-Type": "application/json"})
        tr_id = _json(resp).get("id")
        if not tr_id:
            raise EngineError("Soniox did not return a transcription id")

        deadline = time.monotonic() + SONIOX_POLL_TIMEOUT
        while True:
            _, resp = http_request(f"{SONIOX_BASE}/transcriptions/{tr_id}", headers=auth, timeout=60)
            st = _json(resp)
            status = st.get("status")
            if status == "completed":
                break
            if status in ("error", "failed"):
                raise EngineError(f"Soniox transcription failed: {st.get('error_message') or 'unknown error'}")
            if time.monotonic() > deadline:
                raise EngineError("Soniox transcription did not finish before the timeout")
            time.sleep(SONIOX_POLL_INTERVAL)

        _, resp = http_request(f"{SONIOX_BASE}/transcriptions/{tr_id}/transcript", headers=auth, timeout=120)
        return soniox_tokens_to_words(_json(resp).get("tokens") or [], lang)
    finally:
        # transcription first, then the file it referenced
        if tr_id:
            _soniox_delete("transcriptions", tr_id, key)
        if file_id:
            _soniox_delete("files", file_id, key)


# --------------------------------------------------------------------------
# local runtimes (only invoked if already installed)
# --------------------------------------------------------------------------

def find_whisper_cli() -> str | None:
    """whisper-cli location: $YTCAPTION_WHISPER_CLI, PATH, then a cmake build under ./whisper.cpp/build/bin."""
    env = os.environ.get("YTCAPTION_WHISPER_CLI", "").strip()
    if env:
        p = C.expand_path(env)
        if p.is_file():
            return str(p)
        found = shutil.which(env)
        if found:
            return found
        C.eprint(f"warning: YTCAPTION_WHISPER_CLI={env} does not exist; ignoring it")
    found = shutil.which("whisper-cli")
    if found:
        return found
    base = pathlib.Path.cwd() / "whisper.cpp" / "build" / "bin"
    for folder in (base, base / "Release"):
        for name in ("whisper-cli.exe", "whisper-cli"):
            if (folder / name).is_file():
                return str(folder / name)
    return None


def available_runtimes() -> dict[str, bool]:
    return {
        "whisper.cpp": bool(find_whisper_cli()),
        "mlx-whisper": bool(shutil.which("mlx_whisper")),
        "faster-whisper": importlib.util.find_spec("faster_whisper") is not None,
    }


def pick_runtime(requested: str, recommended: str, avail: dict[str, bool] | None = None) -> str:
    """auto -> recommended runtime if installed, else the first installed one, else ''."""
    avail = avail if avail is not None else available_runtimes()
    if requested != "auto":
        return requested
    order = [recommended] + [r for r in RUNTIMES if r != recommended]
    for r in order:
        if avail.get(r):
            return r
    return ""


def _model_dirs() -> list[pathlib.Path]:
    dirs: list[pathlib.Path] = []
    env = os.environ.get("YTCAPTION_MODEL_DIR", "").strip()
    if env:
        dirs.append(C.expand_path(env))
    home = pathlib.Path.home()
    dirs += [C.config_dir() / "models", home / ".cache" / "ytcaption" / "models",
             home / ".cache" / "whisper.cpp", pathlib.Path.cwd() / "models"]
    return dirs


def find_whispercpp_model(model: str) -> pathlib.Path:
    p = C.expand_path(model)
    if p.is_file():
        return p
    names = [model] if model.endswith(".bin") else [f"ggml-{model}.bin"]
    if model.startswith("ggml-") and not model.endswith(".bin"):
        names = [model + ".bin"]
    for d in _model_dirs():
        for n in names:
            if (d / n).is_file():
                return d / n
    fname = names[0]
    dest = pathlib.Path.home() / ".cache" / "ytcaption" / "models" / fname
    raise DependencyError(
        f"whisper.cpp model file '{fname}' not found. Download it (~1 GB-3 GB depending on the model):\n"
        f"  {'curl.exe' if C.is_windows() else 'curl'} --create-dirs -L -o \"{dest}\" "
        f"\"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{fname}\"\n"
        "or set YTCAPTION_MODEL_DIR to the folder that holds it, or pass --model /path/to/ggml-*.bin")


def find_vad_model() -> pathlib.Path | None:
    for d in _model_dirs():
        if d.is_dir():
            hits = sorted(d.glob("ggml-silero*.bin"))
            if hits:
                return hits[-1]
    return None


def _remove_flag(cmd: list[str], flag: str) -> list[str]:
    """Drop `flag` and, if the next token is not another flag, its value."""
    out, skip = [], False
    for i, tok in enumerate(cmd):
        if skip:
            skip = False
            continue
        if tok == flag:
            if i + 1 < len(cmd) and not cmd[i + 1].startswith("-"):
                skip = True
            continue
        out.append(tok)
    return out


def run_whispercpp(wav: pathlib.Path, model: str, lang: str, prompt: str, workdir: pathlib.Path) -> list[dict]:
    exe = find_whisper_cli()
    if not exe:
        raise DependencyError("whisper-cli not found (set YTCAPTION_WHISPER_CLI to its full path if it is "
                              "not on PATH). Install, one step at a time:" + _install_hint("whisper.cpp"))
    mpath = find_whispercpp_model(model)
    base = workdir / "wcpp"
    run_kw: dict = {}
    wav_arg, base_arg = str(wav), str(base)
    if C.is_windows() and not (wav_arg + base_arg).isascii():
        # whisper-cli.exe reads argv in the ANSI codepage, so a Hangul/Japanese folder name (e.g. the
        # user profile in %TEMP%) turns into '?' on a non-Korean/non-Japanese Windows. Give it plain
        # ASCII names and run it inside the work folder instead.
        if wav.parent != workdir:
            shutil.copyfile(wav, workdir / "input.wav")
            wav = workdir / "input.wav"
        wav_arg, base_arg = wav.name, "wcpp"
        run_kw["cwd"] = str(workdir)
    cmd = [exe, "-m", str(mpath), "-f", wav_arg, "-l", lang, "-ojf", "-osrt", "-of", base_arg, "-sns",
           "-mc", "0"]  # -mc 0 = do not condition on previous text (flag not verified upstream; auto-dropped if rejected)
    if prompt:
        cmd += ["--prompt", prompt]
    vad = find_vad_model()
    if vad:
        cmd += ["--vad", "-vm", str(vad)]
    else:
        C.eprint("warning: no Silero VAD model (ggml-silero*.bin) found; running without VAD "
                 "(more risk of invented text over silence/music).")
    for _ in range(4):
        r = _run(cmd, **run_kw)
        if r.returncode == 0:
            break
        m = re.search(r"unknown argument:?\s*(\S+)", r.stderr + r.stdout)
        if m and m.group(1) in cmd:
            C.eprint(f"warning: this whisper-cli build rejects {m.group(1)}; retrying without it")
            cmd = _remove_flag(cmd, m.group(1))
            if m.group(1) == "--vad":
                cmd = _remove_flag(cmd, "-vm")
            continue
        raise EngineError(f"whisper-cli failed: {(r.stderr or r.stdout).strip()[-400:]}")
    else:
        raise EngineError("whisper-cli kept rejecting arguments")
    jpath, spath = pathlib.Path(str(base) + ".json"), pathlib.Path(str(base) + ".srt")
    segs: list[dict] = []
    if jpath.exists():
        try:
            data = json.loads(jpath.read_text(encoding="utf-8", errors="replace"))
            for e in data.get("transcription", []):
                off = e["offsets"]
                segs.append({"start": off["from"] / 1000.0, "end": off["to"] / 1000.0, "text": e.get("text", "")})
        except (ValueError, KeyError):
            segs = []
    if not segs and spath.exists():
        segs = [{"start": b["start"], "end": b["end"], "text": b["text"].replace("\n", " ")}
                for b in C.read_srt(spath)]
    return C.segments_to_words(segs)


def run_mlx_whisper(wav: pathlib.Path, model: str, lang: str, prompt: str, workdir: pathlib.Path) -> list[dict]:
    exe = shutil.which("mlx_whisper")
    if not exe:
        raise DependencyError("mlx_whisper not found. Install:" + _install_hint("mlx-whisper"))
    # --opt=VALUE form: a value that starts with '-' would otherwise be parsed as another option
    cmd = [exe, str(wav), f"--model={model}", f"--language={lang}", "--output-format", "json",
           "--output-dir", str(workdir), "--condition-on-previous-text", "False",
           "--word-timestamps", "True"]
    if prompt:
        cmd += [f"--initial-prompt={prompt}"]
    r = _run(cmd)
    if r.returncode != 0:
        raise EngineError(f"mlx_whisper failed: {(r.stderr or r.stdout).strip()[-400:]}")
    out = workdir / (wav.stem + ".json")
    if not out.exists():
        raise EngineError("mlx_whisper produced no JSON output")
    return _whisper_json_to_words(json.loads(out.read_text(encoding="utf-8")))


def _whisper_json_to_words(data: dict) -> list[dict]:
    words: list[dict] = []
    segs = data.get("segments") or []
    for s in segs:
        sw = s.get("words") or []
        if sw:
            words += [{"start": float(w["start"]), "end": float(w["end"]), "text": w.get("word", "").strip()} for w in sw]
        else:
            words += C.segments_to_words([{"start": s["start"], "end": s["end"], "text": s.get("text", "")}])
    return words


def _faster_whisper_pass(wav: pathlib.Path, model: str, lang: str, prompt: str, device: str,
                         compute_type: str) -> list[dict]:
    from faster_whisper import WhisperModel  # type: ignore

    m = WhisperModel(model, device=device, compute_type=compute_type)
    segs, _info = m.transcribe(
        str(wav), language=lang, vad_filter=True, vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=False, word_timestamps=True, initial_prompt=prompt or None)
    words: list[dict] = []
    for s in segs:  # generator: the real work (and CUDA/DLL errors) happens while iterating
        sw = getattr(s, "words", None) or []
        if sw:
            words += [{"start": float(w.start), "end": float(w.end), "text": w.word.strip()} for w in sw]
        else:
            words += C.segments_to_words([{"start": s.start, "end": s.end, "text": s.text}])
    return words


def run_faster_whisper(wav: pathlib.Path, model: str, lang: str, prompt: str, compute_type: str) -> list[dict]:
    if importlib.util.find_spec("faster_whisper") is None:
        raise DependencyError("faster_whisper not installed. Install:" + _install_hint("faster-whisper"))
    try:
        return _faster_whisper_pass(wav, model, lang, prompt, "auto", compute_type)
    except Exception as first:  # noqa: BLE001 - CUDA/cuBLAS/cuDNN failures surface lazily and as many types
        C.eprint(f"warning: faster-whisper failed on the default device ({type(first).__name__}: "
                 f"{str(first)[:200]}); retrying once on CPU with int8 (slower).")
        try:
            return _faster_whisper_pass(wav, model, lang, prompt, "cpu", "int8")
        except Exception as second:  # noqa: BLE001
            raise EngineError(
                f"faster-whisper failed on CPU too ({type(second).__name__}: {str(second)[:200]}). "
                "On Windows, missing cublas64_12.dll / cudnn_ops64_9.dll means the CUDA 12 runtime is not "
                "installed; see references/stt-local.md (Windows gotchas), or use a cloud engine.") from None


def _install_hint(runtime: str) -> str:
    """Install steps, one per line (leading newline), or a pointer to the docs."""
    import detect_hardware as H
    steps = H._install_steps(runtime, H._os_name())
    return "".join(f"\n  {st}" for st in steps) if steps else " see references/stt-local.md"


# ---- first-run model downloads (mlx-whisper / faster-whisper fetch from Hugging Face) ----

HF_REPOS = {
    "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "large-v3": "Systran/faster-whisper-large-v3",
}


def hf_hub_dir() -> pathlib.Path:
    env = os.environ.get("HF_HUB_CACHE", "").strip()
    if env:
        return C.expand_path(env)
    home = os.environ.get("HF_HOME", "").strip()
    if home:
        return C.expand_path(home) / "hub"
    return pathlib.Path.home() / ".cache" / "huggingface" / "hub"


def hf_repo_for(runtime: str, model: str) -> str | None:
    """HF repo id a runtime would download for `model`, or None if `model` is a local path."""
    if pathlib.Path(model).expanduser().exists():
        return None
    if runtime == "faster-whisper":
        if "/" in model:
            return model
        return HF_REPOS.get(model, f"Systran/faster-whisper-{model}")
    return model if "/" in model else f"mlx-community/{model}"


def model_cached(runtime: str, model: str) -> bool:
    if runtime == "whisper.cpp":
        return True  # a missing ggml file is reported separately, with a download command
    repo = hf_repo_for(runtime, model)
    if repo is None:
        return True
    snaps = hf_hub_dir() / ("models--" + repo.replace("/", "--")) / "snapshots"
    return snaps.is_dir() and any(snaps.iterdir())


def model_size_mb(runtime: str, model: str) -> int | None:
    import detect_hardware as H
    for fam in H.MODEL_FAMILIES.values():
        entry = fam.get(runtime)
        if entry and entry[0] == model:
            return entry[1]
    return None


def download_notice(runtime: str, model: str) -> tuple[str, bool]:
    """-> (one-line description, needs_download). Only meaningful for mlx-whisper / faster-whisper."""
    size = model_size_mb(runtime, model)
    size_txt = f"~{size} MB" if size else "size unknown (~1-3 GB for large models)"
    if model_cached(runtime, model):
        return f"model {model} ({size_txt}) is already in the local cache; no download", False
    return (f"model {model}: will download {size_txt} from Hugging Face on first run", True)


def local_plan(model_arg: str, runtime_arg: str) -> tuple[str, str, str]:
    """-> (runtime, model, compute_type). Raises DependencyError when nothing usable is installed."""
    import detect_hardware as H
    info = H.detect()
    rec = info["recommended"]
    runtime = pick_runtime(runtime_arg, rec["runtime"])
    if not runtime:
        raise DependencyError(
            "No local speech-to-text runtime is installed. Recommended for this machine "
            f"({info['tier']}): {rec['runtime']}\n  install, one step at a time:"
            + "".join(f"\n    {st}" for st in rec.get("install_steps") or [rec["install_cmd"]]) + "\n"
            + "".join(f"  WARNING: {w}\n" for w in rec["warnings"])
            + "Install it yourself (or ask your agent to run the commands with your OK), then re-run.")
    if not available_runtimes().get(runtime):
        raise DependencyError(f"{runtime} is not installed. Install:" + _install_hint(runtime))
    model = model_arg or H.model_for(runtime, rec["model"])[0]
    return runtime, model, rec.get("compute_type", "int8")


def local_transcribe(wav: pathlib.Path, runtime: str, model: str, lang: str, prompt: str,
                     compute_type: str, workdir: pathlib.Path) -> list[dict]:
    lang = lang.split("-")[0].lower()  # Whisper runtimes accept base language codes only (pt-BR -> pt)
    if runtime == "whisper.cpp":
        return run_whispercpp(wav, model, lang, prompt, workdir)
    if runtime == "mlx-whisper":
        return run_mlx_whisper(wav, model, lang, prompt, workdir)
    if runtime == "faster-whisper":
        return run_faster_whisper(wav, model, lang, prompt, compute_type)
    raise ArgError(f"unknown runtime {runtime}")


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------

def estimate_cost(engine: str, seconds: float | None) -> str:
    if engine == "local":
        return "free (local)"
    if not seconds:
        return f"unknown (duration unavailable; list price ${PRICE_PER_HOUR[engine]}/h as of {PRICE_DATE})"
    return f"~${seconds / 3600 * PRICE_PER_HOUR[engine]:.3f} (list ${PRICE_PER_HOUR[engine]}/h as of {PRICE_DATE})"


def fmt_dur(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    m, s = divmod(int(round(seconds)), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Transcribe a video/audio file to an SRT (cloud STT or a local runtime).",
        epilog="Exit codes: 0 ok, 2 bad args, 3 missing dependency/key, 4 engine/API error.")
    ap.add_argument("--input", required=True, help="video or audio file")
    ap.add_argument("--engine", required=True, choices=["deepgram", "openai", "soniox", "local"])
    ap.add_argument("--lang", default=None, help="spoken language code (default: prefs source_language, else ko)")
    ap.add_argument("--out", default=None, help="output .srt (default: <input name>.stt.srt next to the input)")
    ap.add_argument("--model", default=None, help="override the engine's model / local model file or id")
    ap.add_argument("--runtime", default="auto", choices=["auto", *RUNTIMES],
                    help="local engine only: which runtime to use (default auto)")
    ap.add_argument("--keep-audio", action="store_true", help="keep the extracted audio next to the output")
    ap.add_argument("--dry-run", action="store_true",
                    help="check prerequisites and print the plan (incl. any model download size); no audio extraction, no API calls, no cost")
    ap.add_argument("--prompt", default="", help="proper nouns / channel terms to bias recognition, comma separated")
    ap.add_argument("--yes", action="store_true",
                    help="allow a first-run model download (mlx-whisper / faster-whisper fetch ~0.2-3 GB from "
                         "Hugging Face when the model is not cached)")
    ap.add_argument("--force", action="store_true",
                    help="overwrite --out, the kept audio file and the speakers sidecar if they already exist")
    ap.add_argument("--diarize", action="store_true",
                    help="identify speakers (deepgram or soniox only; default off) and write "
                         "<out stem>.speakers.json next to the SRT: [{start, end, speaker: S1, S2...}] in seconds. "
                         "The SRT stays plain text and blocks never span two speakers. Deepgram bills diarization "
                         "as an add-on that is NOT in the cost estimate; Soniox includes it. "
                         "openai and local engines exit 2.")
    return ap


def run(a: argparse.Namespace) -> int:
    src = C.expand_path(a.input)
    if not src.is_file():
        raise ArgError(f"input not found: {src}")
    if src.suffix.lower() == ".srt":
        raise ArgError("input is already an SRT -- skip transcription and translate it directly")
    lang = (a.lang or C.load_prefs().get("source_language") or "ko").strip()
    out = C.expand_path(a.out) if a.out else src.with_suffix(".stt.srt")
    engine = a.engine
    cloud = engine != "local"

    if a.diarize and engine not in ("deepgram", "soniox"):
        raise ArgError(f"--diarize is not supported by the {engine} engine "
                       + ("(whisper-1 returns no speaker labels)" if engine == "openai"
                          else "(local Whisper runtimes return no speaker labels)")
                       + "; use --engine deepgram or --engine soniox")
    speakers_path = out.parent / (out.stem + ".speakers.json")
    if a.diarize and speakers_path.exists() and not a.force:
        raise ArgError(f"speakers file already exists: {speakers_path} -- pass --force to overwrite it")

    if out.exists() and not a.force:
        raise ArgError(f"output file already exists: {out} -- choose another --out or pass --force to overwrite it")
    if a.keep_audio:
        kept = out.parent / (out.stem + ".audio" + (".mp3" if cloud else ".wav"))
        if kept.exists() and not a.force:
            raise ArgError(f"audio file already exists: {kept} -- pass --force to overwrite it")

    if not shutil.which("ffmpeg"):
        raise DependencyError(C.ffmpeg_missing_message())
    long_warning = C.path_length_warning(src, out)
    if long_warning:
        C.eprint(f"warning: {long_warning}")

    key = ""
    model = a.model or DEFAULT_MODELS.get(engine, "")
    runtime = compute_type = ""
    if cloud:
        key = C.get_key(engine)
        if not key:
            raise DependencyError(
                f"no {C.KEY_VARS[engine]} found. Set it with:  {C.python_cmd()} scripts/check_setup.py --set-key {engine}"
                "  (run it yourself in a terminal; never paste keys into chat)")
        if engine == "openai" and model != "whisper-1":
            raise ArgError(f"OpenAI model '{model}' is not supported: newer OpenAI STT models return no "
                           "timestamps. Use --model whisper-1, or pick deepgram/soniox/local.")
    else:
        runtime, model, compute_type = local_plan(a.model or "", a.runtime)

    dl_line, needs_dl = "", False
    if runtime in ("mlx-whisper", "faster-whisper"):
        dl_line, needs_dl = download_notice(runtime, model)

    duration = ffprobe_duration(src)
    plan = [f"engine:   {engine}", f"model:    {model}" + (f"  (runtime {runtime})" if runtime else ""),
            f"language: {lang}", f"input:    {src}  ({fmt_dur(duration)})", f"output:   {out}",
            f"cost:     {estimate_cost(engine, duration)}"]
    if a.diarize:
        plan.append(f"speakers: {speakers_path}  (diarization ON)")
        plan.append(DEEPGRAM_DIARIZE_NOTICE if engine == "deepgram" else SONIOX_DIARIZE_NOTE)
    if dl_line:
        plan.append(f"download: {dl_line}")
    if a.dry_run:
        print("DRY RUN -- nothing was extracted or sent.\n" + "\n".join(plan))
        if cloud:
            print(f"key:      {C.KEY_VARS[engine]} present")
        if needs_dl and not a.yes:
            C.eprint("the real run will refuse to start until you pass --yes to allow this download.")
            return EXIT_DEP
        return EXIT_OK

    print("\n".join(plan), file=sys.stderr)
    if needs_dl and not a.yes:
        raise DependencyError(f"{dl_line}. Re-run with --yes to allow the download.")
    out.parent.mkdir(parents=True, exist_ok=True)
    work = C.make_temp_dir()
    try:
        ext = ".mp3" if cloud else ".wav"
        # always work on an ASCII-named file inside the temp folder; --keep-audio copies it out
        audio = work / ("audio" + ext)
        extract_audio(src, audio, cloud=cloud)
        if a.keep_audio:
            shutil.copyfile(audio, out.parent / (out.stem + ".audio" + ext))
        if engine == "deepgram":
            words = deepgram_transcribe(audio, key, lang, model, a.prompt, a.diarize)
        elif engine == "openai":
            words = openai_transcribe(audio, key, lang, model, a.prompt, work)
        elif engine == "soniox":
            words = soniox_transcribe(audio, key, lang, model, a.prompt, a.diarize)
        else:
            words = local_transcribe(audio, runtime, model, lang, a.prompt, compute_type, work)
        if a.diarize:
            words = C.normalize_speakers(words)
        blocks = C.segment_words(words, lang=lang)
        if not blocks:
            raise EngineError("the engine returned no speech (empty transcript). Check the input audio and language.")
        C.write_srt(out, blocks)
        turns = C.speaker_turns(words) if a.diarize else []
        if a.diarize and turns:
            with open(speakers_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(turns, ensure_ascii=False, indent=2) + "\n")
    finally:
        C.remove_tree(work)

    if a.diarize and not turns:
        C.eprint(f"Wrote {out} (plain SRT), but the engine returned no speaker labels, so no speakers file was written.")
        raise EngineError("--diarize was requested but the response contained no speaker labels "
                          "(is the audio single-speaker, or does the account/model lack diarization?)")

    covered = blocks[-1]["end"] - blocks[0]["start"]
    print(f"Wrote {out}")
    print(f"  blocks:   {len(blocks)}  (speech span {fmt_dur(covered)}, audio {fmt_dur(duration)})")
    print(f"  engine:   {engine}   model: {model}" + (f"   runtime: {runtime}" if runtime else ""))
    print(f"  cost:     {estimate_cost(engine, duration)}")
    if a.diarize:
        print(f"  speakers: {len({t['speaker'] for t in turns})} speaker(s), {len(turns)} turn(s) -> {speakers_path}")
        print("  " + (DEEPGRAM_DIARIZE_NOTICE if engine == "deepgram" else SONIOX_DIARIZE_NOTE))
    print("  next:     review the first minute against the audio (names, English terms), then verify/translate.")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    C.setup_utf8_io()
    ap = build_parser()
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:  # argparse exits 2 already; keep the contract explicit
        return int(e.code) if isinstance(e.code, int) else EXIT_ARGS
    try:
        return run(a)
    except ArgError as e:
        C.eprint(f"error: {e}")
        return EXIT_ARGS
    except DependencyError as e:
        C.eprint(f"missing dependency: {e}")
        return EXIT_DEP
    except EngineError as e:
        C.eprint(f"engine error: {e}")
        return EXIT_API
    except (OSError, http.client.HTTPException) as e:  # last-resort net: never show a traceback
        C.eprint(f"I/O or network error: {type(e).__name__}: {str(e)[:300]}")
        return EXIT_API
    except KeyboardInterrupt:
        C.eprint("\ncancelled (Ctrl+C); temporary files were removed.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
