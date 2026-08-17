import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def main():
    path = Path("/tmp/worker-health.json")
    if not path.exists():
        return 1
    data = json.loads(path.read_text(encoding="utf-8"))
    timestamp = datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00"))
    fresh = (datetime.now(timezone.utc) - timestamp).total_seconds() < 60
    return 0 if fresh and data.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
