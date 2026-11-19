import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

BASE = Path(__file__).resolve().parent
TMP_DIR = BASE / "tmp"; CACHE_DIR = BASE / "cache"; DATA_DIR = BASE / "data"
for d in (TMP_DIR, CACHE_DIR, DATA_DIR): d.mkdir(parents=True, exist_ok=True)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
LOCAL_BOT_API_URL = os.getenv("LOCAL_BOT_API_URL") or None
FORCE_JOIN_CHANNEL = os.getenv("FORCE_JOIN_CHANNEL") or None
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL") or None
PING_INTERVAL = int(os.getenv("PING_INTERVAL", "5"))
PORT = int(os.getenv("PORT", "8080"))
MAX_JOBS = int(os.getenv("MAX_JOBS", "2"))
MAX_TEMP_MB = int(os.getenv("MAX_TEMP_MB", "2048"))
RATE_LIMIT_PER_HOUR = 10

GROQ_API_KEY = os.getenv("GROQ_API_KEY") or None
LLM_API_KEY = os.getenv("LLM_API_KEY") or None
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
COOKIES_FILE = os.getenv("COOKIES_FILE") or None

DEFAULT_QUALITY = os.getenv("DEFAULT_QUALITY", "best")
DEFAULT_DELIVERY = os.getenv("DEFAULT_DELIVERY", "auto")
DB_PATH = DATA_DIR / "bot.db"