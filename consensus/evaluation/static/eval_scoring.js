/* === Consensus Evaluation UI — scoring (re-score + LLM judge) ===
 * Loaded after eval.js; shares its globals ($, $$, API, showToast,
 * renderBatchResults, viewRun). eval.js calls scoresByRunId /
 * summariseScores and reads JUDGE_KEY_STORAGE only at call time. */

// LLM-judge rubric dimensions (1-5); keep in step with JUDGE_DIMENSIONS in
// consensus/evaluation/constants.py.
const JUDGE_DIMENSIONS = ['accuracy', 'reasoning', 'evidence', 'differential', 'critical_analysis'];
const JUDGE_KEY_STORAGE = 'eval_judge_api_key';

// ---------------------------------------------------------------------------
// Scoring (re-score + LLM judge)
// ---------------------------------------------------------------------------

/** Map run id -> {score_type: score_data} from a batch's score rows. */
function scoresByRunId(scores) {
    const byRun = {};
    (scores || []).forEach(s => {
        (byRun[s.run_id] = byRun[s.run_id] || {})[s.score_type] = s.score_data || {};
    });
    return byRun;
}

/** Correct / in-differential counts and the mean judge score for some runs. */
function summariseScores(runs, scoresByRun) {
    let scored = 0, correct = 0, inDiff = 0, judged = 0, judgeSum = 0;
    runs.forEach(r => {
        const sc = scoresByRun[r.id] || {};
        if (sc.diagnostic_accuracy) {
            scored++;
            if (sc.diagnostic_accuracy.correct) correct++;
            if (sc.differential_quality && sc.differential_quality.mentioned) inDiff++;
        }
        const dims = (sc.llm_judge && sc.llm_judge.scores) || {};
        const vals = JUDGE_DIMENSIONS.map(d => dims[d]).filter(v => typeof v === 'number');
        if (vals.length) {
            judged++;
            judgeSum += vals.reduce((a, b) => a + b, 0) / vals.length;
        }
    });
    return { scored, correct, inDiff, judged, judgeMean: judged ? judgeSum / judged : 0 };
}

function fmtRatio(n, d) {
    return d ? `${n}/${d} (${Math.round(100 * n / d)}%)` : '—';
}

/** Judge settings from the Results tab, or null (with a toast) if incomplete. */
function judgeConfig() {
    const cfg = {
        provider_url: $('#judge-provider-url').value.trim(),
        model: $('#judge-model').value.trim(),
        api_key: $('#judge-api-key').value.trim(),
    };
    if (!cfg.provider_url || !cfg.model) {
        showToast('Judge provider URL and model are required');
        return null;
    }
    // BYOK: kept in this browser session only, sent per request.
    if (cfg.api_key) sessionStorage.setItem(JUDGE_KEY_STORAGE, cfg.api_key);
    else sessionStorage.removeItem(JUDGE_KEY_STORAGE);
    return cfg;
}

let scoringBusy = false;

function setScoringBusy(busy) {
    scoringBusy = busy;
    $$('.scoring-btn').forEach(b => { b.disabled = busy; });
}

function selectedBatchId() {
    return parseInt($('#results-batch-select').value) || null;
}

/** Re-score (kind 'score') or LLM-judge (kind 'judge') one run. */
function scoreRunOf(kind, runId, cfg) {
    return kind === 'judge' ? API.judgeRun(runId, cfg) : API.scoreRun(runId);
}

/** Re-score or LLM-judge every completed run of the selected batch. */
async function scoreBatch(kind) {
    const batchId = selectedBatchId();
    if (!batchId || scoringBusy) return;
    const cfg = kind === 'judge' ? judgeConfig() : null;
    if (kind === 'judge' && !cfg) return;
    const label = kind === 'judge' ? 'LLM-judged' : 'Re-scored';
    const status = $('#scoring-status');

    setScoringBusy(true);
    try {
        const batch = await API.getBatch(batchId);
        const done = (batch.runs || []).filter(r => r.status === 'done');
        if (!done.length) {
            status.textContent = 'No completed runs to score.';
            return;
        }
        let failed = 0, lastError = '';
        for (const [i, r] of done.entries()) {
            status.textContent = `${kind === 'judge' ? 'Judging' : 'Scoring'} run ${i + 1}/${done.length}…`;
            try { await scoreRunOf(kind, r.id, cfg); }
            catch (e) { failed++; lastError = e.message; }
        }
        status.textContent = failed
            ? `${label} ${done.length - failed}/${done.length} runs; ${failed} failed — ${lastError}`
            : `${label} ${done.length} runs.`;
        showToast(failed ? `${failed} run(s) failed: ${lastError}` : status.textContent);
        await renderBatchResults(batchId);
    } catch (e) {
        status.textContent = e.message;
        showToast(e.message);
    } finally {
        setScoringBusy(false);
    }
}

/** Re-score or LLM-judge the run shown in the run dialog, then refresh. */
async function scoreSingleRun(kind, runId) {
    if (!runId || scoringBusy) return;
    const cfg = kind === 'judge' ? judgeConfig() : null;
    if (kind === 'judge' && !cfg) return;
    setScoringBusy(true);
    try {
        await scoreRunOf(kind, runId, cfg);
        showToast(kind === 'judge' ? 'Run judged' : 'Run re-scored');
        await viewRun(runId);
        const batchId = selectedBatchId();
        if (batchId) await renderBatchResults(batchId);
    } catch (e) {
        showToast(e.message);
    } finally {
        setScoringBusy(false);
    }
}
