"""Tests for prompt template CRUD operations."""

import pytest

from consensus.database import Database


class TestPrompts:
    def test_save_and_get_prompt(self, tmp_db):
        pid = tmp_db.save_prompt(
            None, "Custom Prompt", "moderator", "ai", "custom_task", "Do {thing}",
        )
        p = tmp_db.get_prompt(pid)
        assert p["name"] == "Custom Prompt"
        assert p["content"] == "Do {thing}"

    def test_update_prompt(self, tmp_db):
        pid = tmp_db.save_prompt(None, "P1", "moderator", "ai", "task1", "Content1")
        tmp_db.save_prompt(pid, "P1-Updated", "moderator", "ai", "task1", "Content2")
        p = tmp_db.get_prompt(pid)
        assert p["name"] == "P1-Updated"
        assert p["content"] == "Content2"

    def test_delete_prompt(self, tmp_db):
        pid = tmp_db.save_prompt(None, "ToDelete", "participant", "ai", "t", "c")
        tmp_db.delete_prompt(pid)
        assert tmp_db.get_prompt(pid) is None

    def test_get_prompt_by_task(self, tmp_db):
        tmp_db.save_prompt(None, "P", "participant", "human", "custom_unique_task", "Help text")
        row = tmp_db.get_prompt_by_task("participant", "human", "custom_unique_task")
        assert row is not None
        assert row["content"] == "Help text"

    def test_get_prompt_by_task_not_found(self, tmp_db):
        row = tmp_db.get_prompt_by_task("participant", "human", "nonexistent_task_xyz")
        assert row is None

    def test_get_prompts_filter_by_role(self, tmp_db):
        prompts = tmp_db.get_prompts(role="moderator")
        assert all(p["role"] == "moderator" for p in prompts)
        assert len(prompts) > 0

    def test_get_prompts_filter_by_target(self, tmp_db):
        prompts = tmp_db.get_prompts(target="ai")
        assert all(p["target"] == "ai" for p in prompts)

    def test_get_prompts_filter_by_role_and_task(self, tmp_db):
        prompts = tmp_db.get_prompts(role="moderator", task="system")
        assert all(p["role"] == "moderator" and p["task"] == "system" for p in prompts)
