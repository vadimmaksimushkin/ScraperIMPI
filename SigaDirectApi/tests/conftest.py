import sys
from pathlib import Path

# The SigaDirectApi modules import each other as top-level siblings
# (e.g. `from constants import ...`), so put that directory on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
