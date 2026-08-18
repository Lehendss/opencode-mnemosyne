import sys

import requests


try:
    response = requests.get("http://127.0.0.1:8789/health", timeout=5)
    response.raise_for_status()
except Exception:
    sys.exit(1)
