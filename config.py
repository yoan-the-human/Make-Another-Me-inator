import os
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent
TASKS_DIR = BASE_DIR / "tasks"
PENDING_DIR = TASKS_DIR / "pending"
WORKING_DIR = TASKS_DIR / "working"
COMPLETED_DIR = TASKS_DIR / "completed"
ARCHIVE_DIR = TASKS_DIR / "archive"

# Ensure all task directories exist
for folder in [PENDING_DIR, WORKING_DIR, COMPLETED_DIR, ARCHIVE_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

# Default Server repo root
SERVER_REPO_DIR = "/data/testapp"

# Load .env
ENV_FILE = BASE_DIR / ".env"

GIT_USERNAME = ""
GIT_PASSWORD = ""
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID = ""
PUTTY_LOG_PATH = None

if ENV_FILE.exists():
    with open(ENV_FILE, "r", encoding="utf-8", errors="ignore") as f:
        lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        
    env_dict = {}
    for line in lines:
        if "=" in line:
            k, v = line.split("=", 1)
            env_dict[k.strip().upper()] = v.strip()

    # Exact named keys or fallback to line numbers
    GIT_USERNAME = env_dict.get("USERNAME") or (lines[0].split("=", 1)[1] if len(lines) > 0 and "=" in lines[0] else "")
    GIT_PASSWORD = env_dict.get("PASSWORD") or (lines[1].split("=", 1)[1] if len(lines) > 1 and "=" in lines[1] else "")
    TELEGRAM_BOT_TOKEN = env_dict.get("TELEGRAM_BOT_TOKEN") or (lines[2].split("=", 1)[1] if len(lines) > 2 and "=" in lines[2] else "")
    TELEGRAM_CHAT_ID = env_dict.get("TELEGRAM_CHAT_ID") or (lines[3].split("=", 1)[1] if len(lines) > 3 and "=" in lines[3] else "")
    
    if "PUTTY_LOG_PATH" in env_dict:
        PUTTY_LOG_PATH = Path(env_dict["PUTTY_LOG_PATH"])
else:
    print(f"[WARNING] .env file not found at {ENV_FILE}!")

# Fallback default log file in project root if not set
if not PUTTY_LOG_PATH:
    default_log = BASE_DIR / "putty_claude.log"
    PUTTY_LOG_PATH = default_log

if __name__ == "__main__":
    print(f"Project Base: {BASE_DIR}")
    print(f"Pending dir: {PENDING_DIR}")
    print(f"Git User: {GIT_USERNAME}")
    print(f"Git Pass: {'*' * len(GIT_PASSWORD) if GIT_PASSWORD else 'NOT SET'}")
    print(f"Telegram Chat ID: {TELEGRAM_CHAT_ID}")
    print(f"PuTTY Log Path: {PUTTY_LOG_PATH}")
