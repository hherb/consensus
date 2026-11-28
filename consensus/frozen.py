"""Helpers for running inside a PyInstaller-frozen app bundle."""

import sys
from pathlib import Path


def is_frozen() -> bool:
    """Return True when running from a PyInstaller bundle."""
    return getattr(sys, "frozen", False) is True


def worker_command() -> tuple[str, list[str]]:
    """Return (executable, args) that launch the sandbox worker.

    In a frozen app the worker is the sibling ``consensus-worker``
    executable bundled next to the GUI binary (Contents/MacOS on macOS);
    otherwise it is ``python -P <path>/sandbox_worker.py``. The worker
    imports nothing from ``consensus``, so running it by path works from an
    uninstalled checkout too, and ``-P`` keeps its own directory (the
    ``consensus`` package) off its ``sys.path`` (issue #166).
    """
    if is_frozen():
        worker = Path(sys.executable).resolve().parent / "consensus-worker"
        return str(worker), []
    return sys.executable, ["-P", str(Path(__file__).with_name("sandbox_worker.py"))]
