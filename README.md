# Codex Devil's Advocate

<p align="center">
  <img src="assets/C048235B-D8A8-42E4-A298-E52B5E3388A0.png" alt="Codex Devil's Advocate — token-efficient adversarial review for Codex" width="100%" />
</p>

<p align="center">
  <a href="README.md"><img src="https://img.shields.io/badge/English-dc2626?style=for-the-badge" alt="English" /></a>
  <a href="README.ru.md"><img src="https://img.shields.io/badge/Русский-18181b?style=for-the-badge" alt="Русский" /></a>
</p>

<p align="center">
  <strong>Codex wrote the solution. Now give it an opponent.</strong><br/>
  One agent builds it. Another challenges it and helps make the code better.
</p>

<p align="center">
  <a href="#installation"><img src="https://img.shields.io/badge/INSTALL-dc2626?style=for-the-badge&logo=gnubash&logoColor=white" alt="Install" /></a>
  <a href="#usage"><img src="https://img.shields.io/badge/USAGE-991b1b?style=for-the-badge&logo=windowsterminal&logoColor=white" alt="Usage" /></a>
  <a href="#runtime-compatibility-modes"><img src="https://img.shields.io/badge/RUNTIME%20MODES-991b1b?style=for-the-badge" alt="Runtime modes" /></a>
  <a href="#token-efficiency-by-design"><img src="https://img.shields.io/badge/LOW%20TOKEN%20DESIGN-dc2626?style=for-the-badge&logo=lightning&logoColor=white" alt="Low token design" /></a>
</p>

---

## Codex wrote the solution. Now give it an opponent.

The longer AI works on one task, the easier it is for it to **miss its own mistakes**.

Run:

```text
$adversarial-review
```

A separate reviewer challenges the solution, looks for weak spots, and helps turn it into **better code**.

**Not just another review. A second point of view built to disagree.**

No reviewer swarm. No endless loops. No burning tokens just to hear “looks good.”

---

## The idea

After Codex finishes a task, invoke:

```text
$adversarial-review
```

The skill now checks runtime compatibility **before** spending tokens on tests, a full manifest, or a reviewer:

```text
IMPLEMENT
   ↓
RUNTIME PREFLIGHT
   ├── STRICT + identity/sandbox cannot be attested
   │      ↓
   │  INCONCLUSIVE — STOP EARLY
   │
   └── supported / BEST_EFFORT
          ↓
CHEAP TESTS / BUILD / TYPECHECK
   ↓
FREEZE REVIEW SCOPE
   ↓
HASH MANIFEST + SNAPSHOT
   ↓
ONE ADVERSARIAL REVIEWER
   ↓
MAIN CODEX VERIFIES FINDINGS
   ↓
REVIEW INTEGRITY PROVEN?
   ├── YES → PASS / FAIL
   ├── ONLY RUNTIME ATTESTATION MISSING IN BEST_EFFORT → UNVERIFIED
   └── OTHER INTEGRITY FAILURE → INCONCLUSIVE
```

No reviewer swarm. No endless recursive review loop. No full repository reread after every fix.

---

## Runtime compatibility modes

Some Codex runtimes can load a custom-agent TOML but do not expose enough trusted spawn/thread metadata to prove which custom agent actually ran or which effective sandbox policy the child received. `task_name`, a child self-report, or the local TOML file are not sufficient proof.

Devil's Advocate handles that boundary explicitly instead of pretending the runtime is stronger than it is.

### STRICT — default

```text
$adversarial-review
```

STRICT requires runtime evidence for both:

```text
selected custom agent = adversarial_reviewer
effective sandbox     = read-only
```

If the current Codex surface cannot prove those facts, the skill stops **before** project tests, full-project scope enumeration, manifest hashing, or reviewer execution:

```text
Adversarial review: INCONCLUSIVE
Runtime attestation unavailable.
```

That prevents a known-incompatible runtime from consuming the full review budget when a trusted PASS/FAIL is impossible anyway.

### BEST_EFFORT — explicit opt-in

Ask Codex to run:

```text
$adversarial-review in BEST_EFFORT mode
```

BEST_EFFORT keeps the deterministic parts of the workflow:

- complete scope manifest
- before/after worktree snapshots
- exact reviewer JSON preservation
- protocol validation
- independent parent verification of findings
- bounded review/re-review budget

If exact custom-agent identity or effective sandbox cannot be attested, the result becomes:

```text
Adversarial review: UNVERIFIED
```

The findings can still be useful and independently checked, but `UNVERIFIED` is **never** presented as a certified PASS or FAIL.

When exact custom-agent selection is unavailable, BEST_EFFORT may use one generic child with the bundled `references/best-effort-reviewer.md` contract. STRICT never does this.

---

## Why this is different

| Feature | What it means |
|---|---|
| **Runtime preflight first** | Known-incompatible Codex runtimes stop before expensive tests/manifests/reviewer turns |
| **STRICT by default** | PASS/FAIL require trusted runtime identity and effective read-only sandbox attestation |
| **Optional BEST_EFFORT** | Useful review can continue as UNVERIFIED instead of discarding all findings |
| **1 reviewer only** | Avoids overlapping subagents and duplicated findings |
| **Diff-first** | Starts from the actual change instead of ingesting the whole repository |
| **Evidence-first** | Findings need a concrete trigger, execution path, and incorrect result |
| **Objective verification** | CRITICAL/HIGH rejections require executable or repository evidence |
| **Fail-closed core integrity** | Malformed output, incomplete scope, worktree mutation, or unreviewed final code still produce INCONCLUSIVE |
| **Bounded re-review** | One initial full review, up to one escalation full review, and at most two incremental rechecks |
| **Explicit-only invocation** | It does not consume review budget unless you ask for it |

---

## What the reviewer looks for

The reviewer attempts to falsify the implementation by searching for concrete failures involving:

- logic errors
- violated invariants
- invalid state transitions
- boundary conditions
- stale or partially initialized state
- regressions
- incorrect assumptions
- error and failure paths
- tests that pass without proving correctness
- concurrency problems when relevant
- security problems when relevant

A good finding should look like:

```text
trigger / state
→ reachable execution path
→ violated contract or invariant
→ observable incorrect result
```

If that chain cannot be established, the issue should not be treated as a confirmed defect.

---

## Protocol compatibility

The reviewer is instructed to return `confidence` as an integer from `0` to `100`:

```json
"confidence": 99
```

Real Codex model output can sometimes use a fractional form instead:

```json
"confidence": 0.99
```

The guard accepts both forms without rewriting the original reviewer response. Integers must be in `0..100`; fractional floats must be finite and in `0.0..1.0`.

This tolerance affects only the confidence field. It does **not** weaken identity, sandbox, manifest, reviewed-path, review-ID, status, or snapshot checks.

---

## Token-efficiency by design

The hard review budget after preflight is:

```text
Initial full adversarial reviews: 1
Escalation full reviews after substantial fixes: up to 1
Incremental re-reviews: up to 2
Concurrent reviewer subagents: 1
```

A failed STRICT runtime preflight uses zero reviewer turns.

Devil's Advocate reuses the same reviewer thread after fixes instead of repeatedly rebuilding repository context from scratch.

---

## Frozen scope

At the beginning of an actual review, the skill establishes one review target and keeps it stable.

The target is selected from, in order:

1. scope explicitly named by the user
2. implementation completed in the current task
3. current staged, unstaged, and untracked changes
4. branch comparison when it can be determined reliably

The target is frozen with HEAD and merge-base SHAs, file statuses, content hashes, a Git-status hash, and a canonical diff hash. Untracked files are listed explicitly and reviewed in full. The worktree is captured before and after every reviewer turn.

---

## False-positive filtering

The reviewer tries to disprove its own suspicions before reporting them. Then the main Codex agent independently checks every returned finding and classifies it as:

```text
CONFIRMED
REJECTED
UNCERTAIN
```

A CRITICAL or HIGH finding can be rejected only with objective evidence such as a reproduction, an exact regression test, a documented contract, a type invariant, validation logic, or a concrete repository reference. An unresolved UNCERTAIN CRITICAL/HIGH finding forbids PASS.

In degraded BEST_EFFORT mode those classifications can still be useful, but the overall result remains UNVERIFIED while runtime attestation is missing.

---

## Stop condition

PASS requires:

```text
runtime identity + sandbox attestation proven
+ review integrity proven
+ final scope hash matches the reviewed version
+ zero confirmed blocking correctness defects
+ zero unresolved UNCERTAIN CRITICAL/HIGH findings
```

Blocking severity levels are:

- `CRITICAL`
- `HIGH`
- `MEDIUM`

`UNVERIFIED` means the deterministic review artifacts are usable but runtime reviewer identity/isolation was not certified. `INCONCLUSIVE` means a stronger core-integrity requirement failed.

---

# Installation

Requirements: Git and Python 3. The bundled guard deliberately fails closed when it cannot capture or verify review state.

## Supported Codex surfaces

| Surface | Support |
|---|---|
| Codex desktop app, CLI, IDE extension | STRICT works when the active runtime exposes trusted custom-agent identity and effective sandbox metadata; otherwise it stops early |
| Same local surfaces with incomplete agent metadata | Use explicit BEST_EFFORT if you still want an UNVERIFIED review |
| Local plugin marketplace | Skill packaging is supported; run the companion installer once to install the custom agent |
| Codex cloud, ChatGPT Work/web/mobile | Not supported for the certified local workflow because the required local Git/Python/runtime guarantees are unavailable |

The plugin manifest packages the skill, but the current plugin format does not package standalone `.codex/agents/*.toml` files. A plugin-only install therefore cannot provide the strict custom reviewer until the companion agent is installed locally.

## Global install — recommended

### Windows

Clone or download this repository, open PowerShell in the repository root, then run:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

The installer copies the skill to:

```text
%USERPROFILE%\.agents\skills\adversarial-review\
```

and the custom reviewer to:

```text
%USERPROFILE%\.codex\agents\adversarial-reviewer.toml
```

If `CODEX_HOME` is set, the reviewer is installed under `%CODEX_HOME%\agents\` instead.

### macOS / Linux

```bash
chmod +x install.sh
./install.sh
```

The files are installed into:

```text
~/.agents/skills/adversarial-review/
~/.codex/agents/adversarial-reviewer.toml
```

If `CODEX_HOME` is set, the reviewer is installed under `$CODEX_HOME/agents/` instead.

Restart Codex if the skill was not detected immediately.

---

## Uninstall

### Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall.ps1
```

### macOS / Linux

```bash
chmod +x uninstall.sh
./uninstall.sh
```

---

# Usage

## Strict review

First let Codex implement your task normally, then run:

```text
$adversarial-review
```

STRICT is the default. If the runtime cannot certify the reviewer identity/sandbox contract, it exits early as INCONCLUSIVE.

## Best-effort fallback

```text
Run $adversarial-review in BEST_EFFORT mode.
```

Use this when your Codex build does not expose enough trusted custom-agent metadata but you still want useful findings. Expect `UNVERIFIED` unless strict attestation becomes available.

## Run it automatically after a task

```text
Implement the new inventory system.
Run the relevant tests.
Then run $adversarial-review.
```

The skill is explicit-only so you decide when deeper review is worth the budget.

---

# Example degraded runtime

Suppose the spawn interface returns only a task name and does not expose effective custom-agent identity or sandbox metadata.

STRICT:

```text
$adversarial-review

Adversarial review: INCONCLUSIVE
- Mode: STRICT
- Full reviews: 0/2
- Runtime attestation: missing identity and sandbox
- Inconclusive reasons: runtime preflight failed
```

BEST_EFFORT:

```text
Run $adversarial-review in BEST_EFFORT mode.

Adversarial review: UNVERIFIED
- Mode: BEST_EFFORT
- Full reviews: 1/2
- Runtime attestation: missing identity and sandbox
- Validation: completed
- Candidate findings: available
- Unverified reasons: reviewer identity/isolation not certified
```

---

# Architecture

```text
User
 │
 ▼
Main Codex Agent
 │
 ├── invokes skill
 └── runtime preflight
        │
        ├── STRICT unsupported → INCONCLUSIVE / STOP
        │
        └── supported or BEST_EFFORT
                 ↓
        deterministic checks
                 ↓
           frozen manifest
                 ↓
       one reviewer thread
                 ↓
       verify response + snapshots
                 ↓
        verify/fix findings
                 ↓
 PASS / FAIL / UNVERIFIED / INCONCLUSIVE
```

---

# Repository layout

```text
.codex-plugin/
└── plugin.json

skills/
└── adversarial-review/
    ├── SKILL.md
    ├── scripts/
    │   └── review_guard.py
    ├── references/
    │   └── best-effort-reviewer.md
    └── agents/
        └── openai.yaml

.codex/
└── agents/
    └── adversarial-reviewer.toml

install.ps1
install.sh
uninstall.ps1
uninstall.sh
tests/
└── test_review_guard.py
```

### `SKILL.md`

Controls runtime preflight, mode selection, review budget, scope selection, verification loop, repair loop, and stop condition.

### `review_guard.py`

Deterministically performs runtime preflight decisions, captures complete change manifests, verifies immutable snapshots and reviewer JSON, escalates large fixes, and computes PASS, FAIL, INCONCLUSIVE, or UNVERIFIED.

### `adversarial-reviewer.toml`

Defines the preferred independent read-only custom reviewer and its adversarial behavior.

### `best-effort-reviewer.md`

Provides the self-contained reviewer contract used only when BEST_EFFORT must fall back to a generic child.

---

# Why not just ask Codex to review its own code?

Because the agent that designed and implemented a solution already carries the assumptions that led to that solution.

A separate adversarial reviewer gets a different objective:

```text
Implementer:
"Make this work."

Reviewer:
"Find a concrete case proving this does not work."
```

That shift in objective is the core of adversarial review.

---

# Does this replace tests?

No.

The intended supported-runtime workflow is:

```text
runtime preflight
→ deterministic tests
→ adversarial reasoning
→ repair when requested
→ regression tests
→ targeted recheck
```

Tests and adversarial review catch different classes of failure.

---

# Does it guarantee bug-free software?

No — and it deliberately never claims to.

The strict success statement is:

> **No confirmed blocking defects remain in the proven reviewed scope.**

That is much more defensible than claiming an implementation is perfect.

---

## Quick start

```text
1. Clone/download this repository
2. Run install.ps1 or install.sh
3. Restart Codex if needed
4. Open any code project
5. Finish a non-trivial implementation
6. Run $adversarial-review (STRICT by default)
7. If your runtime cannot expose trusted reviewer metadata and you still want findings, explicitly request BEST_EFFORT
```

---

<p align="center">
  <strong>Your coding agent writes the solution.<br/>Devil's Advocate tries to prove it wrong.</strong>
</p>

<p align="center">
  <code>$adversarial-review</code>
</p>
