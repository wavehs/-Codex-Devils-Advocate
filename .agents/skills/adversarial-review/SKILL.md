---
name: adversarial-review
description: Explicit, token-efficient adversarial review of a completed code change. Use only when the user invokes $adversarial-review after implementation and wants concrete correctness defects found, verified, fixed, and incrementally rechecked.
---

# Adversarial Review

Run a bounded adversarial correctness loop with exactly ONE read-only reviewer.
Optimize for confirmed defects per token, not review breadth.

## Hard budget

- Full adversarial review: exactly 1.
- Incremental re-reviews: at most 2.
- Reviewer agents alive at once: exactly 1.
- Reuse the SAME reviewer thread for incremental re-reviews.
- Never spawn a fresh reviewer automatically.
- Do not loop on LOW, style-only, theoretical, or unproven concerns.

Blocking findings are confirmed CRITICAL, HIGH, or MEDIUM correctness defects.

## 1. Cheap validation first

Before using reviewer tokens, run the cheapest relevant deterministic checks already available in the repository, such as targeted tests, type checks, compilation, or static checks.

Prefer changed-area checks over a huge full-suite run unless repository instructions or the task require the full suite.
Fix obvious deterministic failures before adversarial review.

## 2. Freeze the review scope

At skill start, define one immutable review target.

Prefer in this order:
1. target explicitly named by the user;
2. implementation completed in the current task;
3. current staged + unstaged diff;
4. branch diff against the repository's normal base only when it can be determined reliably and cheaply.

Record a compact review brief containing:
- original requirement / acceptance criteria;
- base and target when applicable;
- changed-file list;
- current diff summary;
- relevant validation already run.

Do not silently widen the review into unrelated pre-existing code.

## 3. Context minimization

Use a DIFF-FIRST strategy.

Start from:
- the actual diff;
- changed symbols;
- directly related tests.

Expand only when needed to prove or disprove a defect:
- direct callers and callees;
- interfaces/contracts/types;
- state transitions and invariants;
- persistence/API/schema boundaries;
- code whose behavior can regress because of the change.

Prefer targeted search and small file ranges over broad repository scans.
Skip generated files, vendored code, large fixtures, lockfiles, and unrelated modules unless they are behaviorally relevant.
Do not paste large repository contents into the subagent prompt; give the reviewer the compact brief and let it inspect targeted context itself.

## 4. Spawn the reviewer

Spawn exactly ONE custom agent named `adversarial_reviewer`.
Wait for it to finish before modifying code.

Give it the frozen review brief and instruct it to:
- independently reconstruct intended behavior;
- attempt to falsify the implementation;
- inspect the diff first and expand context only as necessary;
- return only evidence-backed defects;
- remain read-only.

Do not defend the implementation or reveal your own confidence in specific areas.

## 5. Verify every finding

The main agent is the judge. The reviewer is not ground truth.

For each finding classify it as:
- CONFIRMED
- REJECTED
- UNCERTAIN

Confirm only when there is a plausible reachable path from trigger/state to observable incorrect behavior or a clearly violated repository contract.

Cheaply check callers, validation, types, tests, or framework behavior when that can settle a finding.
Reject false positives.
Do not modify correct code merely to satisfy the reviewer.
Do not spend large extra exploration budgets trying to prove a low-confidence suspicion.

## 6. Fix confirmed blocking findings

If no confirmed blocking findings remain, stop and PASS.

Otherwise:
- fix root causes, not symptoms;
- keep changes minimal and in scope;
- add focused regression tests when practical;
- run relevant deterministic validation.

Do not ask the reviewer to re-review LOW-only findings.

## 7. Incremental re-review

If fixes were made, route a follow-up to the SAME `adversarial_reviewer` thread.
Do not spawn another reviewer.

Provide only:
- the patch since the previous review;
- which confirmed findings were fixed;
- focused regression-test results;
- any small new context required by the fixes.

Ask it to inspect only:
1. whether the demonstrated failure paths are actually fixed;
2. whether the fixes introduced regressions in directly related paths;
3. whether the regression tests actually prove the intended behavior;
4. any new blocking defect exposed by the changed fix itself.

It must not restart a full repository review.

Verify its findings again as in step 5.
If blocking findings are confirmed, fix and validate, then perform one more incremental re-review if budget remains.

## 8. Stop condition

PASS when all are true:
- relevant deterministic validation passes;
- no CONFIRMED CRITICAL/HIGH/MEDIUM defect remains in the frozen scope.

Do not require zero LOW findings or zero theoretical risk.
Do not claim the code is bug-free.

If the two incremental re-reviews are exhausted and a blocking defect still remains, stop the adversarial loop, report FAIL, and state the remaining confirmed defect. Do not exceed the review budget automatically.

A new full `$adversarial-review` is justified only if the fixes materially changed the architecture, core algorithm, or review scope.

## Final response

Keep it concise:

`Adversarial review: PASS | FAIL`

- Full reviews: 1
- Incremental re-reviews: N/2
- Confirmed: Critical N / High N / Medium N
- Fixed: N
- Rejected false positives: N
- Validation: <short list>
- Remaining blocking defects: none | <short list>

On PASS, say only that no confirmed blocking defects remain in the reviewed scope; never say the implementation is perfect or bug-free.
