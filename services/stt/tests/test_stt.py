# test_stt.py — gate tests for STT model selection. Deterministic, no model
# weights loaded, no network, temp dirs only. <1s.
# Run: python3 -m unittest services.stt.tests.test_stt -v
import os
import tempfile
import unittest

from services.stt import config
from services.stt.model import CT2_FILES, cached_snapshot, is_ct2_dir, resolve


def make_ct2(path):
    os.makedirs(path, exist_ok=True)
    for f in CT2_FILES:
        open(os.path.join(path, f), "w").close()


def make_hf_cache(cache, repo, weights="pytorch_model.bin", with_ref=True):
    base = os.path.join(cache, "models--" + repo.replace("/", "--"))
    snap = os.path.join(base, "snapshots", "abc123")
    os.makedirs(snap)
    open(os.path.join(snap, "config.json"), "w").close()
    if weights:
        open(os.path.join(snap, weights), "w").close()
    if with_ref:
        os.makedirs(os.path.join(base, "refs"))
        with open(os.path.join(base, "refs", "main"), "w") as f:
            f.write("abc123\n")
    return snap


class TestCt2Dir(unittest.TestCase):
    def test_complete_vs_partial(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(is_ct2_dir(d))
            make_ct2(d)
            self.assertTrue(is_ct2_dir(d))
            os.remove(os.path.join(d, "model.bin"))
            self.assertFalse(is_ct2_dir(d))


class TestCachedSnapshot(unittest.TestCase):
    def test_finds_snapshot_via_ref(self):
        with tempfile.TemporaryDirectory() as c:
            snap = make_hf_cache(c, "NCAIR1/Yoruba-ASR")
            self.assertEqual(cached_snapshot("NCAIR1/Yoruba-ASR", c), snap)

    def test_finds_snapshot_without_ref(self):
        with tempfile.TemporaryDirectory() as c:
            snap = make_hf_cache(c, "NCAIR1/Yoruba-ASR", with_ref=False)
            self.assertEqual(cached_snapshot("NCAIR1/Yoruba-ASR", c), snap)

    def test_safetensors_ok(self):
        with tempfile.TemporaryDirectory() as c:
            make_hf_cache(c, "NCAIR1/Yoruba-ASR", weights="model.safetensors")
            self.assertIsNotNone(cached_snapshot("NCAIR1/Yoruba-ASR", c))

    def test_missing_or_weightless(self):
        with tempfile.TemporaryDirectory() as c:
            self.assertIsNone(cached_snapshot("NCAIR1/Yoruba-ASR", c))
            make_hf_cache(c, "NCAIR1/Yoruba-ASR", weights=None)
            self.assertIsNone(cached_snapshot("NCAIR1/Yoruba-ASR", c))


class TestResolve(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.natlas = os.path.join(self.root, config.MODELS["natlas"][0])
        self.legacy = os.path.join(self.root, config.MODELS["legacy"][0])

    def tearDown(self):
        self.tmp.cleanup()

    def no_convert(self, *a):
        self.fail("convert must not run")

    def test_default_is_natlas_yoruba(self):
        self.assertEqual(config.MODELS["natlas"][1], "NCAIR1/Yoruba-ASR")
        if "STT_MODEL" not in os.environ:
            self.assertEqual(config.MODEL, "natlas")
        if "STT_LANGUAGE" not in os.environ:
            self.assertEqual(config.LANGUAGE, "yo")

    def test_natlas_present(self):
        make_ct2(self.natlas)
        make_ct2(self.legacy)
        r = resolve("natlas", self.root, convert=self.no_convert)
        self.assertEqual((r.name, r.path), ("natlas", self.natlas))

    def test_natlas_built_offline_from_cache(self):
        calls = []

        def fake_convert(snap, out):
            calls.append((snap, out))
            make_ct2(out)

        r = resolve("natlas", self.root, convert=fake_convert,
                    snapshot_fn=lambda repo: "/cache/snap")
        self.assertEqual(calls, [("/cache/snap", self.natlas)])
        self.assertEqual(r.name, "natlas")
        self.assertIn("offline", r.note)

    def test_falls_back_to_legacy(self):
        make_ct2(self.legacy)
        r = resolve("natlas", self.root, convert=self.no_convert,
                    snapshot_fn=lambda repo: None)
        self.assertEqual((r.name, r.path), ("legacy", self.legacy))
        self.assertIn("fallback", r.note)

    def test_no_auto_convert_falls_back(self):
        make_ct2(self.legacy)
        r = resolve("natlas", self.root, auto_convert=False,
                    convert=self.no_convert, snapshot_fn=lambda repo: "/x")
        self.assertEqual(r.name, "legacy")

    def test_nothing_available(self):
        with self.assertRaises(FileNotFoundError):
            resolve("natlas", self.root, convert=self.no_convert,
                    snapshot_fn=lambda repo: None)

    def test_explicit_path(self):
        custom = os.path.join(self.root, "my-ct2")
        make_ct2(custom)
        r = resolve("my-ct2", self.root)
        self.assertEqual((r.name, r.path), ("custom", custom))
        with self.assertRaises(FileNotFoundError):
            resolve("not-a-model", self.root)


if __name__ == "__main__":
    unittest.main()
