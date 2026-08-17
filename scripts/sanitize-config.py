import json
import re
import sys


SENSITIVE_KEY = re.compile(
    r"^(?:api[_-]?key|authorization|cookie|credential|password|passwd|private[_-]?key|secret|token)$",
    re.IGNORECASE,
)


def sanitize(value):
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if SENSITIVE_KEY.match(key) else sanitize(child)
            for key, child in value.items()
        }
    return value


def main():
    source, destination = sys.argv[1:3]
    with open(source, "r", encoding="utf-8") as handle:
        config = json.load(handle)
    with open(destination, "w", encoding="utf-8") as handle:
        json.dump(sanitize(config), handle, ensure_ascii=False, indent=2)
        handle.write("\n")


if __name__ == "__main__":
    main()
