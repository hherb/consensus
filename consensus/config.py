"""Platform-aware configuration and paths."""

import os
import stat
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


def get_env_path() -> str:
    """Return the path to ~/.consensus/.env."""
    d = os.path.expanduser("~/.consensus")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, ".env")


def load_env() -> None:
    """Load ~/.consensus/.env into os.environ (existing vars take precedence)."""
    from dotenv import load_dotenv
    path = get_env_path()
    if os.path.isfile(path):
        load_dotenv(path, override=False)


def _read_env_lines(path: str) -> list[str]:
    """Read .env file lines, returning empty list if file doesn't exist."""
    if not os.path.isfile(path):
        return []
    with open(path, "r") as f:
        return f.readlines()


def _write_env(path: str, lines: list[str]) -> None:
    """Write lines to .env file with restrictive permissions."""
    with open(path, "w") as f:
        f.writelines(lines)
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:
        pass  # Windows may not support chmod


def save_api_key(env_var: str, key_value: str) -> None:
    """Write or update an API key in ~/.consensus/.env.

    Also sets the key in os.environ so it takes effect immediately.
    """
    if not env_var or not key_value:
        return
    path = get_env_path()
    lines = _read_env_lines(path)

    # Replace existing line or append
    prefix = f"{env_var}="
    found = False
    new_lines: list[str] = []
    for line in lines:
        if line.startswith(prefix):
            new_lines.append(f"{env_var}={key_value}\n")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{env_var}={key_value}\n")

    _write_env(path, new_lines)
    os.environ[env_var] = key_value


def remove_api_key(env_var: str) -> None:
    """Remove an API key from ~/.consensus/.env and os.environ."""
    if not env_var:
        return
    path = get_env_path()
    lines = _read_env_lines(path)

    prefix = f"{env_var}="
    new_lines = [line for line in lines if not line.startswith(prefix)]
    _write_env(path, new_lines)
    os.environ.pop(env_var, None)


def has_api_key(env_var: str) -> bool:
    """Return True if the env var is set (from .env or real environment)."""
    if not env_var:
        return False
    return bool(os.environ.get(env_var, ""))
