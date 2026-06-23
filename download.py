# download.py
from huggingface_hub import snapshot_download

path = snapshot_download(
    repo_id="steja/whisper-small-yoruba",
    local_dir="./whisper-small-yoruba",
)
print("Downloaded to:", path)