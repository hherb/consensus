"""Tests for PyInstaller frozen-mode support (consensus.frozen + tools_python)."""

import sys
from pathlib import Path

from consensus import frozen


def test_is_frozen_false_by_default():
    assert frozen.is_frozen() is False


def test_worker_command_unfrozen():
    exe, args = frozen.worker_command()
    assert exe == sys.executable
    assert args == ["-m", "consensus.sandbox_worker"]


def test_is_frozen_true_when_sys_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert frozen.is_frozen() is True


def test_worker_command_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    exe, args = frozen.worker_command()
    assert Path(exe).name == "consensus-worker"
    assert Path(exe).parent == Path(sys.executable).resolve().parent
    assert args == []


async def test_install_package_refused_when_frozen(monkeypatch):
    from consensus.tools_python import _install_package_handler

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    result = await _install_package_handler(
        {"package_name": "numpy", "reason": "testing"}, None, None
    )
    assert result.is_error
    assert "not available" in result.content
    assert result.metadata == {"status": "unavailable_frozen"}
