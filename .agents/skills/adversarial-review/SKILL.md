---
name: adversarial-review
description: Run an explicit fail-closed adversarial correctness review of a completed code change. Use only when the user invokes $adversarial-review and wants defects independently found, objectively verified, fixed, and rechecked with a final PASS, FAIL, or INCONCLUSIVE result.
---

# Adversarial Review

Run a bounded correctness review with exactly one active read-only custom reviewer.
Treat review integrity as a prerequisite, not an assumption.

## Non-negotiable outcome rules

Use exactly one final state:

- `PASS`: the review completed with provable integrity and no blocking finding remains.
- `FAIL`: a CONFIRMED CRITICAL, HIGH, or MEDIUM defect remains after the permitted fixes, or deterministic validation proves the change is broken.
- `INCONCLUSIVE`: review quality or final-version coverage cannot be proved.

Never turn an execution failure, missing evidence, invalid response, or incomplete scope into PASS.
Never substitute a generic reviewer for `adversarial_reviewer`.

PASS requires all of the following:

- the exact custom agent `adversarial_reviewer` was verified from spawn metadata;
- it successfully completed every required review turn;
- every response passed the bundled protocol validator;
- the full change manifest was captured successfully, including untracked files;
- the final reviewed snapshot exactly matches the state used for the decision;
- the reviewer did not change the worktree;
- relevant deterministic validation passed;
- no CONFIRMED CRITICAL, HIGH, or MEDIUM finding remains;
- no UNCERTAIN CRITICAL or HIGH finding remains;
- every required escalation full review completed.

## Hard budget

- Initial full review: exactly 1.
- Escalation full review after a substantial fix: at most 1.
- Incremental re-reviews: at most 2 total.
- Active reviewer agents: exactly 1 at a time.
- Incremental turns: reuse the same verified reviewer thread.
- LOW-only findings never trigger a repair loop.

If a required review would exceed the budget, return INCONCLUSIVE.

## Required guard

Use `scripts/review_guard.py` from this skill directory for manifest capture, snapshot comparison, result validation, escalation assessment, and the final decision.

If Python 3, Git, the script, or any required guard command is unavailable or fails, return INCONCLUSIVE. Do not replace it with an improvised partial check.

Create a private temporary review directory outside the repository. Keep manifests, exact reviewer responses, and decision inputs there. Do not add review artifacts to the project.

## 1. Run cheap validation first

Run the cheapest relevant deterministic checks already present in the repository: focused tests, type checks, compilation, linting, or static checks.

Fix obvious deterministic failures before spawning the reviewer. Record each command and result. Distinguish:

- `PASSED`: relevant checks completed successfully;
- `FAILED`: checks prove a blocking implementation defect remains;
- `INCONCLUSIVE`: checks could not execute or their result is unreliable.

## 2. Build and freeze the complete scope

Choose the review target in this order:

1. target explicitly named by the user;
2. implementation completed in the current task;
3. current staged, unstaged, and untracked changes;
4. branch diff against a reliably determined base.

Capture the target with the guard. Pass `--base <ref>` for a branch comparison and `--include <path>` for each explicit target file not already in the diff.

The captured manifest must contain:

- HEAD SHA;
- base ref and merge-base SHA when applicable;
- branch, staged, unstaged, renamed, added, deleted, and untracked entries;
- SHA-256 for every current scope file and available baseline content;
- Git status hash;
- canonical diff hash and size;
- a canonical manifest hash.

Treat `scope_complete != true`, an empty unintended scope, an unreadable path, an unsupported status, an unresolved base, or any capture error as INCONCLUSIVE.

Every untracked file is a first-class review input. Give the reviewer its path, size, hash, and an explicit instruction to inspect the entire file because no ordinary Git diff contains it.

Record a compact review brief containing the original requirement, acceptance criteria, manifest path and hash, base/target, all scope paths and statuses, untracked full-file inputs, and validation already run.

Do not silently omit generated-looking, binary, large, renamed, or deleted files. If a behaviorally relevant file cannot be reviewed reliably, return INCONCLUSIVE.

## 3. Spawn with fail-closed identity verification

Immediately before spawn, capture `before-review.json` with the same guard arguments as the frozen target.
Compare it to the original frozen manifest. If they differ, rebuild the brief from the new manifest before spawn; never review a stale brief.

Spawn exactly one custom agent by the configured name `adversarial_reviewer`. Do not request `default`, `worker`, `explorer`, or another generic role.

Before trusting any output, verify from the spawn result or agent-thread metadata that the selected custom agent name is exactly `adversarial_reviewer`. The TOML `name` field is authoritative; the task label or a prompt claiming the name is not proof.

If the surface does not expose the selected custom-agent identity, or if spawn selects a different agent, return INCONCLUSIVE. Do not retry with a generic fallback.

Give the reviewer:

- a unique review ID;
- review kind `FULL`;
- the compact brief;
- the exact manifest hash;
- the required JSON response protocol from its developer instructions.

Wait for the reviewer to finish before changing any project file. A crash, timeout, cancellation, shutdown, missing result, undelivered task, or inability to prove successful completion means INCONCLUSIVE.

## 4. Verify reviewer execution and response

Immediately after completion and before any edit or validation command:

1. capture `after-review.json` with the same arguments;
2. compare it to `before-review.json` with `review_guard.py compare`;
3. save the exact reviewer response without repair or reformatting;
4. validate it with `review_guard.py validate-result`.

Any snapshot difference means the reviewer turn is invalid and the final state is INCONCLUSIVE. This includes changes outside the review scope.

Any empty, truncated, malformed, non-JSON, wrong-version, wrong-reviewer, wrong-review-ID, wrong-kind, non-completed, wrong-manifest, or incomplete-path response is invalid and produces INCONCLUSIVE. Zero findings is valid only as an explicit, successfully validated `findings: []` response.

## 5. Verify and classify every finding

Classify every reviewer finding as:

- `CONFIRMED`
- `REJECTED`
- `UNCERTAIN`

Confirm only with a reachable trigger/state, execution path, and observable wrong result or a clearly violated repository contract.

For CRITICAL or HIGH, never use `REJECTED` based only on the main agent's interpretation. Attach objective rejection evidence of at least one accepted type:

- executable reproduction;
- passing regression test that exercises and disproves the exact trigger;
- explicit acceptance criterion;
- documented contract;
- type invariant;
- validation logic that makes the trigger unreachable;
- concrete repository evidence.

Record the evidence type, exact command or repository reference, and details. “Looks intentional”, “probably safe”, author confidence, or a test that does not exercise the trigger is insufficient. Without valid evidence, classify the CRITICAL/HIGH finding as UNCERTAIN.

Do not modify correct code merely to satisfy the reviewer. Do not spend the repair budget on LOW-only or style-only findings.

## 6. Fix blocking findings and assess review escalation

For CONFIRMED blocking findings:

- fix the root cause with the smallest defensible change;
- add a focused regression test when practical;
- rerun relevant deterministic validation;
- capture a new current manifest with the same scope arguments.

Run `review_guard.py assess-fix` against the last reviewed manifest and the new manifest. Add a `--semantic` flag for every applicable semantic change:

- `public-api`
- `schema`
- `persistence`
- `concurrency`
- `core-invariant`
- `architecture`
- `core-algorithm`

The guard automatically escalates for new files, previously unreviewed paths, a changed HEAD, or significant diff growth. Semantic flags cover changes that Git shape alone cannot detect.

If escalation is required, an incremental review is insufficient. Route a new `FULL` turn over the entire new frozen manifest to the same verified custom reviewer thread and count it against the escalation-full-review budget. If the thread cannot receive and complete that full turn, or the budget is exhausted, return INCONCLUSIVE.

If escalation is not required, send an `INCREMENTAL` follow-up to the same verified reviewer thread containing only the fix patch, fixed finding IDs, focused test results, the new manifest hash, and small directly related context.

For every follow-up:

- verify delivery to the exact existing thread;
- capture and compare worktree snapshots around the turn;
- require successful completion;
- validate the exact response against its review ID, kind, and manifest;
- reclassify every returned finding using the same evidence rules.

If delivery, completion, snapshot, or validation cannot be proved, return INCONCLUSIVE.

## 7. Prove the final version was reviewed

After all fixes and deterministic validation, capture a final manifest with the original scope arguments.

The final manifest hash must equal the manifest hash in the last successfully validated reviewer response. If it differs, the final code was not reviewed. Perform the required incremental or full review if budget permits; otherwise return INCONCLUSIVE.

Capture once more immediately before announcing PASS and compare again. Any unexpected scope, HEAD, status, diff, or content-hash change requires re-review or INCONCLUSIVE.

## 8. Compute the final state

Build the guard decision input with:

- all eight integrity booleans required by `review_guard.py decide`;
- validation state;
- escalation requirement and completion;
- every finding's severity and classification;
- objective rejection evidence for every REJECTED CRITICAL/HIGH finding.

Run `review_guard.py decide`. Report exactly the state emitted by the guard. Never override it with prose reasoning.

## Final response

Keep it concise:

`Adversarial review: PASS | FAIL | INCONCLUSIVE`

- Full reviews: N/2
- Incremental re-reviews: N/2
- Confirmed: Critical N / High N / Medium N
- Fixed: N
- Rejected false positives: N
- Uncertain: Critical N / High N / Medium N
- Scope manifest: `<final manifest hash>`
- Validation: `<short list>`
- Remaining blocking defects: `none | <short list>`
- Inconclusive reasons: `none | <short list>`

On PASS, say only that no confirmed blocking defects or unresolved CRITICAL/HIGH uncertainties remain in the proven reviewed scope. Never claim the implementation is perfect or bug-free.
