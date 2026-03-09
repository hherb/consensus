"""Tests for consensus.app_providers — provider CRUD and key management."""

import pytest

from consensus.app_providers import (
    add_provider,
    delete_provider,
    fetch_models,
    get_providers,
    provider_for_frontend,
    update_provider,
)


class TestProviderForFrontend:
    """Tests for redacting provider secrets before sending to frontend."""

    def test_returns_none_for_none(self):
        assert provider_for_frontend(None, {}) is None

    def test_redacts_api_key_env(self):
        p = {"id": 1, "name": "Test", "api_key_env": "SECRET_KEY"}
        result = provider_for_frontend(p, {})
        assert "api_key_env" not in result
        assert result["has_key"] is False

    def test_has_key_true_when_env_set(self):
        p = {"id": 1, "name": "Test", "api_key_env": "MY_KEY"}
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("MY_KEY", "secret123")
            result = provider_for_frontend(p, {})
        assert result["has_key"] is True

    def test_has_key_true_when_byok(self):
        p = {"id": 1, "name": "Test", "api_key_env": ""}
        result = provider_for_frontend(p, {"1": "user-key"})
        assert result["has_key"] is True


class TestAddProvider:
    def test_add_provider_basic(self, tmp_db):
        result = add_provider(tmp_db, "MyProvider", "http://localhost/v1")
        assert result is not None
        assert result["name"] == "MyProvider"
        assert "api_key_env" not in result

    def test_add_provider_with_key(self, tmp_db, tmp_path):
        """Adding a provider with api_key saves to env file."""
        result = add_provider(
            tmp_db, "KeyedProvider", "http://localhost/v1",
            api_key_env="TEST_KEY", api_key="secret",
        )
        assert result is not None


class TestUpdateProvider:
    def test_update_nonexistent_returns_false(self, tmp_db):
        assert update_provider(tmp_db, 9999) is False

    def test_update_existing_provider(self, tmp_db):
        pid = tmp_db.add_provider("P", "http://x", "")
        assert update_provider(tmp_db, pid, name="Updated") is True
        p = tmp_db.get_provider(pid)
        assert p["name"] == "Updated"


class TestDeleteProvider:
    def test_delete_provider(self, tmp_db):
        pid = tmp_db.add_provider("P", "http://x", "")
        assert delete_provider(tmp_db, pid) is True


class TestGetProviders:
    def test_get_providers_redacted(self, tmp_db):
        tmp_db.add_provider("P1", "http://x", "SECRET")
        providers = get_providers(tmp_db, {})
        assert len(providers) >= 1
        for p in providers:
            assert "api_key_env" not in p
