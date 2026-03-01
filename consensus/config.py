"""Platform-aware configuration and paths."""

import os
import sys


def get_data_dir() -> str:
    """Get the platform-appropriate data directory."""
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    elif sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:
        base = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    path = os.path.join(base, "consensus")
    os.makedirs(path, exist_ok=True)
    return path


def get_db_path() -> str:
    """Get the default database file path."""
    return os.path.join(get_data_dir(), "consensus.db")
