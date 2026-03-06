"""Tests for consensus.config — API key management, path resolution."""

import os
import stat

import pytest

from consensus.config import (
    save_api_key, remove_api_key, has_api_key,
    _read_env_lines, _write_env,
)


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    """Set up a temporary .env file path and patch get_env_path."""
    env_path = str(tmp_path / ".env")
    monkeypatch.setattr("consensus.config.get_env_path", lambda: env_path)
    return env_path


class TestSaveApiKey:
    def test_creates_new_key(self, env_file, monkeypatch):
        save_api_key("MY_KEY", "sk-123")
        lines = _read_env_lines(env_file)
        assert any("MY_KEY=sk-123" in line for line in lines)
        assert os.environ.get("MY_KEY") == "sk-123"
        monkeypatch.delenv("MY_KEY", raising=False)

    def test_updates_existing_key(self, env_file, monkeypatch):
        save_api_key("MY_KEY", "old-value")
        save_api_key("MY_KEY", "new-value")
        lines = _read_env_lines(env_file)
        key_lines = [l for l in lines if l.startswith("MY_KEY=")]
        assert len(key_lines) == 1
        assert "new-value" in key_lines[0]
        monkeypatch.delenv("MY_KEY", raising=False)

    def test_preserves_other_keys(self, env_file, monkeypatch):
        save_api_key("KEY_A", "aaa")
        save_api_key("KEY_B", "bbb")
        save_api_key("KEY_A", "updated")
        lines = _read_env_lines(env_file)
        assert any("KEY_B=bbb" in l for l in lines)
        monkeypatch.delenv("KEY_A", raising=False)
        monkeypatch.delenv("KEY_B", raising=False)

    def test_empty_inputs_noop(self, env_file):
        save_api_key("", "value")
        save_api_key("KEY", "")
        assert not os.path.exists(env_file)


class TestRemoveApiKey:
    def test_remove_existing_key(self, env_file, monkeypatch):
        save_api_key("RM_KEY", "secret")
        remove_api_key("RM_KEY")
        lines = _read_env_lines(env_file)
        assert not any("RM_KEY=" in l for l in lines)
        assert "RM_KEY" not in os.environ

    def test_remove_nonexistent_key_noop(self, env_file):
        # Should not raise
        remove_api_key("DOES_NOT_EXIST")

    def test_empty_env_var_noop(self, env_file):
        remove_api_key("")


class TestHasApiKey:
    def test_has_key_true(self, monkeypatch):
        monkeypatch.setenv("TEST_HAS_KEY", "value")
        assert has_api_key("TEST_HAS_KEY") is True

    def test_has_key_false(self):
        assert has_api_key("NONEXISTENT_KEY_XYZ") is False

    def test_empty_var_name(self):
        assert has_api_key("") is False


class TestFilePermissions:
    def test_env_file_permissions(self, env_file, monkeypatch):
        save_api_key("PERM_KEY", "secret")
        mode = os.stat(env_file).st_mode
        # Should be owner read/write only (0600)
        assert mode & stat.S_IRUSR  # owner read
        assert mode & stat.S_IWUSR  # owner write
        assert not (mode & stat.S_IRGRP)  # no group read
        assert not (mode & stat.S_IROTH)  # no other read
        monkeypatch.delenv("PERM_KEY", raising=False)
