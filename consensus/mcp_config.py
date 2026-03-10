"""Load MCP server definitions from JSON or TOML config files.

Supports two config file formats:
- JSON: {"mcp_servers": [{...}, ...]}
- TOML: [[mcp_servers]] sections

Each entry requires at minimum 'name' and either 'command' (stdio) or
'url' (HTTP transport).  Optional fields: 'description', 'args', 'env',
'enabled', 'headers'.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Required: at least one of these must be present
_STDIO_REQUIRED = {"name", "command"}
_HTTP_REQUIRED = {"name", "url"}

_DEFAULTS: dict[str, Any] = {
    "description": "",
    "args": [],
    "env": {},
    "enabled": True,
    "transport": "stdio",
}


def load_mcp_config(path: str) -> list[dict]:
    """Load MCP server definitions from a JSON or TOML file.

    Args:
        path: Path to the config file (.json or .toml).

    Returns:
        List of server definition dicts, each with at least
        'name' and 'command' (stdio) or 'url' (http).
        Returns empty list on any error.
    """
    if not os.path.isfile(path):
        return []

    try:
        raw = _read_config_file(path)
    except Exception:
        logger.warning("Failed to read MCP config from %s", path, exc_info=True)
        return []

    entries = raw.get("mcp_servers", [])
    if not isinstance(entries, list):
        logger.warning("mcp_servers in %s is not a list", path)
        return []

    servers: list[dict] = []
    for entry in entries:
        server = _normalize_entry(entry)
        if server is not None:
            servers.append(server)

    logger.info("Loaded %d MCP server(s) from %s", len(servers), path)
    return servers


def _read_config_file(path: str) -> dict:
    """Read and parse a JSON or TOML config file."""
    with open(path, "r") as f:
        text = f.read()

    if path.endswith(".toml"):
        import tomllib
        return tomllib.loads(text)

    return json.loads(text)


def _normalize_entry(entry: dict) -> dict | None:
    """Validate and normalize a single server entry.

    Returns None if required fields are missing.
    """
    if not isinstance(entry, dict):
        return None

    # Detect transport type
    has_url = bool(entry.get("url"))
    has_command = bool(entry.get("command"))
    has_name = bool(entry.get("name"))

    if not has_name:
        logger.warning("Skipping MCP config entry without 'name': %s", entry)
        return None

    if not has_url and not has_command:
        logger.warning("Skipping MCP config entry '%s': needs 'command' or 'url'",
                       entry.get("name"))
        return None

    server: dict[str, Any] = {}
    server["name"] = entry["name"]
    server["description"] = entry.get("description", _DEFAULTS["description"])
    server["enabled"] = entry.get("enabled", _DEFAULTS["enabled"])

    if has_url:
        server["transport"] = "http"
        server["url"] = entry["url"]
        server["command"] = entry.get("command", "")
        server["args"] = entry.get("args", [])
        server["env"] = entry.get("env", {})
        server["headers"] = entry.get("headers", {})
    else:
        server["transport"] = "stdio"
        server["command"] = entry["command"]
        server["args"] = entry.get("args", _DEFAULTS["args"])
        server["env"] = entry.get("env", _DEFAULTS["env"])

    return server


def merge_config_servers(
    config_servers: list[dict],
    db_servers: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Compare config-file servers against DB and determine adds/updates.

    Args:
        config_servers: Parsed server definitions from config file.
        db_servers: Existing server records from the database.

    Returns:
        Tuple of (servers_to_add, servers_to_update).
        servers_to_update includes the DB 'id' field.
    """
    db_by_name: dict[str, dict] = {s["name"]: s for s in db_servers}
    to_add: list[dict] = []
    to_update: list[dict] = []

    for cs in config_servers:
        name = cs["name"]
        existing = db_by_name.get(name)

        if existing is None:
            to_add.append(cs)
        else:
            # Check if any fields differ
            changed = False
            for key in ("command", "description", "args", "env", "enabled",
                        "transport", "url", "headers"):
                config_val = cs.get(key)
                db_val = existing.get(key)
                # DB stores enabled as int
                if key == "enabled":
                    db_val = bool(db_val)
                if config_val is not None and config_val != db_val:
                    changed = True
                    break

            if changed:
                update = dict(cs)
                update["id"] = existing["id"]
                to_update.append(update)

    return to_add, to_update


def get_default_config_paths() -> list[str]:
    """Return the default config file search paths in priority order.

    Checks:
    1. CONSENSUS_MCP_CONFIG env var
    2. ./mcp_servers.json (current working directory)
    3. ~/.consensus/mcp_servers.json
    4. ~/.consensus/mcp_servers.toml
    5. Platform data dir (e.g. ~/Library/Application Support/consensus/mcp_servers.json)
    """
    paths: list[str] = []

    env_path = os.environ.get("CONSENSUS_MCP_CONFIG")
    if env_path:
        paths.append(env_path)

    paths.append(os.path.join(os.getcwd(), "mcp_servers.json"))

    home_dir = os.path.expanduser("~/.consensus")
    paths.append(os.path.join(home_dir, "mcp_servers.json"))
    paths.append(os.path.join(home_dir, "mcp_servers.toml"))

    from .config import get_data_dir
    data_dir = get_data_dir()
    paths.append(os.path.join(data_dir, "mcp_servers.json"))
    paths.append(os.path.join(data_dir, "mcp_servers.toml"))

    return paths


def load_and_merge_config(db) -> dict:
    """Load config from default paths and merge into the database.

    Args:
        db: Database instance with add_mcp_server / get_mcp_servers / update_mcp_server.

    Returns:
        Dict with 'added' and 'updated' counts.
    """
    config_servers: list[dict] = []
    for path in get_default_config_paths():
        loaded = load_mcp_config(path)
        if loaded:
            config_servers.extend(loaded)
            break  # Use only the first file found

    if not config_servers:
        return {"added": 0, "updated": 0}

    db_servers = db.get_mcp_servers()
    to_add, to_update = merge_config_servers(config_servers, db_servers)

    for server in to_add:
        db.add_mcp_server(
            name=server["name"],
            description=server.get("description", ""),
            command=server.get("command", ""),
            args=server.get("args", []),
            env=server.get("env", {}),
            enabled=server.get("enabled", True),
            transport=server.get("transport", "stdio"),
            url=server.get("url", ""),
            headers=server.get("headers", {}),
        )

    for server in to_update:
        update_fields = {k: v for k, v in server.items()
                         if k not in ("id", "name")}
        db.update_mcp_server(server["id"], **update_fields)

    added = len(to_add)
    updated = len(to_update)
    if added or updated:
        logger.info("MCP config sync: %d added, %d updated", added, updated)
    return {"added": added, "updated": updated}
