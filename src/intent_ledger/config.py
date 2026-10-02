import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ["SECRET_KEY"]
DATA_DIR = Path(os.environ.get("DATA_DIR", PROJECT_ROOT / "data")).resolve()
TRUSTED_PROXIES = int(os.environ.get("TRUSTED_PROXIES") or 0)
