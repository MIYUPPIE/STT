# test_tts.py — gate tests. Deterministic, no network, free, <2s.
# Run: python3 -m unittest services.tts.tests.test_tts -v
import io
import os
import tempfile
import unittest
import wave

import numpy as np

from services.tts.contract import SynthRequest
from services.tts.engine import YarnTTS, clean_text, chunk_text
from services.tts.speak import to_int16, save_wav


def wav_bytes(seconds=1.0, sr=44100, level=0.1, ch=1):
    """Build an in-memory PCM16 WAV the engine can decode (stands in for the
    API). Stdlib only, so the suite has no soundfile dependency."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(sr)
        samples = np.full(int(seconds * sr) * ch, int(level * 32767), dtype="<i2")
        w.writeframes(samples.tobytes())
    return buf.getvalue()


def fake(status, body):
    calls = []

    def transport(url, payload, timeout, headers=None):
        calls.append((url, payload, timeout, headers))
        return status, body

    transport.calls = calls
    return transport


def tts(transport, **kw):
    return YarnTTS(api_key="test-key", transport=transport, **kw)


class TestCleanText(unittest.TestCase):
    def test_strips_timestamp_prefix(self):
        self.assertEqual(clean_text("[11:57:03] Báwo ni"), "Báwo ni")

    def test_keeps_diacritics(self):
        self.assertEqual(clean_text("  Mo fẹ́ lọ sí ọjà.  "), "Mo fẹ́ lọ sí ọjà.")

    def test_collapses_whitespace(self):
        self.assertEqual(clean_text("Báwo    ni\n o"), "Báwo ni o")

    def test_empty(self):
        self.assertEqual(clean_text("   "), "")


class TestChunkText(unittest.TestCase):
    def test_splits_sentences(self):
        self.assertEqual(chunk_text("Mo wà dáadáa. Báwo ni?"),
                         ["Mo wà dáadáa.", "Báwo ni?"])

    def test_short_single_sentence_whole(self):
        self.assertEqual(chunk_text("Ẹ ṣé gan-an ni."), ["Ẹ ṣé gan-an ni."])

    def test_no_punctuation_one_chunk(self):
        self.assertEqual(chunk_text("bawo ni o se wa"), ["bawo ni o se wa"])

    def test_empty(self):
        self.assertEqual(chunk_text("   "), [])

    def test_long_sentence_split_at_clauses(self):
        # one long sentence, commas -> multiple chunks, content preserved
        s = "apa kiini gigun, apa keji gigun pelu, ati apa keta to tun gun"
        chunks = chunk_text(s, max_chunk=20)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(" ".join(chunks), s)              # nothing lost


class TestSynth(unittest.TestCase):
    def test_success_decodes_audio(self):
        t = fake(200, wav_bytes(seconds=1.0, sr=44100))
        res = tts(t).synth(SynthRequest(text="[11:00:00] Báwo ni"))
        self.assertTrue(res.ok)
        self.assertEqual(res.sample_rate, 44100)
        self.assertEqual(len(res.audio), 44100)
        self.assertAlmostEqual(res.duration_s, 1.0, places=2)

    def test_sends_auth_voice_and_clean_text(self):
        t = fake(200, wav_bytes())
        tts(t, voice="Idera").synth(SynthRequest(text="[09:00:00] Ẹ ku àárọ̀"))
        url, payload, _, headers = t.calls[0]
        self.assertEqual(url, "https://yarngpt.ai/api/v1/tts")
        self.assertEqual(payload["voice"], "Idera")
        self.assertEqual(payload["response_format"], "wav")
        self.assertEqual(payload["text"], "Ẹ ku àárọ̀")          # timestamp stripped
        self.assertEqual(headers["Authorization"], "Bearer test-key")

    def test_truncates_to_max_chars(self):
        t = fake(200, wav_bytes())
        tts(t, max_chars=10).synth(SynthRequest(text="x" * 50))
        self.assertEqual(len(t.calls[0][1]["text"]), 10)

    def test_empty_input_skips_call(self):
        t = fake(200, wav_bytes())
        res = tts(t).synth(SynthRequest(text="   "))
        self.assertTrue(res.ok)
        self.assertEqual(len(res.audio), 0)
        self.assertEqual(len(t.calls), 0)

    def test_missing_key(self):
        res = YarnTTS(api_key="", transport=fake(200, wav_bytes())).synth(
            SynthRequest(text="Báwo ni"))
        self.assertFalse(res.ok)
        self.assertIn("YARN_API_KEY", res.error)

    def test_http_error_is_graceful(self):
        res = tts(fake(401, "invalid api key")).synth(SynthRequest(text="hi"))
        self.assertFalse(res.ok)
        self.assertEqual(len(res.audio), 0)
        self.assertEqual(res.error, "invalid api key")

    def test_connection_error_is_graceful(self):
        res = tts(fake(0, "connection refused")).synth(SynthRequest(text="hi"))
        self.assertFalse(res.ok)
        self.assertEqual(res.error, "connection refused")

    def test_undecodable_body_is_graceful(self):
        res = tts(fake(200, b"this is not audio")).synth(SynthRequest(text="hi"))
        self.assertFalse(res.ok)
        self.assertIn("decode failed", res.error)


class TestAudioIO(unittest.TestCase):
    def test_to_int16_clips(self):
        out = to_int16(np.array([-2.0, 0.0, 2.0], dtype="float32"))
        self.assertEqual(out.dtype, np.int16)
        self.assertEqual(out[0], -32767)
        self.assertEqual(out[2], 32767)

    def test_save_wav_roundtrip(self):
        wav = (np.sin(np.linspace(0, 6.28, 8000)) * 0.2).astype("float32")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.wav")
            save_wav(path, wav, 8000)
            with wave.open(path, "rb") as w:    # stdlib read-back, no soundfile
                self.assertEqual(w.getframerate(), 8000)
                self.assertEqual(w.getnframes(), 8000)


if __name__ == "__main__":
    unittest.main()
