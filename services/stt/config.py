# config.py — speech-to-text model settings, all env-overridable.
#
# STT runs locally with faster-whisper on a CTranslate2 build of a Whisper model.
# Default is N-ATLAS Yoruba ASR (NCAIR1/Yoruba-ASR, Whisper-small fine-tune). It
# is built from the copy already in the Hugging Face cache, never downloaded.
import os

from services.env_loader import load_env

load_env()

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Known models: name -> (CT2 dir under ROOT, HF repo it is built from or None).
MODELS = {
    "natlas": ("natlas-yoruba-asr-ct2", "NCAIR1/Yoruba-ASR"),
    "legacy": ("whisper-small-yoruba-ct2", None),   # steja/whisper-small-yoruba
}

# "natlas" | "legacy" | a path to any CTranslate2 Whisper directory.
MODEL = os.environ.get("STT_MODEL", "natlas")

# Whisper language token. N-ATLAS was trained on "yo"; "en" measurably worse.
LANGUAGE = os.environ.get("STT_LANGUAGE", "yo")

# Weight type for the offline conversion (runtime compute type is picked by the
# app from the device: float16 on GPU, int8 on CPU).
CONVERT_QUANT = os.environ.get("STT_CONVERT_QUANT", "float16")

# Hugging Face cache root (where NCAIR1/Yoruba-ASR already lives).
HF_CACHE = os.environ.get(
    "HF_HUB_CACHE",
    os.path.join(os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface")),
                 "hub"))
