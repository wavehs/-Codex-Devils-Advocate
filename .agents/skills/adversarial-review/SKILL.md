---
name: adversarial-review
description: Run an explicit fail-closed adversarial correctness review of a completed code change. Use only when the user invokes $adversarial-review and wants defects independently found, objectively verified, fixed, and rechecked with a final PASS, FAIL, or INCONCLUSIVE result.
---

# Adversarial Review

Run a bounded review with exactly one read-only custom reviewer. Evidence artifacts, not the main agent's assertions, determine the outcome.

## Outcome state machine

- `PASS`: the exact reviewer completed, deterministic validation passed, the final version was reviewed unchanged, and every blocking finding is either objectively rejected or reviewer-verified as fixed.
- `FAIL`: deterministic validation failed or a CONFIRMED CRITICAL/HIGH/MEDIUM finding is OPEN or STILL_PRESENT after the permitted repair cycles.
- `INCONCLUSIVE`: review integrity, scope, identity, lifecycle continuity, or final-version coverage cannot be proved.

`UNCERTAIN` CRITICAL/HIGH always forbids PASS. Any crash, timeout, empty/malformed response, missing receipt, wrong reviewer, undelivered follow-up, unexpected worktree change, incomplete verification, or exhausted required full-review budget produces INCONCLUSIVE. Never use a generic reviewer fallback.

## Hard budget

- Initial FULL review: exactly 1.
- Escalation FULL re-review: at most 1.
- INCREMENTAL re-reviews: at most 2.
- Active reviewer agents: exactly 1; every follow-up uses the same proven thread.
- LOW-only findings do not trigger repairs.

## Evidence guard

Use `scripts/review_guard.py` for every manifest, receipt, validation, escalation, and decision. Store exact JSON artifacts in a private temporary directory outside the repository. Never edit receipts or reviewer results. If the guard, Git, Python, or a required capability is unavailable, stop with INCONCLUSIVE.

Receipts are deterministic links to original artifacts, not replacements for them. Runtime metadata must come from the Codex orchestration surface. Never manually reconstruct metadata to make a preflight or turn pass.

## 1. Deterministic validation

Run the cheapest relevant repository checks first. Record every command as:

```json
{"command":"...","exit_code":0,"output_sha256":"...","scope":"...","outcome":"PASSED|FAILED|INCONCLUSIVE"}
```

Create `validation.json` containing a non-empty `commands` array, then seal it:

```text
review_guard.py validation-receipt --validation validation.json > validation-receipt.json
```

Fix obvious failures before review when within task scope. An unreliable or unexecutable required check is INCONCLUSIVE.

## 2. Capability preflight

Before the expensive review, ask the runtime to prove all of these in exported metadata:

- configured and selected agent name is exactly `adversarial_reviewer`;
- `.codex/agents/adversarial-reviewer.toml` loaded;
- required model `gpt-5.6-terra` is available and selected;
- custom routing selected the configured agent;
- spawn completed and identity metadata is available;
- same-thread follow-ups are supported;
- stable spawn and thread IDs are present.

Seal the unmodified runtime metadata:

```text
review_guard.py preflight-receipt --metadata preflight-metadata.json > preflight-receipt.json
```

If the runtime cannot expose this evidence, return INCONCLUSIVE before review. A prompt, task label, or self-reported identity is not proof.

## 3. Freeze the complete scope

Choose the target in this order: explicit user scope, implementation from the current task, all current changes, then a reliable branch comparison. Capture with:

```text
review_guard.py capture [--base REF] [--include PATH ...] > manifest.json
```

The standard workflow must never pass `--allow-empty`. Empty scope, unresolved merge conflicts, unmerged index stages, `git diff --check` failure, unsupported status, unreadable content, or unresolved base is INCONCLUSIVE.

The manifest includes HEAD/merge-base, ADDED/MODIFIED/DELETED/RENAMED, staged/unstaged/untracked layers, full untracked content, per-file hashes, diff hash/size, semantic fingerprints, and its own canonical hash. Give the reviewer every scope path and the entire content of every untracked file.

Immediately before each reviewer turn, capture the same scope again. If it differs, rebuild the review target; never send a stale manifest.

## 4. Run and validate a reviewer turn

Spawn only `adversarial_reviewer`. Export the exact completed-turn metadata and seal it:

```text
review_guard.py turn-receipt --metadata turn.json --preflight preflight-receipt.json > turn-receipt.json
```

The first turn is `FULL`, sequence 1, parent null. Later turns must be delivered to the same thread, have contiguous sequence numbers, and name the preceding review as parent. A crash, timeout, cancellation, missing result, or wrong thread is INCONCLUSIVE.

Capture the worktree immediately after the turn and compare it with the before snapshot:

```text
review_guard.py compare --before before.json --after after.json > snapshot-receipt.json
```

Any change, including outside the review scope, invalidates the review. Save the exact reviewer JSON and create its receipt:

```text
review_guard.py validate-result \
  --result review.json --manifest manifest.json \
  --preflight preflight-receipt.json --turn turn-receipt.json \
  --snapshot snapshot-receipt.json > result-receipt.json
```

Protocol v2 requires a completed envelope, exact manifest, every reviewed path, `findings`, and `finding_verifications`. Empty output is invalid. `findings: []` is valid only as part of a complete result and never closes an earlier finding.

## 5. Preserve every finding lifecycle

The guard extracts immutable origin data directly from validated original results:

- `finding_id`
- `origin_review_id`
- `origin_result_sha256`
- `origin_manifest_sha256`
- `original_severity`
- `finding_content_sha256`

The main agent supplies only lifecycle state and evidence:

- classification: `CONFIRMED | REJECTED | UNCERTAIN`
- resolution: `OPEN | FIXED | NOT_APPLICABLE`
- `classification_evidence`
- fix and verification links when FIXED.

Allowed pairs are CONFIRMED+OPEN, CONFIRMED+FIXED, REJECTED+NOT_APPLICABLE, and UNCERTAIN+OPEN. Every validated finding must occur exactly once in `classifications.json`. Missing, invented, duplicated, renamed, or severity-mutated findings make the result INCONCLUSIVE.

For REJECTED CRITICAL/HIGH, evidence type must be one of: executable reproduction, passing regression test disproving the trigger, explicit acceptance criterion, documented contract, type invariant, validation logic proving the trigger unreachable, or concrete repository evidence. Executable/test evidence must link the passing validation receipt. Contract/invariant/repository evidence must link a reviewed manifest, path, and matching content hash. “Seems intentional” or an unlinked reference is not evidence.

## 6. Fix, escalate, and explicitly verify

For each CONFIRMED blocking finding, fix the root cause, add focused regression validation, and capture the fix manifest. Assess the transition:

```text
review_guard.py assess-fix --before origin-manifest.json --after fix-manifest.json \
  [--semantic public-api|schema|persistence|concurrency|core-invariant|architecture|core-algorithm] \
  > escalation-receipt.json
```

Manual semantic flags are additive. The guard also escalates automatically for new/unreviewed files, HEAD changes, major diff growth, exported/public symbols, routes, schemas/migrations, persistence/models, concurrency primitives, shared state, public types/interfaces, and serialization formats.

If escalation says FULL, an incremental turn cannot close the finding. Run a new FULL review on the entire fix manifest or return INCONCLUSIVE if the budget is exhausted.

Every re-review request must list each earlier CONFIRMED finding ID and its origin result hash. The same reviewer must return one `finding_verifications` entry per requested finding:

- `FIXED`: original trigger is gone on the fix manifest and regression test was verified;
- `STILL_PRESENT`: finding remains blocking;
- `UNRESOLVED`: CRITICAL/HIGH is INCONCLUSIVE.

A missing entry is INCONCLUSIVE. To record CONFIRMED+FIXED, include `fixed_in_manifest_sha256`, `verified_by_review_id`, `verified_by_result_sha256`, and `regression_validation.validation_receipt_sha256`. The verification result must be later, validated, target that exact fix manifest, and explicitly say FIXED.

## 7. Prove and decide the final version

Capture the final manifest, compare it unchanged immediately before decision, and seal it:

```text
review_guard.py final-manifest-receipt \
  --manifest final-manifest.json --snapshot final-snapshot-receipt.json \
  > final-manifest-receipt.json
```

The final manifest must equal the manifest in the last validated reviewer result. Then pass all original and receipt artifacts directly to the guard:

```text
review_guard.py decide \
  --result initial-review.json [--result recheck.json ...] \
  --manifest initial-manifest.json [--manifest fix-manifest.json ...] \
  --result-receipt initial-result-receipt.json [--result-receipt recheck-receipt.json ...] \
  --preflight-receipt preflight-receipt.json \
  --turn-receipt initial-turn-receipt.json [--turn-receipt recheck-turn-receipt.json ...] \
  --snapshot-receipt initial-snapshot.json [--snapshot-receipt recheck-snapshot.json ...] \
  --classifications classifications.json \
  --validation-receipt validation-receipt.json \
  --final-manifest final-manifest.json --final-manifest-receipt final-manifest-receipt.json \
  [--escalation-receipt escalation-receipt.json ...]
```

Do not supply or synthesize integrity booleans. Report exactly the guard state.

## Final response

Start with `Adversarial review: PASS | FAIL | INCONCLUSIVE`, then give review counts, confirmed/fixed/rejected/uncertain counts, final manifest hash, validation summary, remaining blockers, and inconclusive reasons. On PASS say only that no confirmed blocking defect or unresolved CRITICAL/HIGH uncertainty remains in the proven reviewed scope; never claim perfection.
