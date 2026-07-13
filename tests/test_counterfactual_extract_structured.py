"""Structured-output conversion of the counterfactual_extract phase (#23).

The forced submit_claims tool replaces free-text numbered-list/CONCLUSION:
parsing for tool-capable models; the free-text path (``process_response``)
remains intact for models that fall back to it after exhausting the
structured-output retry budget. This is a moderator-only turn phase
(``get_turn_order`` always returns just the moderator id) and the
``MAX_EXTRACTION_ATTEMPTS`` give-up logic (``extraction_attempts`` /
``extraction_failed`` / ``should_advance``) is shared by both paths.
"""

from consensus.methods.phases.counterfactual_extract import (
    CLAIM_MIN_LENGTH,
    CLAIMS_TOOL_PARAMETERS,
    ExtractClaimsHandler,
    validate_claims_payload,
)
from consensus.models import Discussion, Entity, EntityType

VALID_CLAIMS = [
    "Personal cars contribute significantly to urban pollution",
    "Public transit can fully replace personal car usage",
    "Car bans would reduce traffic fatalities substantially",
]

PAYLOAD = {
    "claims": VALID_CLAIMS,
    "preliminary_conclusion": "Cars should be banned from city centres.",
}


def _moderator(eid: int = 100, name: str = "Moderator") -> Entity:
    return Entity(id=eid, name=name, entity_type=EntityType.AI)


def _discussion(**state) -> Discussion:
    disc = Discussion(topic="Should cities ban personal cars?",
                      discussion_method="counterfactual",
                      moderator_id=100)
    disc.method_state = {
        "current_phase": "extract",
        "phase_round": 1,
        "preliminary_conclusion": None,
        "prior_conclusion": None,
        "claims": [],
        "claim_results": [],
        "current_claim_index": 0,
        "extraction_failed": False,
        "extraction_attempts": 0,
        **state,
    }
    return disc


class TestClaimsToolParameters:
    def test_schema_shape(self):
        assert CLAIMS_TOOL_PARAMETERS["type"] == "object"
        assert set(CLAIMS_TOOL_PARAMETERS["required"]) == {
            "claims", "preliminary_conclusion"}
        props = CLAIMS_TOOL_PARAMETERS["properties"]
        assert props["claims"]["type"] == "array"
        assert props["claims"]["items"]["type"] == "string"
        assert props["preliminary_conclusion"]["type"] == "string"

    def test_schema_bounds_match_prompt(self):
        """Single moderator extraction of '3-7 key claims': bounded in
        the schema like the framing tool's 3-5 set, so schema-enforcing
        providers constrain generation (PR #39 review). Runtime
        validation stays lenient for non-enforcing providers."""
        claims = CLAIMS_TOOL_PARAMETERS["properties"]["claims"]
        assert claims["minItems"] == 3
        assert claims["maxItems"] == 7


class TestValidateClaimsPayload:
    def test_valid(self):
        assert validate_claims_payload(PAYLOAD) == ""

    def test_missing_claims_rejected(self):
        bad = {k: v for k, v in PAYLOAD.items() if k != "claims"}
        assert validate_claims_payload(bad) != ""

    def test_empty_claims_rejected(self):
        bad = {**PAYLOAD, "claims": []}
        assert validate_claims_payload(bad) != ""

    def test_all_claims_too_short_rejected(self):
        bad = {**PAYLOAD, "claims": ["short", "tiny", "x"]}
        err = validate_claims_payload(bad)
        assert err != ""

    def test_at_least_one_substantive_claim_accepted(self):
        """Mirrors parse_numbered_list: short items are tolerated as long
        as at least one substantive claim survives."""
        mixed = {**PAYLOAD, "claims": ["short", VALID_CLAIMS[0]]}
        assert validate_claims_payload(mixed) == ""

    def test_missing_conclusion_rejected(self):
        bad = {k: v for k, v in PAYLOAD.items()
               if k != "preliminary_conclusion"}
        err = validate_claims_payload(bad)
        assert "preliminary_conclusion" in err.lower()

    def test_whitespace_only_conclusion_rejected(self):
        bad = {**PAYLOAD, "preliminary_conclusion": "   \n\t "}
        err = validate_claims_payload(bad)
        assert "preliminary_conclusion" in err.lower()

    def test_empty_conclusion_rejected(self):
        bad = {**PAYLOAD, "preliminary_conclusion": ""}
        err = validate_claims_payload(bad)
        assert "preliminary_conclusion" in err.lower()


class TestExtractClaimsHandlerStructured:
    def test_requires_structured_output(self):
        assert ExtractClaimsHandler().requires_structured_output is True

    def test_turn_order_still_moderator_only(self):
        handler = ExtractClaimsHandler()
        disc = _discussion()
        assert handler.get_turn_order([1, 2, 3], disc) == [disc.moderator_id]

    def test_declares_output_tool(self):
        handler = ExtractClaimsHandler()
        spec = handler.get_output_tool(_moderator(), _discussion())
        assert spec.name == "submit_claims"
        assert spec.parameters is CLAIMS_TOOL_PARAMETERS

    def test_validate_delegates_to_shared_function(self):
        handler = ExtractClaimsHandler()
        disc = _discussion()
        assert handler.validate_output(PAYLOAD, _moderator(), disc) == ""
        bad = {k: v for k, v in PAYLOAD.items() if k != "claims"}
        assert handler.validate_output(bad, _moderator(), disc) != ""

    def test_process_structured_builds_claims_and_claim_results(self):
        handler = ExtractClaimsHandler()
        disc = _discussion()
        mod = _moderator()
        processed = handler.process_structured_response(PAYLOAD, mod, disc)

        state = disc.method_state
        claims = state["claims"]
        assert claims == [
            {"id": i + 1, "text": text}
            for i, text in enumerate(VALID_CLAIMS)
        ]
        assert state["claim_results"] == [
            {
                "claim_id": c["id"],
                "claim_text": c["text"],
                "scores": {},
                "avg_score": None,
                "classification": None,
            }
            for c in claims
        ]
        assert state["extraction_failed"] is False
        assert state["preliminary_conclusion"] == PAYLOAD["preliminary_conclusion"]

        assert "urban pollution" in processed.display_content
        assert PAYLOAD["preliminary_conclusion"] in processed.display_content

    def test_process_structured_strips_trailing_period(self):
        """Parity with the regex path: parse_numbered_list rstrips '.'
        so claim_text matches across paths (PR #39 review)."""
        handler = ExtractClaimsHandler()
        disc = _discussion()
        payload = {**PAYLOAD,
                   "claims": ["Remote work reduces urban pollution levels."]}
        handler.process_structured_response(payload, _moderator(), disc)
        assert disc.method_state["claims"] == [
            {"id": 1, "text": "Remote work reduces urban pollution levels"}]

    def test_process_structured_filters_short_claims(self):
        """Non-substantive claims are dropped, mirroring parse_numbered_list;
        ids are assigned after filtering, starting at 1."""
        handler = ExtractClaimsHandler()
        disc = _discussion()
        payload = {**PAYLOAD, "claims": ["short", VALID_CLAIMS[0], "tiny"]}
        handler.process_structured_response(payload, _moderator(), disc)
        claims = disc.method_state["claims"]
        assert len(claims) == 1
        assert claims[0] == {"id": 1, "text": VALID_CLAIMS[0]}

    def test_process_structured_resets_extraction_failed(self):
        handler = ExtractClaimsHandler()
        disc = _discussion(extraction_failed=True, extraction_attempts=2)
        handler.process_structured_response(PAYLOAD, _moderator(), disc)
        assert disc.method_state["extraction_failed"] is False

    def test_process_structured_respects_prior_conclusion_precedence(self):
        """When prior_conclusion is already set, the submitted
        preliminary_conclusion must not overwrite it."""
        handler = ExtractClaimsHandler()
        disc = _discussion(prior_conclusion="Given conclusion")
        handler.process_structured_response(PAYLOAD, _moderator(), disc)
        assert disc.method_state["preliminary_conclusion"] is None

    def test_process_structured_does_not_overwrite_existing_preliminary(self):
        handler = ExtractClaimsHandler()
        disc = _discussion(preliminary_conclusion="Already captured")
        handler.process_structured_response(PAYLOAD, _moderator(), disc)
        assert disc.method_state["preliminary_conclusion"] == "Already captured"

    def test_structured_matches_free_text_state_shape(self):
        """True cross-path parity (PR #39 review): equivalent input
        through process_response and process_structured_response must
        leave byte-identical claims/claim_results/conclusion state."""
        # A trailing period exercises both paths' rstrip('.')
        # normalization within the parity comparison itself.
        claims = VALID_CLAIMS[:2] + [VALID_CLAIMS[2] + "."]
        free_handler = ExtractClaimsHandler()
        free_disc = _discussion()
        content = ("CONCLUSION: " + PAYLOAD["preliminary_conclusion"]
                   + "\n" + "\n".join(f"{i}. {c}"
                                      for i, c in enumerate(claims, 1)))
        free_handler.process_response(content, _moderator(), free_disc)

        structured_handler = ExtractClaimsHandler()
        structured_disc = _discussion()
        structured_handler.process_structured_response(
            {**PAYLOAD, "claims": claims}, _moderator(), structured_disc)

        for key in ("claims", "claim_results", "preliminary_conclusion",
                    "extraction_failed"):
            assert structured_disc.method_state[key] \
                == free_disc.method_state[key], key

    def test_display_shows_conclusion_actually_in_effect(self):
        """When the payload's conclusion is discarded (a conclusion
        already exists), the transcript must show the one used
        downstream, not the discarded one (PR #39 review)."""
        handler = ExtractClaimsHandler()
        disc = _discussion(prior_conclusion="Given conclusion")
        processed = handler.process_structured_response(
            PAYLOAD, _moderator(), disc)
        assert "Given conclusion" in processed.display_content
        assert PAYLOAD["preliminary_conclusion"] \
            not in processed.display_content

    def test_should_advance_after_structured_success(self):
        handler = ExtractClaimsHandler()
        disc = _discussion()
        handler.process_structured_response(PAYLOAD, _moderator(), disc)
        assert handler.should_advance(disc) is True

    def test_free_text_path_still_works(self):
        """process_response (free-text numbered-list parsing) stays intact."""
        handler = ExtractClaimsHandler()
        disc = _discussion()
        content = (
            "CONCLUSION: Cars should be banned from city centres.\n\n"
            "1. Personal cars contribute significantly to urban pollution\n"
            "2. Public transit can fully replace personal car usage\n"
        )
        result = handler.process_response(content, _moderator(), disc)
        assert disc.method_state["claims"]
        assert disc.method_state["extraction_failed"] is False
        assert result.display_content == content


class TestPromptsNameTheTool:
    def test_system_prompt_names_tool(self):
        handler = ExtractClaimsHandler()
        mod = _moderator()
        disc = _discussion()
        prompt = handler.get_system_prompt(mod, disc)
        assert "submit_claims" in prompt

    def test_initial_turn_prompt_names_tool(self):
        handler = ExtractClaimsHandler()
        mod = _moderator()
        disc = _discussion()
        prompt = handler.get_turn_prompt(mod, disc)
        assert "submit_claims" in prompt
        assert "3-7" in prompt

    def test_retry_turn_prompt_names_tool(self):
        handler = ExtractClaimsHandler()
        mod = _moderator()
        disc = _discussion(extraction_failed=True, extraction_attempts=1)
        prompt = handler.get_turn_prompt(mod, disc)
        assert "submit_claims" in prompt
        assert "try again" in prompt.lower() or "did not produce" in prompt.lower()

    def test_numbered_list_wording_removed_from_prompts(self):
        """The 'numbered list' / 'NUMBERED LIST' format instructions are
        replaced by the tool-call instruction."""
        handler = ExtractClaimsHandler()
        mod = _moderator()
        disc = _discussion()
        initial = handler.get_turn_prompt(mod, disc)
        assert "numbered" not in initial.lower()

        disc_retry = _discussion(extraction_failed=True, extraction_attempts=1)
        retry = handler.get_turn_prompt(mod, disc_retry)
        assert "numbered" not in retry.lower()
