"""--diarize: opt-in speaker diarization (deepgram / soniox), sidecar format, never-merge-across-speakers."""
import contextlib
import io
import json
import pathlib
import random
import re
import tempfile
import unittest
import urllib.parse
from unittest import mock

import support
from support import FAKE_KEY, FakeNet, FakeResp, temp_home

import _common as C
import context_pack as CP
import transcribe as T

NOTICE = ("Deepgram diarization is billed as an add-on; not included in the cost estimate above "
          "— check https://deepgram.com/pricing")


def dg_response():
    """Two Korean utterances; speaker ints 1,0 (ids are arbitrary: labels must be renumbered S1, S2)."""
    def w(word, a, b, spk):
        return {"word": word, "start": a, "end": b, "speaker": spk, "speaker_confidence": 0.9, "confidence": 0.9}
    return {"results": {"utterances": [
        {"start": 0.0, "end": 2.0, "transcript": "안녕하세요 여러분", "words": [
            w("안녕하세요", 0.0, 1.0, 1), w("여러분", 1.1, 2.0, 1)]},
        {"start": 2.2, "end": 4.0, "transcript": "네 반갑습니다", "words": [
            w("네", 2.2, 2.6, 0), w("반갑습니다", 2.7, 4.0, 0)]},
        {"start": 4.1, "end": 5.5, "transcript": "다시 제가 말합니다", "words": [
            w("다시", 4.1, 4.5, 1), w("제가", 4.6, 4.9, 1), w("말합니다", 5.0, 5.5, 1)]},
    ]}}


def tok(text, a, b, spk=None):
    t = {"text": text, "start_ms": a, "end_ms": b, "confidence": 0.9}
    if spk is not None:
        t["speaker"] = spk
    return t


class HelperTests(unittest.TestCase):
    def test_normalize_in_order_of_first_appearance(self):
        words = [{"start": 0, "end": 1, "text": "a", "speaker": 5}, {"start": 1, "end": 2, "text": "b", "speaker": 2},
                 {"start": 2, "end": 3, "text": "c", "speaker": 5}, {"start": 3, "end": 4, "text": "d"}]
        out = C.normalize_speakers(words)
        self.assertEqual([w.get("speaker") for w in out], ["S1", "S2", "S1", None])
        self.assertEqual(words[0]["speaker"], 5)                       # input untouched
        strs = C.normalize_speakers([{"start": 0, "end": 1, "text": "x", "speaker": "2"},
                                     {"start": 1, "end": 2, "text": "y", "speaker": "1"}])
        self.assertEqual([w["speaker"] for w in strs], ["S1", "S2"])
        self.assertEqual(C.normalize_speakers([{"start": 0, "end": 1, "text": "z", "speaker": 0}])[0]["speaker"], "S1")

    def test_turns_merge_consecutive_words(self):
        words = C.normalize_speakers([
            {"start": 0.0, "end": 1.0, "text": "a", "speaker": 3}, {"start": 1.5, "end": 2.25, "text": "b", "speaker": 3},
            {"start": 2.5, "end": 3.0, "text": "c", "speaker": 1}, {"start": 3.0, "end": 3.75, "text": "d", "speaker": 3}])
        self.assertEqual(C.speaker_turns(words), [
            {"start": 0.0, "end": 2.25, "speaker": "S1"}, {"start": 2.5, "end": 3.0, "speaker": "S2"},
            {"start": 3.0, "end": 3.75, "speaker": "S1"}])

    def test_turns_unlabelled_words_join_previous_speaker_and_none_gives_empty(self):
        words = [{"start": 0, "end": 1, "text": "a", "speaker": "S1"}, {"start": 1, "end": 2, "text": "b"}]
        self.assertEqual(C.speaker_turns(words), [{"start": 0.0, "end": 2.0, "speaker": "S1"}])
        self.assertEqual(C.speaker_turns([{"start": 0, "end": 1, "text": "a"}]), [])

    def test_segmenter_never_mixes_speakers_property(self):
        rng = random.Random(1234)
        for trial in range(200):
            words, t, spk, owner = [], 0.0, 0, {}
            for i in range(rng.randint(1, 60)):
                if rng.random() < 0.3:
                    spk = rng.randint(0, 3)
                dur = rng.uniform(0.1, 0.6)
                text = f"w{i}" + rng.choice(["", "", ".", ","])
                words.append({"start": t, "end": t + dur, "text": text, "speaker": f"S{spk + 1}"})
                owner[f"w{i}"] = f"S{spk + 1}"
                t += dur + rng.choice([0.0, 0.05, 0.1, 0.9])
            for lang in ("ko", "en"):
                blocks = C.segment_words(words, lang=lang)
                for b in blocks:
                    speakers = {owner[re.sub(r"[.,]$", "", tok_)] for tok_ in b["text"].split()}
                    self.assertEqual(len(speakers), 1, (trial, b))
                self.assertEqual(" ".join(b["text"] for b in blocks).replace("\n", " ").count("w"), len(words))

    def test_segmenter_without_speaker_labels_still_merges(self):
        words = [{"start": i * 0.4, "end": i * 0.4 + 0.35, "text": f"단어{i}"} for i in range(4)]
        self.assertEqual(len(C.segment_words(words, lang="ko")), 1)
        labelled = [dict(w, speaker="S1" if i < 2 else "S2") for i, w in enumerate(words)]
        self.assertEqual(len(C.segment_words(labelled, lang="ko")), 2)



class DeepgramDiarizeTests(unittest.TestCase):
    def call(self, diarize):
        net = FakeNet(lambda m, u, h, d: FakeResp(dg_response()))
        with tempfile.TemporaryDirectory() as td, mock.patch("urllib.request.urlopen", net):
            audio = pathlib.Path(td) / "a.mp3"
            audio.write_bytes(b"x")
            words = T.deepgram_transcribe(audio, FAKE_KEY, "ko", "nova-3", "", diarize)
        return net.calls[0]["url"], words

    def test_param_and_speaker_field(self):
        url, words = self.call(True)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(q["diarize_model"], ["latest"])           # official recommended param
        self.assertNotIn("diarize", q)                              # deprecated boolean; setting both is rejected
        self.assertEqual([w["speaker"] for w in words], [1, 1, 0, 0, 1, 1, 1])
        self.assertEqual(q["utterances"], ["true"])

    def test_off_by_default_sends_no_diarize_and_keeps_no_speakers(self):
        url, words = self.call(False)
        self.assertNotIn("diariz", url)
        self.assertTrue(all("speaker" not in w for w in words))

    def test_keyterm_retry_keeps_diarize(self):
        calls = []

        def handler(m, u, h, d):
            calls.append(u)
            if "keyterm=" in u:
                import urllib.error
                return urllib.error.HTTPError(u, 400, "bad", {}, io.BytesIO(b"x"))
            return FakeResp(dg_response())
        with tempfile.TemporaryDirectory() as td, mock.patch("urllib.request.urlopen", FakeNet(handler)), \
                contextlib.redirect_stderr(io.StringIO()):
            a = pathlib.Path(td) / "a.mp3"
            a.write_bytes(b"x")
            T.deepgram_transcribe(a, FAKE_KEY, "ko", "nova-3", "Acme Studio", True)
        self.assertEqual(len(calls), 2)
        self.assertIn("diarize_model=latest", calls[1])
        self.assertNotIn("keyterm=", calls[1])


class SonioxDiarizeTests(unittest.TestCase):
    TOKENS = [tok("안", 0, 100, "1"), tok("녕", 100, 200, "1"), tok(" 네", 300, 500, "2"), tok("!", 500, 550, "2"),
              tok(" 좋", 700, 800, "1"), tok("아", 800, 900, "1")]

    def test_subword_run_is_split_at_a_speaker_change_and_carries_speaker(self):
        # no whitespace/gap/punctuation between the tokens: only the speaker differs
        toks = [tok("가", 0, 100, "1"), tok("나", 100, 200, "2")]
        words = T.soniox_tokens_to_words(toks, "ko")
        self.assertEqual([(w["text"], w["speaker"]) for w in words], [("가", "1"), ("나", "2")])
        no_labels = T.soniox_tokens_to_words([tok("가", 0, 100), tok("나", 100, 200)], "ko")
        self.assertEqual(len(no_labels), 1)
        self.assertNotIn("speaker", no_labels[0])

    def flow(self, diarize, tokens):
        seen = {}

        def handler(m, u, h, d):
            if m == "POST" and u.endswith("/v1/files"):
                return FakeResp({"id": "f1"})
            if m == "POST" and u.endswith("/v1/transcriptions"):
                seen["payload"] = json.loads(d)
                return FakeResp({"id": "t1"})
            if m == "GET" and u.endswith("/transcript"):
                return FakeResp({"id": "t1", "text": "", "tokens": tokens})
            if m == "GET":
                return FakeResp({"id": "t1", "status": "completed"})
            return FakeResp(b"", 204)
        with tempfile.TemporaryDirectory() as td, mock.patch("urllib.request.urlopen", FakeNet(handler)), \
                mock.patch.object(T.time, "sleep"), contextlib.redirect_stderr(io.StringIO()):
            a = pathlib.Path(td) / "a.mp3"
            a.write_bytes(b"x")
            words = T.soniox_transcribe(a, FAKE_KEY, "ko", "stt-async-v5", "", diarize)
        return seen["payload"], words

    def test_payload_flag_and_speakers(self):
        payload, words = self.flow(True, self.TOKENS)
        self.assertIs(payload["enable_speaker_diarization"], True)
        self.assertEqual([w["text"] for w in words], ["안녕", "네!", "좋아"])
        self.assertEqual([w["speaker"] for w in words], ["1", "2", "1"])

    def test_off_by_default(self):
        payload, _ = self.flow(False, self.TOKENS)
        self.assertNotIn("enable_speaker_diarization", payload)


class DiarizeEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.td.name)
        self.src = self.dir / "video.mp4"
        self.src.write_bytes(b"fakevideo")
        self.out = self.dir / "video.srt"
        self.side = self.dir / "video.speakers.json"

    def tearDown(self):
        self.td.cleanup()

    def run_main(self, argv, net=None, env=None):
        env = {"DEEPGRAM_API_KEY": FAKE_KEY, "SONIOX_API_KEY": FAKE_KEY} if env is None else env
        net = net or FakeNet(lambda *a: FakeResp(dg_response()))
        with temp_home(**env), mock.patch.object(T.shutil, "which", return_value="/usr/bin/x"), \
                mock.patch.object(T, "ffprobe_duration", return_value=600.0), \
                mock.patch.object(T, "extract_audio", side_effect=lambda s, d, cloud: d.write_bytes(b"a")), \
                mock.patch.object(T.time, "sleep"), mock.patch("urllib.request.urlopen", net), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = T.main(argv)
        return code, out.getvalue(), err.getvalue(), net

    def args(self, *extra, engine="deepgram"):
        return ["--input", str(self.src), "--engine", engine, "--lang", "ko", "--out", str(self.out), *extra]

    def test_deepgram_sidecar_srt_and_notice(self):
        code, out, err, net = self.run_main(self.args("--diarize"))
        self.assertEqual(code, 0, err)
        turns = json.loads(self.side.read_text(encoding="utf-8"))
        self.assertEqual(turns, [{"start": 0.0, "end": 2.0, "speaker": "S1"}, {"start": 2.2, "end": 4.0, "speaker": "S2"},
                                 {"start": 4.1, "end": 5.5, "speaker": "S1"}])
        blocks = C.read_srt(self.out)
        self.assertEqual([b["text"] for b in blocks], ["안녕하세요 여러분", "네 반갑습니다", "다시 제가 말합니다"])
        raw = self.out.read_text(encoding="utf-8")
        self.assertNotRegex(raw, r"\bS[12]\b")                                  # plain text, no speaker prefixes
        self.assertIn(NOTICE, out)
        self.assertIn("diarize_model=latest", net.calls[0]["url"])
        self.assertNotIn(FAKE_KEY, out + err)

    def test_sidecar_is_readable_by_context_pack(self):
        self.run_main(self.args("--diarize"))
        loaded = CP.load_sidecar(self.side)
        self.assertEqual([t["speaker"] for t in loaded], ["S1", "S2", "S1"])
        self.assertEqual(CP.speaker_for({"start": 2.3, "end": 3.9}, loaded), "S2")

    def test_blocks_split_at_speaker_changes(self):
        code, *_ = self.run_main(self.args("--diarize"))
        self.assertEqual(len(C.read_srt(self.out)), 3)      # one block per speaker turn

    def test_default_off_no_sidecar_no_notice(self):
        code, out, err, net = self.run_main(self.args())
        self.assertEqual(code, 0)
        self.assertFalse(self.side.exists())
        self.assertNotIn("diarization", out + err)
        self.assertNotIn("diariz", net.calls[0]["url"])

    def test_dry_run_prints_notice_and_writes_nothing(self):
        code, out, err, net = self.run_main(self.args("--diarize", "--dry-run"))
        self.assertEqual(code, 0)
        self.assertIn(NOTICE, out)
        self.assertIn("video.speakers.json", out)
        self.assertEqual(net.calls, [])
        self.assertFalse(self.out.exists())
        self.assertFalse(self.side.exists())

    def test_notice_only_for_deepgram(self):
        code, out, err, net = self.run_main(self.args("--diarize", "--dry-run", engine="soniox"))
        self.assertEqual(code, 0)
        self.assertNotIn("billed as an add-on", out)
        self.assertIn("no extra cost", out)

    def test_soniox_end_to_end(self):
        toks = [tok("안", 0, 100, "7"), tok("녕", 100, 200, "7"), tok(".", 200, 250, "7"),
                tok(" 네", 400, 600, "3"), tok(".", 600, 650, "3")]

        def handler(m, u, h, d):
            if m == "POST" and u.endswith("/v1/files"):
                return FakeResp({"id": "f1"})
            if m == "POST":
                return FakeResp({"id": "t1"})
            if m == "GET" and u.endswith("/transcript"):
                return FakeResp({"tokens": toks})
            if m == "GET":
                return FakeResp({"status": "completed"})
            return FakeResp(b"", 204)
        code, out, err, net = self.run_main(self.args("--diarize", engine="soniox"), net=FakeNet(handler))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(self.side.read_text(encoding="utf-8")),
                         [{"start": 0.0, "end": 0.25, "speaker": "S1"}, {"start": 0.4, "end": 0.65, "speaker": "S2"}])
        self.assertEqual([b["text"] for b in C.read_srt(self.out)], ["안녕.", "네."])
        self.assertNotIn("billed as an add-on", out)

    def test_unsupported_engines_exit_2(self):
        for engine in ("openai", "local"):
            for extra in ((), ("--dry-run",)):
                code, out, err, net = self.run_main(self.args("--diarize", *extra, engine=engine), env={})
                self.assertEqual(code, 2, (engine, extra, err))
                self.assertIn("--diarize is not supported by the " + engine, err)
                self.assertIn("deepgram or --engine soniox", err)
                self.assertEqual(net.calls, [])
                self.assertFalse(self.out.exists())

    def test_force_governs_sidecar_overwrite(self):
        self.side.write_text("[]", encoding="utf-8")
        code, out, err, net = self.run_main(self.args("--diarize"))
        self.assertEqual(code, 2)
        self.assertIn("speakers file already exists", err)
        self.assertEqual(net.calls, [])                                        # refused before any API call/cost
        self.assertFalse(self.out.exists())
        code, *_ = self.run_main(self.args("--diarize", "--force"))
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(self.side.read_text(encoding="utf-8"))), 3)

    def test_existing_sidecar_ignored_without_diarize(self):
        self.side.write_text("[]", encoding="utf-8")
        code, *_ = self.run_main(self.args())
        self.assertEqual(code, 0)
        self.assertEqual(self.side.read_text(encoding="utf-8"), "[]")

    def test_no_speaker_labels_returned_exits_4_keeps_srt_writes_no_sidecar(self):
        data = {"results": {"utterances": [{"start": 0, "end": 1, "transcript": "네",
                                             "words": [{"word": "네", "start": 0, "end": 1}]}]}}
        code, out, err, net = self.run_main(self.args("--diarize"), net=FakeNet(lambda *a: FakeResp(data)))
        self.assertEqual(code, 4)
        self.assertIn("no speaker labels", err)
        self.assertTrue(self.out.exists())
        self.assertFalse(self.side.exists())

    def test_help_documents_flag(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit):
                T.build_parser().parse_args(["--help"])
        text = " ".join(out.getvalue().split())
        self.assertIn("--diarize", text)
        self.assertIn("speakers.json", text)
        self.assertIn("NOT in the cost estimate", text)


if __name__ == "__main__":
    unittest.main()
