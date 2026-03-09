"""Tests for provider CRUD operations."""

from unittest.mock import patch

import pytest

from consensus.database import Database


class TestProviders:
    def test_add_and_get(self, tmp_db):
        pid = tmp_db.add_provider("OpenAI", "https://api.openai.com/v1", "OPENAI_KEY")
        p = tmp_db.get_provider(pid)
        assert p["name"] == "OpenAI"
        assert p["base_url"] == "https://api.openai.com/v1"
        assert p["api_key_env"] == "OPENAI_KEY"

    def test_get_providers_returns_all(self, tmp_db):
        tmp_db.add_provider("A", "http://a", "")
        tmp_db.add_provider("B", "http://b", "")
        providers = tmp_db.get_providers()
        names = {p["name"] for p in providers}
        assert "A" in names and "B" in names

    def test_update_provider(self, tmp_db, sample_provider):
        tmp_db.update_provider(sample_provider, name="Updated", base_url="http://new")
        p = tmp_db.get_provider(sample_provider)
        assert p["name"] == "Updated"
        assert p["base_url"] == "http://new"

    def test_delete_provider(self, tmp_db, sample_provider):
        tmp_db.delete_provider(sample_provider)
        assert tmp_db.get_provider(sample_provider) is None

    def test_get_nonexistent_provider(self, tmp_db):
        assert tmp_db.get_provider(99999) is None


class TestMigrateProviders:
    def test_literal_key_migrated_to_env_var(self, tmp_db):
        """Literal API keys in api_key_env should be migrated to .env file."""
        literal_key = "sk-ant-abc123-some-long-literal-key-with-dashes"
        pid = tmp_db.add_provider("Anthropic", "https://api.anthropic.com/v1", literal_key)
        with patch("consensus.config.save_api_key") as mock_save:
            # Trigger re-import-style migration
            tmp_db._migrate_providers()
            mock_save.assert_called_once_with("ANTHROPIC_API_KEY", literal_key)
        p = tmp_db.get_provider(pid)
        assert p["api_key_env"] == "ANTHROPIC_API_KEY"

    def test_env_var_name_not_migrated(self, tmp_db):
        """Proper env var names (UPPER_SNAKE_CASE, short) should not be migrated."""
        pid = tmp_db.add_provider("OpenAI", "https://api.openai.com/v1", "OPENAI_API_KEY")
        with patch("consensus.config.save_api_key") as mock_save:
            tmp_db._migrate_providers()
            mock_save.assert_not_called()
        p = tmp_db.get_provider(pid)
        assert p["api_key_env"] == "OPENAI_API_KEY"

    def test_duplicate_api_key_suffix_handled(self, tmp_db):
        """Provider named 'X API_KEY' should not produce X_API_KEY_API_KEY."""
        literal_key = "sk-lowercase-key-that-triggers-migration"
        pid = tmp_db.add_provider("My API_KEY", "http://example.com", literal_key)
        with patch("consensus.config.save_api_key") as mock_save:
            tmp_db._migrate_providers()
            env_var = mock_save.call_args[0][0]
            assert not env_var.endswith("_API_KEY_API_KEY")
            assert env_var.endswith("_API_KEY")
