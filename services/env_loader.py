# env_loader.py — shared glue: load the repo-root .env into os.environ.
#
# No python-dotenv dependency. Each service config calls load_env() at import so
# XAI_API_KEY / YARN_API_KEY are available whether the process was started with
# them exported or not. Never overwrites an already-set variable.
import os

_ROOT_ENV = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))


def load_env(path: str = _ROOT_ENV) -> None:
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass
