"""Tests for provider CRUD operations."""

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
