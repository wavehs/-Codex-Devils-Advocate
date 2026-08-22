---
name: adversarial-review
description: Run an explicit adversarial correctness review of a completed code change. Strict mode is fail-closed and returns PASS, FAIL, or INCONCLUSIVE only when runtime identity/sandbox attestation is available. Best-effort mode may degrade to UNVERIFIED when Codex cannot expose trusted custom-agent metadata.
---

# Adversarial Review

Run a bounded correctness review with exactly one active reviewer.
Treat review integrity as a prerequisite, not an assumption.

## Modes

Default to `STRICT` unless the user explicitly asks for best-effort or unverified review.

### STRICT — default

Use exactly one final state:

- `PASS`: the review completed with provable integrity and no blocking finding remains.
- `FAIL`: a CONFIRMED CRITICAL, HIGH, or MEDIUM defect remains after the permitted fixes, or deterministic validation proves the change is broken.
- `INCONCLUSIVE`: review quality, runtime identity/sandbox attestation, or final-version coverage cannot be proved.

Never turn an execution failure, missing evidence, invalid response, incomplete scope, or unavailable runtime attestation into PASS or FAIL.
Never substitute a generic reviewer for `adversarial_reviewer` in STRICT mode.

### BEST_EFFORT — explicit opt-in

Use this mode only when the user explicitly asks for `BEST_EFFORT`, `best-effort`, or an unverified fallback.

BEST_EFFORT keeps the deterministic manifest, snapshot, protocol, and finding-verification checks, but it may continue when the current Codex runtime cannot prove the spawned custom-agent identity or effective read-only sandbox.

Possible final states are:

- `PASS` / `FAIL`: only if the same strict runtime attestation requirements were actually satisfied.
- `UNVERIFIED`: the review completed with usable protocol/scope/worktree evidence, but exact custom-agent identity and/or effective read-only sandbox could not be independently attested by the runtime.
- `INCONCLUSIVE`: core review integrity failed, such as malformed output, incomplete scope, reviewer execution failure, worktree mutation, or unreviewed final code.

`UNVERIFIED` is never a synonym for PASS or FAIL. Present findings as candidate/independently checked findings and explicitly state that review identity/isolation was not certified.

## Strict PASS requirements

PASS requires all of the following:

- the exact custom agent `adversarial_reviewer` was verified from trusted runtime metadata;
- the spawned reviewer's effective sandbox was verified as `read-only` from trusted runtime metadata;
- it successfully completed every required review turn;
- every response passed the bundled protocol validator;
- the full change manifest was captured successfully, including untracked files;
- the final reviewed snapshot exactly matches the state used for the decision;
- the reviewer did not change the worktree;
- relevant deterministic validation passed;
- no CONFIRMED CRITICAL, HIGH, or MEDIUM finding remains;
- no UNCERTAIN CRITICAL or HIGH finding remains;
- every required escalation full review completed.

A task/thread label, prompt claim, self-reported JSON field, local TOML contents, or the existence of the custom-agent file is not trusted runtime identity evidence.

## Hard budget

After runtime preflight permits execution:

- Initial full review: exactly 1.
- Escalation full review after a substantial fix: at most 1.
- Incremental re-reviews: at most 2 total.
- Active reviewer agents: exactly 1 at a time.
- Incremental turns: reuse the same reviewer thread.
- LOW-only findings never trigger a repair loop.

If STRICT preflight fails before review starts, use zero reviewer turns and stop early.
If a required review would exceed the budget, return INCONCLUSIVE.

## Required guard

Use `scripts/review_guard.py` from this skill directory for runtime preflight, manifest capture, snapshot comparison, result validation, escalation assessment, and the final decision.

If Python 3, Git, the script, or any required guard command is unavailable or fails, return INCONCLUSIVE. Do not replace it with an improvised partial check.

Create a private temporary review directory outside the repository. Keep manifests, exact reviewer responses, and decision inputs there. Do not add review artifacts to the project.

## 0. Runtime compatibility preflight — before expensive work

Do this before tests, full-project enumeration, manifest capture, or reviewer spawn.

Inspect the model-visible reviewer/subagent interface and any runtime metadata contract available in the current Codex surface.
Determine whether the runtime can independently prove both:

1. the selected reviewer is exactly the registered custom agent `adversarial_reviewer`;
2. the effective child sandbox is exactly `read-only`.

Do not count any of these as proof:

- `task_name` matching `adversarial_reviewer`;
- a prompt telling the child what its identity is;
- `reviewer_name` inside the child's JSON response;
- the local `.toml` file alone;
- the child saying that it is read-only.

If the current spawn/tool schema exposes only task/thread naming and no trusted custom-agent selector or identity metadata, identity is not verifiable.
If effective sandbox metadata is absent, effective sandbox is not verifiable.
When evidence is unavailable, do not assume it exists just because the custom-agent configuration is valid.

Run the guard preflight with the observed capabilities, for example:

```text
python review_guard.py preflight --mode STRICT --identity-verifiable no --sandbox-read-only-verifiable no
```

or:

```text
python review_guard.py preflight --mode BEST_EFFORT --identity-verifiable no --sandbox-read-only-verifiable no
```

Follow the emitted `proceed` value.

### STRICT preflight failure

If `proceed` is false:

- stop immediately with `INCONCLUSIVE`;
- do not run the project's test suite merely for this review;
- do not enumerate/freeze a large scope;
- do not spawn the reviewer;
- report the runtime-attestation reason concisely.

This prevents spending a full review budget on a Codex runtime that can never produce a trusted PASS/FAIL.

If the limitation becomes apparent only after a spawn call, do not wait for or trust the child's substantive review. Cancel/stop the child when the surface supports it and finish INCONCLUSIVE.

### BEST_EFFORT degraded preflight

If BEST_EFFORT emits `proceed: true` with `degraded: true`, continue, but the final result must remain `UNVERIFIED` unless strict runtime attestation becomes available later.

Prefer the configured `adversarial_reviewer` when the runtime can request it. If the runtime has no exact custom-agent selector but can spawn a generic child, one generic child is permitted only in BEST_EFFORT mode. Give it the complete fallback reviewer contract in `references/best-effort-reviewer.md` plus the exact review brief and protocol fields.

Never use more than one fallback reviewer.

## 1. Run cheap validation

Only after preflight permits execution, run the cheapest relevant deterministic checks already present in the repository: focused tests, type checks, compilation, linting, or static checks.

Respect the user's write intent. If the user requested review only, do not modify project code merely because deterministic checks fail; record the failure and continue the review when possible. If the user explicitly asked for repair, fix obvious deterministic failures before spawning the reviewer.

Record each command and result. Distinguish:

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

## 3. Spawn the reviewer

Immediately before spawn, capture `before-review.json` with the same guard arguments as the frozen target.
Compare it to the original frozen manifest. If they differ, rebuild the brief from the new manifest before spawn; never review a stale brief.

### STRICT

Spawn exactly one custom agent by the configured name `adversarial_reviewer`. Do not request `default`, `worker`, `explorer`, or another generic role.

Before trusting any output, verify from trusted spawn/thread runtime metadata that the selected custom agent name is exactly `adversarial_reviewer` and its effective sandbox is exactly `read-only`. The TOML fields are defaults only: parent runtime overrides may supersede them.

If the runtime metadata contradicts preflight, stop INCONCLUSIVE.

### BEST_EFFORT

Use the exact custom agent when possible. If exact selection is unavailable, use one generic child with `references/best-effort-reviewer.md` as the behavioral contract.

When runtime identity or sandbox attestation remains unavailable, record the corresponding integrity booleans as false in the final decision input. Do not manufacture proof from the child's response.

### Every reviewer turn

Give the reviewer:

- a unique review ID;
- review kind `FULL` or `INCREMENTAL`;
- the compact brief;
- the exact manifest hash;
- the required JSON response protocol.

Wait for the reviewer to finish only after the mode allows the turn to proceed. A crash, timeout, cancellation, shutdown, missing result, undelivered task, or inability to prove successful completion means INCONCLUSIVE.

## 4. Verify reviewer execution and response

Immediately after completion and before any edit or validation command:

1. capture `after-review.json` with the same arguments;
2. compare it to `before-review.json` with `review_guard.py compare`;
3. save the exact reviewer response without repair or reformatting;
4. validate it with `review_guard.py validate-result`.

Any snapshot difference means the reviewer turn is invalid and the final state is INCONCLUSIVE. This includes changes outside the review scope.

Any empty, truncated, malformed, non-JSON, wrong-version, wrong-reviewer-protocol-label, wrong-review-ID, wrong-kind, non-completed, wrong-manifest, or incomplete-path response is invalid and produces INCONCLUSIVE. Zero findings is valid only as an explicit, successfully validated `findings: []` response.

The protocol requests `confidence` as an integer from `0` to `100`. For compatibility with real Codex model output, the validator also accepts a finite fractional confidence from `0.0` to `1.0` without rewriting the original JSON. Confidence is not runtime identity evidence.

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

In BEST_EFFORT degraded mode, these classifications may still be useful because the parent verifies them independently, but the overall review remains UNVERIFIED.

## 6. Fix blocking findings and assess review escalation

Only modify project code when the user's request permits fixes.

For CONFIRMED blocking findings that should be repaired:

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

If escalation is required, an incremental review is insufficient. Route a new `FULL` turn over the entire new frozen manifest to the same reviewer thread and count it against the escalation-full-review budget. If the thread cannot receive and complete that full turn, or the budget is exhausted, return INCONCLUSIVE.

If escalation is not required, send an `INCREMENTAL` follow-up to the same reviewer thread containing only the fix patch, fixed finding IDs, focused test results, the new manifest hash, and small directly related context.

For every follow-up:

- verify delivery to the exact existing thread;
- capture and compare worktree snapshots around the turn;
- require successful completion;
- validate the exact response against its review ID, kind, and manifest;
- reclassify every returned finding using the same evidence rules.

If delivery, completion, snapshot, or validation cannot be proved, return INCONCLUSIVE.

## 7. Prove the final version was reviewed

After all permitted fixes and deterministic validation, capture a final manifest with the original scope arguments.

The final manifest hash must equal the manifest hash in the last successfully validated reviewer response. If it differs, the final code was not reviewed. Perform the required incremental or full review if budget permits; otherwise return INCONCLUSIVE.

Capture once more immediately before announcing PASS, FAIL, or UNVERIFIED and compare again. Any unexpected scope, HEAD, status, diff, or content-hash change requires re-review or INCONCLUSIVE.

## 8. Compute the final state

Build the guard decision input with:

- `mode`: `STRICT` or `BEST_EFFORT`;
- all nine integrity booleans required by `review_guard.py decide`;
- validation state;
- escalation requirement and completion;
- every finding's severity and classification;
- objective rejection evidence for every REJECTED CRITICAL/HIGH finding.

Run `review_guard.py decide`. Report exactly the state emitted by the guard. Never override it with prose reasoning.

## Final response

Keep it concise.

For strict/certified results:

`Adversarial review: PASS | FAIL | INCONCLUSIVE`

For degraded best-effort results:

`Adversarial review: UNVERIFIED`

Include:

- Mode: `STRICT | BEST_EFFORT`
- Full reviews: N/2
- Incremental re-reviews: N/2
- Confirmed: Critical N / High N / Medium N
- Fixed: N
- Rejected false positives: N
- Uncertain: Critical N / High N / Medium N
- Scope manifest: `<final manifest hash>`
- Validation: `<short list>`
- Runtime attestation: `<verified | missing identity | missing sandbox | both missing>`
- Remaining blocking defects: `none | <short list>`
- Inconclusive reasons: `none | <short list>`
- Unverified reasons: `none | <short list>`

On PASS, say only that no confirmed blocking defects or unresolved CRITICAL/HIGH uncertainties remain in the proven reviewed scope. Never claim the implementation is perfect or bug-free.

On UNVERIFIED, explicitly say that findings may be useful but the result must not be treated as a certified PASS/FAIL because the current runtime did not provide sufficient trusted reviewer identity/isolation metadata.
