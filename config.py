"""Central configuration — everything comes from environment variables."""
import os
import shutil
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

BASE = Path(__file__).resolve().parent
TMP_DIR = BASE / "tmp"; CACHE_DIR = BASE / "cache"; DATA_DIR = BASE / "data"
for d in (TMP_DIR, CACHE_DIR, DATA_DIR):
    d.mkdir(parents=True, exist_ok=True)


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name) or default)
    except ValueError:
        return default


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
MONGODB_URI = os.getenv("MONGODB_URI") or None
MONGODB_DB = os.getenv("MONGODB_DB", "clipbot")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY") or None
SHAPELESSAI_API_KEY = os.getenv("SHAPELESSAI_API_KEY") or None
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
LOCAL_BOT_API_URL = os.getenv("LOCAL_BOT_API_URL") or None
FORCE_JOIN_CHANNEL = os.getenv("FORCE_JOIN_CHANNEL") or None
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL") or None
PING_INTERVAL = _int("PING_INTERVAL", 5)
PORT = _int("PORT", 8080)
MAX_JOBS = 1
MAX_TEMP_MB = _int("MAX_TEMP_MB", 2048)
RATE_LIMIT_PER_HOUR = 10
MAX_UPLOAD_MIN = _int("MAX_UPLOAD_MIN", 20)   # longest user-uploaded video we add subtitles to
MAX_ANALYZE_MIN = 180          # longest video we will transcribe fully (highlights / search)

GROQ_API_KEY = os.getenv("GROQ_API_KEY") or None
# LLM falls back to the Groq key (same provider by default)
LLM_API_KEY = os.getenv("LLM_API_KEY") or GROQ_API_KEY
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")

# Render "Secret Files" are mounted read-only; yt-dlp wants to write to its
# cookie jar, so work on a writable copy.
COOKIES_FILE = None
_cookies_src = os.getenv("COOKIES_FILE") or None
if _cookies_src and Path(_cookies_src).is_file():
    try:
        _dst = DATA_DIR / "cookies.txt"
        shutil.copyfile(_cookies_src, _dst)
        COOKIES_FILE = str(_dst)
    except OSError:
        COOKIES_FILE = _cookies_src

DEFAULT_QUALITY = os.getenv("DEFAULT_QUALITY", "best")
DEFAULT_DELIVERY = os.getenv("DEFAULT_DELIVERY", "auto")
DB_PATH = DATA_DIR / "bot.db"
