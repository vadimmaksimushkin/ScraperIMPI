import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

API_PREFIX = "/api/v1"

# ERRROR PRONE, may require root access
ARCHIVE_DIR = Path(
    # os.getenv("SIGA_ARCHIVE_DIR", "/var/cache/siga-api/todays-archives")
    os.getenv("SIGA_DB_PATH", str(PROJECT_ROOT / "todays-archives"))
)

DB_PATH = Path(os.getenv("SIGA_DB_PATH", str(PROJECT_ROOT / "siga.db")))

ARCHIVE_TTL_HOURS = float(os.getenv("SIGA_ARCHIVE_TTL_HOURS", "1"))

cleanup_interval_minutes = str(60*15)
CLEANUP_INTERVAL_SECONDS = float(os.getenv("SIGA_CLEANUP_INTERVAL_SECONDS", cleanup_interval_minutes))
