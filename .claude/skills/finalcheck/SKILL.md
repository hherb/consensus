---
name: finalcheck
description: Use for the final pre-merge pass on the current consensus PR — review it, fix everything, run the local test gate, then drive the GitHub checks (CodeQL, code quality) to green
disable-model-invocation: false
allowed-tools: Read, Edit, Bash(git *), Bash(gh *), Bash(uv *), Bash(uvx ruff *)
---
Run these steps in order on the PR for the current branch. Agent sessions here may leave `git worktree`s
under `.claude/worktrees/`, most of them on a detached HEAD. First run
`pwd && git branch --show-current && git worktree list` and `cd` into the worktree that has the PR's branch
checked out. Then find the PR with `gh pr view --json number,url,headRefOid,baseRefName`. If there is none,
stop and tell me.

1. **Review.** Invoke the `pr-review-toolkit:review-pr` skill via the Skill tool and let it finish.
2. **Sync with main.** `git fetch origin && git merge origin/main`. Resolve any conflicts by reading both
   sides; don't rebase or force-push a branch that is already under review.
3. **Fix.** This repo's `fixall` skill has `disable-model-invocation: true`, so the Skill tool cannot run it.
   Read `.claude/skills/fixall/SKILL.md` and follow it on the review's findings. It fixes what fits in this
   session, files GitHub issues for the rest, checks against `docs/llm/golden_rules.md`, touches
   `HANDOVER.md` / `ROADMAP.md` only if needed, then commits and pushes into the PR. Before moving on,
   confirm the push landed: `git fetch origin && git status -sb` must show no `ahead`, and the PR's
   `headRefOid` must equal `git rev-parse HEAD`.
4. **Local test gate.** There is **no test CI** in this repo; the GitHub checks below run static analysis
   only. So this is the only place the suite runs:
   - `uv run pytest`. It includes `tests/test_lint.py`, which fails on any `ruff --select F` finding in
     `consensus/` (issue #86). For a quick lint on its own, use `uvx ruff check --select F consensus`.
   - If HANDOVER.md records a test count, it must match what this run reports.

   Judge each command by its **exit code**, never by filtered output: `cmd | tail` reports `tail`'s status.
   A failure is fixed at the root cause, with TDD where it is a code bug (failing test first). Commit with a
   message naming the failure, push, and re-run.
5. **Drive the GitHub checks to green.** Loop:
   - Watch the checks for the pushed head commit with `gh pr checks --watch --fail-fast`. Checks can take a
     minute to register, so wait if it reports none yet. What normally appears:
     - `Analyze (python)`, `Analyze (javascript-typescript)` and the `CodeQL` result. These come from CodeQL
       **default setup**: there is no workflow file under `.github/workflows/`, and the runs don't show up
       in `gh run list --branch`.
     - `Cloudflare Pages`, the preview build of the static site in `website/`.
   - What blocks merging is the `protect main branch` ruleset (`gh api repos/hherb/consensus/rules/branches/main`).
     It gates on code scanning (CodeQL security alerts of high or higher severity, and other alerts at
     error level), on code quality at error severity, and it forbids force-pushes to and deletion of `main`.
     `branches/main/protection` returns 404 by design, so don't read that as "unprotected".
   - **All checks green.** Also confirm the PR adds no open code-scanning alerts:
     `gh api "repos/hherb/consensus/code-scanning/alerts?ref=refs/pull/<n>/merge&state=open"`.
     `main` carries some older open alerts. Judge only the ones this PR introduces: compare against
     `gh api "repos/hherb/consensus/code-scanning/alerts?state=open"` by rule and location. If there are
     none, you're done: report the PR URL, the final check list and the alert count.
   - **A CodeQL check failed or the PR adds an alert.** Read the alert (`rule.id`,
     `most_recent_instance.location`, message) from the API above. A real defect, such as path injection,
     SSRF or a clear-text secret: fix the root cause with a failing test first, push, and loop again.
     A false positive: stop and report it with your reasoning. Never dismiss an alert, or add
     `# noqa` / `# lgtm` / `# codeql` suppressions, without my explicit permission. Alert handling here is
     a security decision.
   - **Cloudflare Pages failed.** This matters only if the PR touches `website/`. Open the dashboard link
     from `gh pr checks` and report the build error; the site is plain static HTML/CSS (`website/_headers`
     controls caching).
   - **The failure is not caused by the code** (runner or infra outage, network flake): retry once. For
     CodeQL, push nothing new; re-run it from the check's link, or with `gh run rerun <run-id> --failed`
     using the run id from that link. If it fails the same way again, stop and report rather than changing
     code to work around it. The API returns empty or stale data during an outage, so re-query before
     concluding a run never happened.
   - Never "fix" a failure by skipping, `xfail`-ing or deleting tests, or by weakening the ruff gate in
     `tests/test_lint.py`, a facade guard (`tests/test_*_facade.py`), the SSRF / path-traversal guards or
     the sandbox restrictions, without my explicit permission. If the same failure survives three fix
     attempts, stop and report what you tried.
6. **Not covered by any check.** Include each of these that applies in the report:
   - **Frontend** (`consensus/static/`): there is no JS test harness. If the PR touches it, say so, and if
     you can, exercise the change with `python -m consensus --web --debug` in a browser.
   - **Desktop mode** (`desktop/`, pywebview) can't run headless, so name any change there as unverified.
   - **Packaging and release** (`packaging/`, `scripts/release_pypi.sh`, `pyproject.toml`): the release
     playbook (TestPyPI, then PyPI, then the notarized DMG) is a separate manual process. Mention it if
     the PR touches these.
