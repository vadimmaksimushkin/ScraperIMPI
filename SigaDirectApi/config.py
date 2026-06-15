import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

API_PREFIX = "/api/v1"

# On-demand archives are large (40-200 MB), regenerable, and TTL'd, so the
# FHS-correct home for them is /var/cache. Override with an env var in prod
# (e.g. a mounted /data volume in a container).
ARCHIVE_DIR = Path(
    os.getenv("SIGA_ARCHIVE_DIR", "/var/cache/siga-api/todays-archives")
)

# SQLite lives in the project root by default (handy while the schema is still
# in flux); point it at /var/lib/... or a volume in prod.
DB_PATH = Path(os.getenv("SIGA_DB_PATH", str(PROJECT_ROOT / "siga.db")))

# How long a cached archive stays downloadable before the sweep deletes it.
ARCHIVE_TTL_HOURS = float(os.getenv("SIGA_ARCHIVE_TTL_HOURS", "1"))

# How often the background cleanup sweep runs.
CLEANUP_INTERVAL_SECONDS = float(os.getenv("SIGA_CLEANUP_INTERVAL_SECONDS", "900"))
