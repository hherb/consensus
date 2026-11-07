"""Characterization tests for ``consensus.evaluation.scorer`` (issue #61).

Written before the module was split so the move is verified by behaviour,
not by a green suite that never executed it (it was at 0% coverage). The
LLM judge is exercised through a stubbed ``AIClient.complete`` — one layer
further out than the code under test — and DB scoring uses a real
``EvalDatabase`` on a temporary file.
"""

import json
import sys

import pytest

from consensus.ai_client import AIClient, AIResponse
from consensus.evaluation import scorer
from consensus.evaluation.eval_db import EvalDatabase
from consensus.evaluation.scorer import (
    llm_judge_run_from_db,
    llm_judge_score,
    normalise,
    print_report,
    score_all,
    score_diagnostic_accuracy,
    score_differential_quality,
    score_run,
    score_run_from_db,
)

#: A seeded case (``consensus/evaluation/cases.py``) — gold is Systemic Lupus
#: Erythematosus with aliases SLE / lupus / systemic lupus.
CASE_KEY = "eval-001"
GOLD = "Systemic Lupus Erythematosus"

JUDGE_SCORES = {"accuracy": 5, "reasoning": 4, "evidence": 3,
                "differential": 2, "critical_analysis": 1, "comments": "ok"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def eval_db(tmp_path):
    """A real EvalDatabase (with seeded cases/conditions) on a temp file."""
    db = EvalDatabase(str(tmp_path / "eval.db"))
    yield db
    db.conn.close()


def _make_run(db: EvalDatabase, *, status: str = "done",
              conclusion: str = "", messages: tuple = ()) -> int:
    """Create a run against the seeded CASE_KEY case and return its id."""
    case = db.get_case_by_key(CASE_KEY)
    assert case is not None
    condition = db.list_conditions()[0]
    batch_id = db.create_batch("b", "http://x/v1", "m")
    run_id = db.create_run(batch_id, case["id"], condition["id"],
                           "http://x/v1", "m")
    db.update_run(run_id, status=status, conclusion=conclusion)
    for i, (speaker, content) in enumerate(messages):
        db.add_run_message(run_id, i, speaker, "participant", content)
    return run_id


@pytest.fixture
def judge_calls(monkeypatch):
    """Stub AIClient.complete; returns the list of recorded call kwargs.

    Set ``judge_calls.reply`` (str) or ``judge_calls.error`` (Exception)
    to control the outcome.
    """
    class Recorder(list):
        reply = json.dumps(JUDGE_SCORES)
        error: Exception | None = None

    rec = Recorder()

    async def fake_complete(self, messages, model, temperature=0.7,
                            max_tokens=1024):
        rec.append({"base_url": self.base_url, "api_key": self.api_key,
                    "messages": messages, "model": model,
                    "temperature": temperature, "max_tokens": max_tokens})
        if rec.error is not None:
            raise rec.error
        return AIResponse(content=rec.reply)

    monkeypatch.setattr(AIClient, "complete", fake_complete)
    return rec


# ---------------------------------------------------------------------------
# String matching
# ---------------------------------------------------------------------------

class TestNormalise:
    def test_lowercases_strips_punctuation_and_collapses_space(self):
        assert normalise("  Lupus,  (SLE)!\n Nephritis ") == "lupus sle nephritis"

    def test_keeps_word_characters_and_underscores(self):
        assert normalise("Type_2 DM-II") == "type_2 dm ii"


class TestDiagnosticAccuracy:
    def test_exact_match_on_gold(self):
        r = score_diagnostic_accuracy("Dx: systemic lupus erythematosus.", GOLD, [])
        assert r == {"correct": True, "matched_term": GOLD.lower(),
                     "confidence": "exact"}

    def test_exact_match_via_alias_ignores_punctuation(self):
        r = score_diagnostic_accuracy("Likely S.L.E? no — SLE.", GOLD, ["SLE"])
        assert r == {"correct": True, "matched_term": "sle",
                     "confidence": "exact"}

    def test_gold_checked_before_aliases(self):
        r = score_diagnostic_accuracy("sle or systemic lupus erythematosus",
                                      GOLD, ["SLE"])
        assert r["matched_term"] == GOLD.lower()

    def test_partial_match_on_longest_word_of_multiword_term(self):
        r = score_diagnostic_accuracy("an erythematosus picture", GOLD, [])
        assert r == {"correct": True, "matched_term": GOLD.lower(),
                     "confidence": "partial"}

    def test_partial_match_needs_two_words(self):
        r = score_diagnostic_accuracy("lupu", "lupus", [])
        assert r == {"correct": False, "matched_term": "", "confidence": "none"}

    def test_partial_match_needs_longest_word_of_four_chars(self):
        # longest word "abc" is under the 4-char floor
        r = score_diagnostic_accuracy("abc here", "ab abc", [])
        assert r["confidence"] == "none"

    def test_partial_longest_word_is_substring_match(self):
        r = score_diagnostic_accuracy("hyperthyroidism", "graves thyroid", [])
        assert r["confidence"] == "partial"

    def test_no_match(self):
        r = score_diagnostic_accuracy("rheumatoid arthritis", GOLD, ["SLE"])
        assert r == {"correct": False, "matched_term": "", "confidence": "none"}


class TestDifferentialQuality:
    def test_first_exact_mention(self):
        msgs = [{"content": "maybe RA", "speaker": "A"},
                {"content": "consider SLE", "speaker": "B"},
                {"content": "SLE again", "speaker": "C"}]
        assert score_differential_quality(msgs, GOLD, ["SLE"]) == {
            "mentioned": True, "first_mention_turn": 1,
            "first_mention_speaker": "B"}

    def test_partial_mention_on_longest_word(self):
        msgs = [{"content": "erythematosus?", "speaker": "A"}]
        r = score_differential_quality(msgs, GOLD, [])
        assert r["mentioned"] and r["first_mention_turn"] == 0

    def test_partial_applies_to_single_word_terms_too(self):
        msgs = [{"content": "lupusoid", "speaker": "A"}]
        assert score_differential_quality(msgs, "lupus", [])["mentioned"]

    def test_missing_keys_default(self):
        r = score_differential_quality([{}, {"content": "SLE"}], GOLD, ["SLE"])
        assert r == {"mentioned": True, "first_mention_turn": 1,
                     "first_mention_speaker": ""}

    def test_not_mentioned(self):
        msgs = [{"content": "gout", "speaker": "A"}]
        assert score_differential_quality(msgs, GOLD, ["SLE"]) == {
            "mentioned": False, "first_mention_turn": -1,
            "first_mention_speaker": ""}

    def test_no_messages(self):
        assert not score_differential_quality([], GOLD, [])["mentioned"]


# ---------------------------------------------------------------------------
# Aggregate scoring (JSON results)
# ---------------------------------------------------------------------------

class TestScoreRun:
    def test_uses_case_aliases_and_augments_in_place(self):
        run = {"case_id": CASE_KEY, "gold_diagnosis": GOLD,
               "conclusion": "Final: SLE",
               "messages": [{"content": "lupus?", "speaker": "A"}]}
        out = score_run(run)
        assert out is run
        assert run["scores"]["diagnostic_accuracy"]["matched_term"] == "sle"
        assert run["scores"]["differential_quality"]["first_mention_speaker"] == "A"

    def test_unknown_case_scores_without_aliases(self):
        run = {"case_id": "no-such-case", "gold_diagnosis": GOLD,
               "conclusion": "SLE"}
        out = score_run(run)
        assert out["scores"]["diagnostic_accuracy"]["correct"] is False
        assert out["scores"]["differential_quality"]["mentioned"] is False


class TestScoreAll:
    def test_prefers_combined_file(self, tmp_path):
        (tmp_path / "all_results.json").write_text(json.dumps(
            [{"case_id": CASE_KEY, "gold_diagnosis": GOLD, "conclusion": "SLE"}]))
        (tmp_path / "eval-zzz.json").write_text(json.dumps(
            {"case_id": CASE_KEY, "gold_diagnosis": GOLD}))
        scored = score_all(str(tmp_path))
        assert len(scored) == 1
        assert scored[0]["scores"]["diagnostic_accuracy"]["correct"]

    def test_falls_back_to_sorted_individual_files(self, tmp_path):
        for name, cond in (("eval-b.json", "second"), ("eval-a.json", "first")):
            (tmp_path / name).write_text(json.dumps(
                {"case_id": CASE_KEY, "gold_diagnosis": GOLD,
                 "condition": cond}))
        (tmp_path / "other.json").write_text("{}")
        scored = score_all(str(tmp_path))
        assert [r["condition"] for r in scored] == ["first", "second"]

    def test_empty_dir(self, tmp_path):
        assert score_all(str(tmp_path)) == []


# ---------------------------------------------------------------------------
# DB-aware scoring
# ---------------------------------------------------------------------------

def _scores_by_type(db: EvalDatabase, run_id: int) -> dict:
    return {s["score_type"]: s["score_data"] for s in db.get_scores(run_id)}


class TestScoreRunFromDb:
    def test_writes_both_string_scores(self, eval_db):
        run_id = _make_run(eval_db, conclusion="Diagnosis: lupus",
                           messages=(("Alice", "gout"), ("Bob", "SLE")))
        score_run_from_db(eval_db, run_id)
        scores = _scores_by_type(eval_db, run_id)
        assert scores["diagnostic_accuracy"]["correct"] is True
        assert scores["differential_quality"] == {
            "mentioned": True, "first_mention_turn": 1,
            "first_mention_speaker": "Bob"}

    def test_skips_run_that_is_not_done(self, eval_db):
        run_id = _make_run(eval_db, status="running", conclusion="SLE")
        score_run_from_db(eval_db, run_id)
        assert eval_db.get_scores(run_id) == []

    def test_unknown_run_is_a_no_op(self, eval_db):
        score_run_from_db(eval_db, 9999)

    def test_missing_case_is_a_no_op(self, eval_db, monkeypatch):
        run_id = _make_run(eval_db, conclusion="SLE")
        monkeypatch.setattr(eval_db, "get_case", lambda _id: None)
        score_run_from_db(eval_db, run_id)
        assert eval_db.get_scores(run_id) == []


# ---------------------------------------------------------------------------
# LLM judge
# ---------------------------------------------------------------------------

class TestLlmJudgeScore:
    async def test_parses_plain_json_and_sends_rubric(self, judge_calls):
        r = await llm_judge_score("CASE TEXT", GOLD, "CONCLUSION TEXT",
                                  "http://judge/v1", "judge-m", api_key="k")
        assert r == {"scores": JUDGE_SCORES, "error": ""}
        (call,) = judge_calls
        assert call["base_url"] == "http://judge/v1"
        assert call["api_key"] == "k"
        assert call["model"] == "judge-m"
        assert call["temperature"] == 0.1
        assert call["max_tokens"] == 512
        prompt = call["messages"][0]["content"]
        assert call["messages"][0]["role"] == "user"
        for part in ("CASE TEXT", GOLD, "CONCLUSION TEXT", '"accuracy": N'):
            assert part in prompt

    @pytest.mark.parametrize("fence", ["```json\n{}\n```", "```\n{}\n```"])
    async def test_strips_markdown_fences(self, judge_calls, fence):
        judge_calls.reply = fence.replace("{}", json.dumps(JUDGE_SCORES))
        r = await llm_judge_score("p", GOLD, "c", "http://j/v1", "m")
        assert r["scores"] == JUDGE_SCORES

    async def test_unparseable_reply_is_reported(self, judge_calls, caplog):
        judge_calls.reply = "I think it scores well."
        r = await llm_judge_score("p", GOLD, "c", "http://j/v1", "m")
        assert r["scores"] == {} and r["error"]
        assert "LLM judge failed" in caplog.text

    async def test_client_failure_is_reported(self, judge_calls):
        judge_calls.error = ConnectionError("down")
        r = await llm_judge_score("p", GOLD, "c", "http://j/v1", "m")
        assert r == {"scores": {}, "error": "down"}


class TestLlmJudgeRunFromDb:
    async def test_writes_llm_judge_score(self, eval_db, judge_calls):
        run_id = _make_run(eval_db, conclusion="SLE")
        await llm_judge_run_from_db(eval_db, run_id, "http://j/v1", "m", "k")
        assert _scores_by_type(eval_db, run_id)["llm_judge"] == {
            "scores": JUDGE_SCORES, "error": ""}
        prompt = judge_calls[0]["messages"][0]["content"]
        case = eval_db.get_case_by_key(CASE_KEY)
        assert case["presentation"] in prompt

    async def test_skips_run_that_is_not_done(self, eval_db, judge_calls):
        run_id = _make_run(eval_db, status="error")
        await llm_judge_run_from_db(eval_db, run_id, "http://j/v1", "m")
        assert judge_calls == [] and eval_db.get_scores(run_id) == []

    async def test_missing_case_is_a_no_op(self, eval_db, judge_calls,
                                           monkeypatch):
        run_id = _make_run(eval_db, conclusion="SLE")
        monkeypatch.setattr(eval_db, "get_case", lambda _id: None)
        await llm_judge_run_from_db(eval_db, run_id, "http://j/v1", "m")
        assert judge_calls == []


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _scored(case_id, condition, correct, mentioned, *, tokens=0, error="",
            judge=None, matched="", conf=""):
    scores = {"diagnostic_accuracy": {"correct": correct,
                                      "matched_term": matched,
                                      "confidence": conf},
              "differential_quality": {"mentioned": mentioned}}
    if judge is not None:
        scores["llm_judge"] = judge
    return {"case_id": case_id, "case_title": f"Title {case_id}",
            "gold_diagnosis": "Gold", "condition": condition,
            "total_tokens": tokens, "error": error, "scores": scores}


class TestPrintReport:
    def test_summary_per_case_and_judge_tables(self, capsys):
        results = [
            _scored("c2", "baseline", True, True, tokens=1500,
                    matched="sle", conf="exact",
                    judge={"scores": {"accuracy": 4, "reasoning": 2}}),
            _scored("c1", "baseline", False, True, tokens=500,
                    judge={"scores": {"accuracy": 2, "reasoning": 4}}),
            _scored("c1", "full", False, False, error="x" * 80),
            _scored("c1", "custom_condition", True, True),
        ]
        print_report(results)
        out = capsys.readouterr().out
        lines = out.splitlines()

        assert "EVALUATION RESULTS SUMMARY" in out
        baseline = next(line for line in lines if line.startswith("baseline "))
        assert "1/2 (50%)" in baseline and "2/2 (100%)" in baseline
        assert "2,000" in baseline
        full = next(line for line in lines if line.startswith("full "))
        assert "0/1 (0%)" in full and full.rstrip().endswith("1")
        # conditions outside the fixed list are left out of the summary
        assert not any(line.startswith("custom_condition ") for line in lines)

        # per-case breakdown: sorted by case id, error text truncated to 50
        assert out.index("c1: Title c1") < out.index("c2: Title c2")
        assert "ERROR: " + "x" * 50 + " " in out
        assert "x" * 51 not in out
        assert "CORRECT (exact: 'sle')" in out
        assert "custom_condition" in out  # still listed per case

        assert "LLM JUDGE SCORES (1-5 scale)" in out
        judge_row = [line for line in lines
                     if line.startswith("baseline ") and "(n=2)" in line]
        assert judge_row and "3.0" in judge_row[0]
        assert "0.0" in judge_row[0]  # absent dimensions average as 0

    def test_no_judge_table_without_judge_scores(self, capsys):
        print_report([_scored("c1", "baseline", True, True)])
        assert "LLM JUDGE" not in capsys.readouterr().out

    def test_judge_table_skips_conditions_without_judged_runs(self, capsys):
        print_report([
            _scored("c1", "baseline", True, True,
                    judge={"scores": {"accuracy": 5}}),
            _scored("c1", "full", True, True, judge={"scores": {}}),
        ])
        lines = capsys.readouterr().out.splitlines()
        judge_rows = [line for line in lines if "(n=" in line]
        assert len(judge_rows) == 1 and judge_rows[0].startswith("baseline")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _write_results(tmp_path, runs):
    (tmp_path / "all_results.json").write_text(json.dumps(runs))


class TestMain:
    def test_scores_saves_default_output_and_prints(self, tmp_path,
                                                    monkeypatch, capsys):
        _write_results(tmp_path, [{"case_id": CASE_KEY, "gold_diagnosis": GOLD,
                                   "condition": "baseline",
                                   "conclusion": "SLE"}])
        monkeypatch.setattr(sys, "argv", ["scorer", "--input", str(tmp_path)])
        scorer.main()
        saved = json.loads((tmp_path / "scored_results.json").read_text())
        assert saved[0]["scores"]["diagnostic_accuracy"]["correct"] is True
        assert "EVALUATION RESULTS SUMMARY" in capsys.readouterr().out

    def test_llm_judge_flag_judges_known_cases(self, tmp_path, monkeypatch,
                                               judge_calls):
        _write_results(tmp_path, [
            {"case_id": CASE_KEY, "gold_diagnosis": GOLD,
             "condition": "baseline", "conclusion": "SLE"},
            {"case_id": "no-such-case", "gold_diagnosis": GOLD,
             "condition": "baseline", "conclusion": "SLE"},
        ])
        out_file = tmp_path / "custom.json"
        monkeypatch.setenv("JUDGE_KEY", "secret")
        monkeypatch.setattr(sys, "argv", [
            "scorer", "--input", str(tmp_path), "--llm-judge",
            "--judge-provider-url", "http://j/v1", "--judge-model", "jm",
            "--judge-api-key-env", "JUDGE_KEY", "--output", str(out_file)])
        scorer.main()
        saved = json.loads(out_file.read_text())
        assert saved[0]["scores"]["llm_judge"] == {"scores": JUDGE_SCORES,
                                                   "error": ""}
        assert "llm_judge" not in saved[1]["scores"]
        (call,) = judge_calls
        assert (call["base_url"], call["model"], call["api_key"]) == (
            "http://j/v1", "jm", "secret")

    def test_llm_judge_model_falls_back_to_eval_model(self, tmp_path,
                                                      monkeypatch,
                                                      judge_calls):
        _write_results(tmp_path, [{"case_id": CASE_KEY,
                                   "gold_diagnosis": GOLD,
                                   "condition": "baseline"}])
        monkeypatch.setenv("EVAL_MODEL", "fallback-model")
        monkeypatch.setattr(sys, "argv", ["scorer", "--input", str(tmp_path),
                                          "--llm-judge", "--judge-model", ""])
        scorer.main()
        assert judge_calls[0]["model"] == "fallback-model"
        assert judge_calls[0]["api_key"] == ""
