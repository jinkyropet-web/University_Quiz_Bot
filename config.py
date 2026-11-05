try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import os

def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY") or os.urandom(32).hex()
DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME", "admin")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
COOKIE_SECURE = env_bool("COOKIE_SECURE", False)
DATABASE_PATH = os.getenv("DATABASE_PATH", "database/university.sqlite3")
UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "uploads")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "25"))
DEFAULT_QUESTION_SECONDS = int(os.getenv("DEFAULT_QUESTION_SECONDS", "30"))
PORT = int(os.getenv("PORT", "8080"))
