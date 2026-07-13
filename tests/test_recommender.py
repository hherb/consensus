"""Tests for MethodRecommender — LLM-based method classification."""

import json
import time

import pytest
from consensus.methods.recommender import MethodRecommendation


class TestMethodRecommendation:
    def test_creates_recommendation(self):
        rec = MethodRecommendation(
            method_name="ach",
            display_name="Analysis of Competing Hypotheses",
            confidence=0.85,
            reasoning="Topic involves testing claims against evidence.",
            fit_factors=["hypothesis testing", "evidence evaluation"],
        )
        assert rec.method_name == "ach"
        assert rec.confidence == 0.85
        assert len(rec.fit_factors) == 2

    def test_to_dict(self):
        rec = MethodRecommendation(
            method_name="delphi",
            display_name="Delphi Method",
            confidence=0.7,
            reasoning="Forecasting question.",
            fit_factors=["quantitative"],
        )
        d = rec.to_dict()
        assert d["method_name"] == "delphi"
        assert d["display_name"] == "Delphi Method"
        assert d["confidence"] == 0.7
        assert d["reasoning"] == "Forecasting question."
        assert d["fit_factors"] == ["quantitative"]
        assert d["capability_warning"] == ""


from consensus.methods.recommender import (
    MethodRecommender, _EXCLUDED_METHODS, ANSWER_TYPES,
)


class TestMethodRecommender:
    def test_build_catalog_excludes_triage_and_open(self):
        catalog = [
            {"name": "ach", "display_name": "ACH", "description": "...", "phases": []},
            {"name": "triage", "display_name": "Guided Triage", "description": "...", "phases": []},
            {"name": "open_discussion", "display_name": "Open Discussion", "description": "...", "phases": []},
            {"name": "delphi", "display_name": "Delphi", "description": "...", "phases": []},
        ]
        recommender = MethodRecommender()
        filtered = recommender._filter_catalog(catalog)
        names = [m["name"] for m in filtered]
        assert "ach" in names
        assert "delphi" in names
        assert "triage" not in names
        assert "open_discussion" not in names

    def test_build_system_prompt_contains_methods(self):
        catalog = [
            {"name": "ach", "display_name": "ACH", "description": "Hypothesis testing", "phases": []},
        ]
        recommender = MethodRecommender()
        prompt = recommender._build_system_prompt(catalog)
        assert "ACH" in prompt
        assert "Hypothesis testing" in prompt
        assert "methodology expert" in prompt.lower()

    def test_build_user_prompt_contains_topic_and_type(self):
        recommender = MethodRecommender()
        prompt = recommender._build_user_prompt(
            topic="Will AI replace doctors?",
            answer_type="Forecast or estimate something",
            additional_context="",
        )
        assert "Will AI replace doctors?" in prompt
        assert "Forecast" in prompt

    def test_build_user_prompt_includes_additional_context(self):
        recommender = MethodRecommender()
        prompt = recommender._build_user_prompt(
            topic="test",
            answer_type="test",
            additional_context="The uncertainty is quantifiable.",
        )
        assert "quantifiable" in prompt


class TestParseRecommendations:
    def test_parses_valid_json(self):
        recommender = MethodRecommender()
        raw = json.dumps({"recommendations": [
            {"method_name": "ach", "display_name": "ACH",
             "confidence": 0.9, "reasoning": "Good fit.",
             "fit_factors": ["hypothesis"]},
            {"method_name": "delphi", "display_name": "Delphi",
             "confidence": 0.6, "reasoning": "Possible.",
             "fit_factors": ["forecasting"]},
        ]})
        results = recommender._parse_response(raw, num_recommendations=3)
        assert len(results) == 2
        assert results[0].method_name == "ach"
        assert results[0].confidence == 0.9

    def test_parses_json_in_code_fence(self):
        recommender = MethodRecommender()
        raw = '```json\n{"recommendations": [{"method_name": "delphi", "display_name": "Delphi", "confidence": 0.8, "reasoning": "r", "fit_factors": []}]}\n```'
        results = recommender._parse_response(raw, num_recommendations=3)
        assert len(results) == 1
        assert results[0].method_name == "delphi"

    def test_returns_fallback_on_invalid_json(self):
        recommender = MethodRecommender()
        results = recommender._parse_response("not json at all", num_recommendations=3)
        assert len(results) == 1
        assert results[0].method_name == "open_discussion"
        assert results[0].fit_factors == ["fallback"]

    def test_returns_fallback_on_missing_recommendations_key(self):
        recommender = MethodRecommender()
        results = recommender._parse_response('{"other": []}', num_recommendations=3)
        assert len(results) == 1
        assert results[0].method_name == "open_discussion"

    def test_clamps_confidence(self):
        recommender = MethodRecommender()
        raw = json.dumps({"recommendations": [
            {"method_name": "ach", "display_name": "ACH",
             "confidence": 1.5, "reasoning": "r", "fit_factors": []},
        ]})
        results = recommender._parse_response(raw, num_recommendations=3)
        assert results[0].confidence == 1.0


from unittest.mock import AsyncMock, MagicMock
from consensus.methods.recommender import _fallback_recommendation


class TestRecommendAsync:
    @pytest.mark.asyncio
    async def test_recommend_returns_parsed_results(self):
        recommender = MethodRecommender()
        mock_response = MagicMock()
        mock_response.content = json.dumps({"recommendations": [
            {"method_name": "ach", "display_name": "ACH",
             "confidence": 0.9, "reasoning": "Good.", "fit_factors": ["test"]},
        ]})
        mock_client = MagicMock()
        mock_client.complete = AsyncMock(return_value=mock_response)
        mock_client.close = AsyncMock()

        catalog = [{"name": "ach", "display_name": "ACH", "description": "d", "phases": []}]
        provider = {"model": "test-model"}

        results = await recommender.recommend(
            "test topic", "test type", catalog, mock_client, provider,
        )
        assert len(results) == 1
        assert results[0].method_name == "ach"
        mock_client.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_recommend_returns_fallback_on_error(self):
        recommender = MethodRecommender()
        mock_client = MagicMock()
        mock_client.complete = AsyncMock(side_effect=Exception("API down"))
        mock_client.close = AsyncMock()

        catalog = [{"name": "ach", "display_name": "ACH", "description": "d", "phases": []}]
        provider = {"model": "test-model"}

        results = await recommender.recommend(
            "test topic", "test type", catalog, mock_client, provider,
        )
        assert len(results) == 1
        assert results[0].method_name == "open_discussion"


def _insert_pricing_row(tmp_db, model_id: str, supported: str) -> None:
    """Insert a model_pricing row with a given supported_parameters value."""
    tmp_db.conn.execute(
        "INSERT INTO model_pricing (model_id, prompt_cost, completion_cost,"
        " last_updated, input_modalities, context_length,"
        " supported_parameters) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (model_id, 0.0, 0.0, time.time(), "text", 8192, supported),
    )
    tmp_db.conn.commit()


def _mock_client_with_recommendations(recs: list[dict]) -> MagicMock:
    """Build a mock AIClient whose complete() returns the given recommendations."""
    mock_response = MagicMock()
    mock_response.content = json.dumps({"recommendations": recs})
    mock_client = MagicMock()
    mock_client.complete = AsyncMock(return_value=mock_response)
    mock_client.close = AsyncMock()
    return mock_client


class TestRecommendMethodCapabilityDownranking:
    """``recommend_method`` (consensus.app_discussion_setup) down-ranks
    structured-output methods when a panel model is known to lack tool
    support (#23 recommender-awareness follow-up)."""

    @pytest.mark.asyncio
    async def test_downranks_structured_method_behind_compatible_ones(
            self, tmp_db, monkeypatch):
        from consensus.app_discussion_setup import recommend_method

        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_pricing_row(tmp_db, "test/no-tools", "temperature,top_p")

        mock_client = _mock_client_with_recommendations([
            {"method_name": "delphi", "display_name": "Delphi",
             "confidence": 0.9, "reasoning": "Forecasting fit.",
             "fit_factors": []},
            {"method_name": "premortem", "display_name": "Premortem",
             "confidence": 0.6, "reasoning": "Risk fit.", "fit_factors": []},
        ])

        recs = await recommend_method(
            "topic", "type", mock_client, {"model": "m"},
            db=tmp_db, panel_models=[("test/no-tools", "")],
        )

        # premortem (no structured phases) must be ranked ahead of the
        # down-ranked delphi (structured, incompatible panel model), even
        # though the LLM ranked delphi first by confidence.
        assert [r["method_name"] for r in recs] == ["premortem", "delphi"]
        delphi_rec = recs[1]
        assert "tool-capable" in delphi_rec["reasoning"]
        assert "test/no-tools" in delphi_rec["reasoning"]
        assert "Forecasting fit." in delphi_rec["reasoning"]
        # The UI needs a machine-readable flag: the down-ranked rec's
        # confidence badge would otherwise contradict its ordering
        # (PR #39 review).
        assert "test/no-tools" in delphi_rec["capability_warning"]
        assert recs[0]["capability_warning"] == ""

    @pytest.mark.asyncio
    async def test_never_drops_incompatible_recommendation(
            self, tmp_db, monkeypatch):
        from consensus.app_discussion_setup import recommend_method

        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_pricing_row(tmp_db, "test/no-tools", "temperature,top_p")

        mock_client = _mock_client_with_recommendations([
            {"method_name": "delphi", "display_name": "Delphi",
             "confidence": 0.9, "reasoning": "Forecasting fit.",
             "fit_factors": []},
        ])

        recs = await recommend_method(
            "topic", "type", mock_client, {"model": "m"},
            db=tmp_db, panel_models=[("test/no-tools", "")],
        )

        assert len(recs) == 1
        assert recs[0]["method_name"] == "delphi"

    @pytest.mark.asyncio
    async def test_unknown_capability_does_not_downrank(
            self, tmp_db, monkeypatch):
        from consensus.app_discussion_setup import recommend_method

        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        # No pricing row at all -> supports_tools() returns None (unknown)

        mock_client = _mock_client_with_recommendations([
            {"method_name": "delphi", "display_name": "Delphi",
             "confidence": 0.9, "reasoning": "Forecasting fit.",
             "fit_factors": []},
            {"method_name": "premortem", "display_name": "Premortem",
             "confidence": 0.6, "reasoning": "Risk fit.", "fit_factors": []},
        ])

        recs = await recommend_method(
            "topic", "type", mock_client, {"model": "m"},
            db=tmp_db, panel_models=[("test/unknown-model", "")],
        )

        assert [r["method_name"] for r in recs] == ["delphi", "premortem"]
        assert "tool-capable" not in recs[0]["reasoning"]

    @pytest.mark.asyncio
    async def test_no_panel_info_leaves_order_unchanged(
            self, tmp_db, monkeypatch):
        from consensus.app_discussion_setup import recommend_method

        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_pricing_row(tmp_db, "test/no-tools", "temperature,top_p")

        mock_client = _mock_client_with_recommendations([
            {"method_name": "delphi", "display_name": "Delphi",
             "confidence": 0.9, "reasoning": "Forecasting fit.",
             "fit_factors": []},
            {"method_name": "premortem", "display_name": "Premortem",
             "confidence": 0.6, "reasoning": "Risk fit.", "fit_factors": []},
        ])

        # No panel_models and no db supplied at all -> unchanged behavior.
        recs = await recommend_method(
            "topic", "type", mock_client, {"model": "m"},
        )

        assert [r["method_name"] for r in recs] == ["delphi", "premortem"]

    @pytest.mark.asyncio
    async def test_compatible_panel_leaves_order_unchanged(
            self, tmp_db, monkeypatch):
        from consensus.app_discussion_setup import recommend_method

        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_pricing_row(tmp_db, "test/tool-model", "temperature,tools")

        mock_client = _mock_client_with_recommendations([
            {"method_name": "delphi", "display_name": "Delphi",
             "confidence": 0.9, "reasoning": "Forecasting fit.",
             "fit_factors": []},
            {"method_name": "premortem", "display_name": "Premortem",
             "confidence": 0.6, "reasoning": "Risk fit.", "fit_factors": []},
        ])

        recs = await recommend_method(
            "topic", "type", mock_client, {"model": "m"},
            db=tmp_db, panel_models=[("test/tool-model", "")],
        )

        assert [r["method_name"] for r in recs] == ["delphi", "premortem"]
