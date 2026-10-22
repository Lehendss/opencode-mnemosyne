#!/usr/bin/env python3
"""Validate existing bind mounts without creating directories or changing data."""

import os
from pathlib import Path
import sys


def validate_path(label, value):
    path = Path(value).expanduser().resolve()
    if len(path.parts) >= 3 and path.parts[1] == "Volumes":
        volume = Path(*path.parts[:3])
        if not os.path.ismount(str(volume)):
            raise ValueError("%s requires a mounted volume: %s" % (label, volume))
    if not path.is_dir():
        raise ValueError("%s directory is missing or inaccessible: %s" % (label, path))
    if not os.access(str(path), os.R_OK | os.W_OK | os.X_OK):
        raise ValueError("%s directory must be readable and writable: %s" % (label, path))
    return path


def main(arguments):
    if not arguments or len(arguments) % 2:
        raise ValueError("Usage: check-storage.py <label> <directory> [<label> <directory> ...]")
    for label, value in zip(arguments[::2], arguments[1::2]):
        path = validate_path(label, value)
        print("Storage available: %s (%s)" % (label, path))


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (ValueError, OSError, RuntimeError) as error:
        print("Storage check failed: %s" % error, file=sys.stderr)
        sys.exit(1)
