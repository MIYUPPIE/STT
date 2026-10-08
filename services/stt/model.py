# model.py — pick the STT model directory live_caption loads. Deterministic file
# logic only (no model weights are touched here except by the offline converter):
#
#   resolve()  ->  STT_MODEL ("natlas" default) -> its CTranslate2 dir.
#                  If the N-ATLAS CT2 dir is missing but NCAIR1/Yoruba-ASR is in
#                  the Hugging Face cache, convert it once, offline (no download).
#                  If neither exists, fall back to the legacy model with a note.
#
#   python3 -m services.stt.model            # show what would load
#   python3 -m services.stt.model --convert  # (re)build natlas-yoruba-asr-ct2
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass

from . import config

CT2_FILES = ("model.bin", "config.json", "tokenizer.json", "preprocessor_config.json")


@dataclass
class Resolved:
    path: str            # CTranslate2 directory to hand to WhisperModel
    name: str            # "natlas" | "legacy" | "custom"
    note: str            # human-readable: how it was chosen / built


def is_ct2_dir(path: str) -> bool:
    return all(os.path.isfile(os.path.join(path, f)) for f in CT2_FILES)


def cached_snapshot(repo: str, cache: str | None = None) -> str | None:
    """Local snapshot dir of an HF repo already in the cache, or None. Requires
    config.json + weights; never touches the network."""
    cache = cache or config.HF_CACHE
    base = os.path.join(cache, "models--" + repo.replace("/", "--"))
    ref = os.path.join(base, "refs", "main")
    try:
        with open(ref) as f:
            snap = os.path.join(base, "snapshots", f.read().strip())
    except OSError:
        snaps = os.path.join(base, "snapshots")
        if not os.path.isdir(snaps) or not os.listdir(snaps):
            return None
        snap = os.path.join(snaps, sorted(os.listdir(snaps))[-1])
    has_weights = any(os.path.isfile(os.path.join(snap, w))
                      for w in ("pytorch_model.bin", "model.safetensors"))
    if os.path.isfile(os.path.join(snap, "config.json")) and has_weights:
        return snap
    return None


def convert_offline(snapshot: str, out_dir: str, quant: str | None = None) -> None:
    """Convert a cached HF Whisper snapshot to CTranslate2 with the network
    disabled. Builds into a temp dir and renames, so a crash never leaves a
    half-written model where the app would load it."""
    quant = quant or config.CONVERT_QUANT
    tmp = out_dir + ".partial"
    shutil.rmtree(tmp, ignore_errors=True)
    env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    cmd = [sys.executable, "-m", "ctranslate2.converters.transformers",
           "--model", snapshot, "--output_dir", tmp, "--quantization", quant,
           "--copy_files", "preprocessor_config.json", "tokenizer.json"]
    r = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if r.returncode != 0 or not is_ct2_dir(tmp):
        shutil.rmtree(tmp, ignore_errors=True)
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-3:]
        raise RuntimeError("conversion failed: " + " | ".join(tail))
    shutil.rmtree(out_dir, ignore_errors=True)
    os.replace(tmp, out_dir)


def resolve(model: str | None = None, root: str | None = None,
            auto_convert: bool = True, convert=convert_offline,
            snapshot_fn=cached_snapshot) -> Resolved:
    model = model or config.MODEL
    root = root or config.ROOT

    if model not in config.MODELS:                         # explicit path
        path = model if os.path.isabs(model) else os.path.join(root, model)
        if not is_ct2_dir(path):
            raise FileNotFoundError(f"STT_MODEL={model!r} is not a CTranslate2 "
                                    f"Whisper dir (needs {', '.join(CT2_FILES)})")
        return Resolved(path, "custom", f"STT_MODEL path {path}")

    sub, repo = config.MODELS[model]
    path = os.path.join(root, sub)
    if is_ct2_dir(path):
        return Resolved(path, model, f"{model} ({sub})")

    if repo and auto_convert:
        snap = snapshot_fn(repo)
        if snap:
            convert(snap, path)
            return Resolved(path, model,
                            f"{model} (built offline from cached {repo})")

    if model != "legacy":                                  # graceful fallback
        legacy = os.path.join(root, config.MODELS["legacy"][0])
        if is_ct2_dir(legacy):
            return Resolved(legacy, "legacy",
                            f"legacy fallback: {model} not available "
                            f"({repo} not in HF cache)")
    raise FileNotFoundError(f"no STT model found for STT_MODEL={model!r}")


def _main(argv):
    if "--convert" in argv:
        name = "natlas"
        sub, repo = config.MODELS[name]
        snap = cached_snapshot(repo)
        if not snap:
            print(f"{repo} is not in the HF cache ({config.HF_CACHE}).",
                  file=sys.stderr)
            return 1
        out = os.path.join(config.ROOT, sub)
        print(f"converting {snap}\n        -> {out} (offline, {config.CONVERT_QUANT})")
        convert_offline(snap, out)
        print("done")
        return 0
    r = resolve(auto_convert=False)
    print(f"STT model: {r.note}\n  path: {r.path}\n  language: {config.LANGUAGE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
