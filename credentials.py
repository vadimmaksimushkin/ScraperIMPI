"""Example credentials.py for future use"""

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

USERNAME: str = os.environ.get("USERNAME", "")
PASSWORD: str = os.environ.get("PASSWORD", "")
