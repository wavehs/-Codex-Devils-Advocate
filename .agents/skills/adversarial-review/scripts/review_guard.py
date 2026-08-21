#!/usr/bin/env python3
"""Fail-closed evidence guard for the adversarial-review skill.

The guard deliberately accepts artifacts, not conclusions.  Receipts are hashes of
runtime metadata and validated files; they are useful only while accompanied by the
original artifact from which they were produced.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


PROTOCOL_VERSION = "2"
REVIEWER_NAME = "adversarial_reviewer"
REVIEWER_MODEL = "gpt-5.6-terra"
SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
BLOCKING = {"CRITICAL", "HIGH", "MEDIUM"}
CLASSIFICATIONS = {"CONFIRMED", "REJECTED", "UNCERTAIN"}
RESOLUTIONS = {"OPEN", "FIXED", "NOT_APPLICABLE"}
VERIFICATIONS = {"FIXED", "STILL_PRESENT", "UNRESOLVED"}
EVIDENCE_TYPES = {
    "executable_reproduction",
    "passing_regression_test",
    "acceptance_criterion",
    "documented_contract",
    "type_invariant",
    "validation_logic",
    "repository_evidence",
}


class GuardError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sha(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_sha(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def normalize_path(raw_path: str) -> str:
    candidate = PurePosixPath(raw_path.replace("\\", "/"))
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        raise GuardError(f"unsafe repository path: {raw_path!r}")
    return candidate.as_posix()


def path_fingerprint(repo: Path, rel: str) -> dict[str, Any]:
    path = repo / normalize_path(rel)
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return {"kind": "missing", "size": None, "sha256": None}
    except OSError as exc:
        raise GuardError(f"cannot stat {rel}: {exc}") from exc
    if stat.S_ISLNK(mode):
        target = os.readlink(path)
        payload = b"symlink\0" + os.fsencode(target)
        return {"kind": "symlink", "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    if stat.S_ISREG(mode):
        return {"kind": "file", "size": path.stat().st_size, "sha256": file_sha(path)}
    if stat.S_ISDIR(mode):
        payload = _run_bytes(str(path), "rev-parse", "HEAD", check=False).stdout + b"\0" + _run_bytes(str(path), "status", "--porcelain=v2", "-z", check=False).stdout
        return {"kind": "directory", "size": None, "sha256": hashlib.sha256(payload).hexdigest()}
    raise GuardError(f"unsupported filesystem object in scope: {rel}")


def _run(repo: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args], cwd=repo, text=True,
        encoding="utf-8", errors="surrogateescape", capture_output=True, check=False
    )
    if check and proc.returncode:
        raise GuardError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc


def _run_bytes(repo: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    proc = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args], cwd=repo,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
    )
    if check and proc.returncode:
        raise GuardError(f"git {' '.join(args)} failed: {proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc


def _zsplit(value: str) -> list[str]:
    return [item for item in value.split("\0") if item]


def _change_name(code: str) -> str:
    names = {"A": "ADDED", "M": "MODIFIED", "D": "DELETED", "R": "RENAMED", "C": "COPIED"}
    if code == "U":
        raise GuardError("repository contains unresolved merge conflicts")
    if code not in names:
        raise GuardError(f"unsupported git status: {code}")
    return names[code]


def parse_name_status(raw: str, layer: str) -> list[dict[str, Any]]:
    fields = _zsplit(raw)
    result: list[dict[str, Any]] = []
    i = 0
    while i < len(fields):
        status = fields[i]
        i += 1
        code = status[0]
        if code in {"R", "C"}:
            if i + 1 >= len(fields):
                raise GuardError("malformed rename/copy status")
            old_path, path = normalize_path(fields[i]), normalize_path(fields[i + 1])
            i += 2
            result.append({"path": path, "old_path": old_path, "layer": layer, "change_type": _change_name(code)})
        else:
            if i >= len(fields):
                raise GuardError("malformed name-status output")
            path = normalize_path(fields[i])
            i += 1
            result.append({"path": path, "layer": layer, "change_type": _change_name(code)})
    return result


def _check_repository(repo: str, base_range: str | None) -> None:
    if _run(repo, "ls-files", "-u", "-z").stdout:
        raise GuardError("repository contains unresolved merge conflicts")
    checks = [("diff", "--check"), ("diff", "--cached", "--check", "HEAD")]
    if base_range:
        checks.append(("diff", "--check", base_range))
    for args in checks:
        proc = _run(repo, *args, check=False)
        if proc.returncode:
            detail = proc.stdout.strip() or proc.stderr.strip()
            raise GuardError(f"git {' '.join(args)} failed: {detail}")


SEMANTIC_PATTERNS: dict[str, re.Pattern[str]] = {
    "public-api": re.compile(r"\b(export\s+|public\s+|__all__|module\.exports|exports\.|@app\.|router\.|Route\b)"),
    "schema": re.compile(r"\b(CREATE|ALTER|DROP)\s+(TABLE|TYPE|INDEX)|\b(migration|schema)\b", re.I),
    "persistence": re.compile(r"\b(repository|database|db\.|query\(|execute\(|Model\b|Entity\b|INSERT|UPDATE|DELETE)\b", re.I),
    "concurrency": re.compile(r"\b(async|await|mutex|lock|semaphore|thread|atomic|synchronized|Promise\.all)\b", re.I),
    "shared-state": re.compile(r"\b(global|singleton|shared|static\s+(?!final|const)|cache)\b", re.I),
    "public-types": re.compile(r"\b(export\s+)?(interface|type|enum|class|struct)\s+\w+"),
    "serialization": re.compile(r"\b(JSON\.|serialize|deserialize|marshal|unmarshal|to_dict|from_dict|serde)\b", re.I),
}
PATH_SEMANTICS = {
    "api-routes": re.compile(r"(^|/)(api|routes?|controllers?)(/|\.)", re.I),
    "schema": re.compile(r"(^|/)(migrations?|schema)(/|\.)", re.I),
    "persistence": re.compile(r"(^|/)(models?|repositories|persistence|database|db)(/|\.)", re.I),
}


def semantic_fingerprints(repo: str, paths: Iterable[str]) -> dict[str, dict[str, str]]:
    output: dict[str, dict[str, str]] = {}
    root = Path(repo)
    for rel in sorted(set(paths)):
        path = root / rel
        if not path.is_file() or path.is_symlink():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        matched: dict[str, list[str]] = {}
        for category, pattern in SEMANTIC_PATTERNS.items():
            selected = [line.strip() for line in lines if pattern.search(line)]
            if selected:
                matched[category] = selected
        for category, pattern in PATH_SEMANTICS.items():
            if pattern.search(rel):
                matched.setdefault(category, []).append(f"FILE:{file_sha(path)}")
        if matched:
            output[rel] = {key: sha(value) for key, value in sorted(matched.items())}
    return output


def build_manifest(repo: str, base: str | None, includes: list[str], allow_empty: bool = False) -> dict[str, Any]:
    requested = str(Path(repo).resolve())
    repo = str(Path(_run(requested, "rev-parse", "--show-toplevel").stdout.strip()).resolve())
    head = _run(repo, "rev-parse", "HEAD").stdout.strip()
    merge_base = None
    base_range = None
    changes: list[dict[str, Any]] = []
    if base:
        merge_base = _run(repo, "merge-base", base, "HEAD").stdout.strip()
        base_range = f"{merge_base}..HEAD"
    _check_repository(repo, base_range)
    if base_range:
        changes += parse_name_status(_run(repo, "diff", "--name-status", "-z", "-M", base_range).stdout, "branch")
    changes += parse_name_status(_run(repo, "diff", "--cached", "--name-status", "-z", "-M", "HEAD").stdout, "staged")
    changes += parse_name_status(_run(repo, "diff", "--name-status", "-z", "-M").stdout, "unstaged")
    for path in _zsplit(_run(repo, "ls-files", "--others", "--exclude-standard", "-z").stdout):
        changes.append({"path": path, "layer": "untracked", "change_type": "UNTRACKED"})

    for rel in map(normalize_path, includes):
        changes.append({"path": rel, "layer": "explicit", "change_type": "INCLUDED"})
    scope_paths = sorted({c["path"] for c in changes})
    if not scope_paths and not allow_empty:
        raise GuardError("review scope contains no files")

    baseline_paths: dict[str, str | None] = {}
    for c in changes:
        if c.get("old_path"):
            baseline_paths.setdefault(c["path"], c["old_path"])
    scope_files = []
    for rel in scope_paths:
        current = Path(repo) / rel
        baseline_path = baseline_paths.get(rel, rel)
        baseline_ref = merge_base or "HEAD"
        old = _run_bytes(repo, "show", f"{baseline_ref}:{baseline_path}", check=False)
        baseline_sha = hashlib.sha256(old.stdout).hexdigest() if old.returncode == 0 else None
        scope_files.append({
            "path": rel,
            "status": sorted({c["change_type"] for c in changes if c["path"] == rel}),
            "current": path_fingerprint(Path(repo), rel),
            "baseline_sha256": baseline_sha,
        })
    untracked = []
    for c in changes:
        if c["layer"] != "untracked":
            continue
        path = Path(repo) / c["path"]
        if not path.is_file() or path.is_symlink():
            raise GuardError(f"untracked scope entry is not a regular file: {c['path']}")
        raw = path.read_bytes()
        try:
            text_utf8 = raw.decode("utf-8")
        except UnicodeDecodeError:
            text_utf8 = None
        untracked.append({
            "path": c["path"], "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw),
            "content_base64": base64.b64encode(raw).decode("ascii"),
            "text_utf8": text_utf8,
        })
    diff_material = {
        "branch": _run(repo, "diff", "--binary", base_range).stdout if base_range else "",
        "staged": _run(repo, "diff", "--cached", "--binary", "HEAD").stdout,
        "unstaged": _run(repo, "diff", "--binary").stdout,
        "untracked": untracked,
    }
    manifest: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "head_sha": head,
        "merge_base_sha": merge_base,
        "base": base,
        "changes": changes,
        "scope_files": scope_files,
        "scope_empty": not scope_files,
        "scope_complete": True,
        "untracked_files": untracked,
        "git_status_sha256": hashlib.sha256(_run(repo, "status", "--porcelain=v2", "-z", "--untracked-files=all").stdout.encode()).hexdigest(),
        "diff_sha256": sha(diff_material),
        "diff_bytes": len(canonical(diff_material)),
        "semantic_fingerprints": semantic_fingerprints(repo, scope_paths),
    }
    manifest["manifest_sha256"] = sha(manifest)
    validate_manifest(manifest, allow_empty=allow_empty)
    return manifest


def validate_manifest(manifest: dict[str, Any], allow_empty: bool = False) -> dict[str, Any]:
    if manifest.get("protocol_version") != PROTOCOL_VERSION:
        raise GuardError("invalid manifest protocol")
    if not manifest.get("scope_complete"):
        raise GuardError("review scope is incomplete")
    if (manifest.get("scope_empty") or not manifest.get("scope_files")) and not allow_empty:
        raise GuardError("review scope is empty")
    if any("UNMERGED" in item.get("status", []) for item in manifest.get("scope_files", [])):
        raise GuardError("repository contains unresolved merge conflicts")
    expected = dict(manifest)
    actual = expected.pop("manifest_sha256", None)
    if not actual or actual != sha(expected):
        raise GuardError("manifest hash mismatch")
    paths = [item.get("path") for item in manifest["scope_files"]]
    if len(paths) != len(set(paths)) or any(not isinstance(p, str) or not p for p in paths):
        raise GuardError("invalid scope paths")
    if any(normalize_path(p) != p for p in paths):
        raise GuardError("unsafe or non-canonical scope path")
    return manifest


def seal(receipt_type: str, fields: dict[str, Any]) -> dict[str, Any]:
    receipt = {"protocol_version": PROTOCOL_VERSION, "receipt_type": receipt_type, **fields}
    receipt["receipt_sha256"] = sha(receipt)
    return receipt


def validate_receipt(receipt: dict[str, Any], receipt_type: str) -> dict[str, Any]:
    if receipt.get("protocol_version") != PROTOCOL_VERSION or receipt.get("receipt_type") != receipt_type:
        raise GuardError(f"invalid {receipt_type} receipt")
    body = dict(receipt)
    actual = body.pop("receipt_sha256", None)
    if not actual or actual != sha(body):
        raise GuardError(f"{receipt_type} receipt hash mismatch")
    return receipt


def make_preflight_receipt(metadata: dict[str, Any]) -> dict[str, Any]:
    required = {
        "configured_agent_name": REVIEWER_NAME, "selected_agent_name": REVIEWER_NAME,
        "config_status": "LOADED", "model": REVIEWER_MODEL, "model_status": "AVAILABLE",
        "routing_status": "SELECTED", "spawn_status": "COMPLETED", "metadata_status": "AVAILABLE",
        "followup_status": "SUPPORTED",
    }
    for key, expected in required.items():
        if metadata.get(key) != expected:
            raise GuardError(f"preflight cannot prove {key}={expected}")
    if not str(metadata.get("config_path", "")).endswith("adversarial-reviewer.toml"):
        raise GuardError("reviewer configuration was not proven loaded")
    for key in ("probe_id", "thread_id", "spawn_id"):
        if not isinstance(metadata.get(key), str) or not metadata[key]:
            raise GuardError(f"preflight missing {key}")
    return seal("preflight", {
        "raw_metadata_sha256": sha(metadata), "probe_id": metadata["probe_id"],
        "agent_name": REVIEWER_NAME, "model": REVIEWER_MODEL,
        "thread_id": metadata["thread_id"], "spawn_id": metadata["spawn_id"],
    })


def make_turn_receipt(metadata: dict[str, Any], preflight: dict[str, Any]) -> dict[str, Any]:
    validate_receipt(preflight, "preflight")
    sequence = metadata.get("sequence")
    if not isinstance(sequence, int) or sequence < 1:
        raise GuardError("invalid review sequence")
    expected_mode = "SPAWN" if sequence == 1 else "FOLLOWUP"
    checks = {
        "agent_name": preflight["agent_name"], "model": preflight["model"],
        "thread_id": preflight["thread_id"], "turn_mode": expected_mode,
        "delivery_status": "DELIVERED", "completion_status": "COMPLETED", "result_status": "RECEIVED",
    }
    for key, expected in checks.items():
        if metadata.get(key) != expected:
            raise GuardError(f"review turn cannot prove {key}={expected}")
    for key in ("turn_id", "review_id"):
        if not isinstance(metadata.get(key), str) or not metadata[key]:
            raise GuardError(f"review turn missing {key}")
    kind = metadata.get("review_kind")
    if kind not in {"FULL", "INCREMENTAL"} or (sequence == 1 and kind != "FULL"):
        raise GuardError("invalid review kind")
    parent = metadata.get("parent_review_id")
    if sequence == 1 and parent is not None or sequence > 1 and not parent:
        raise GuardError("invalid review parent")
    return seal("review-turn", {
        "raw_metadata_sha256": sha(metadata), "preflight_receipt_sha256": preflight["receipt_sha256"],
        "turn_id": metadata["turn_id"], "review_id": metadata["review_id"], "review_kind": kind,
        "sequence": sequence, "parent_review_id": parent, "thread_id": metadata["thread_id"],
        "agent_name": metadata["agent_name"], "model": metadata["model"],
    })


def make_snapshot_receipt(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    validate_manifest(before)
    validate_manifest(after)
    return seal("snapshot-compare", {
        "before_manifest_sha256": before["manifest_sha256"],
        "after_manifest_sha256": after["manifest_sha256"],
        "match": before["manifest_sha256"] == after["manifest_sha256"],
    })


def _finding_hashes(findings: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [{"finding_id": item["id"], "finding_content_sha256": sha(item)} for item in findings]


def validate_review_result(result: dict[str, Any], manifest: dict[str, Any], turn: dict[str, Any] | None = None) -> dict[str, Any]:
    validate_manifest(manifest)
    if result.get("protocol_version") != PROTOCOL_VERSION or result.get("reviewer_name") != REVIEWER_NAME:
        raise GuardError("wrong or missing reviewer identity")
    if result.get("status") != "COMPLETED":
        raise GuardError("reviewer did not complete")
    if result.get("scope_manifest_sha256") != manifest["manifest_sha256"]:
        raise GuardError("review result targets a different manifest")
    if turn:
        validate_receipt(turn, "review-turn")
        for key in ("review_id", "review_kind", "sequence", "parent_review_id"):
            if result.get(key) != turn.get(key):
                raise GuardError(f"review result does not match turn {key}")
    required_paths = {item["path"] for item in manifest["scope_files"]}
    reviewed = result.get("reviewed_paths")
    if not isinstance(reviewed, list) or not required_paths.issubset(set(reviewed)):
        raise GuardError("review result does not cover every scope path")
    findings = result.get("findings")
    verifications = result.get("finding_verifications")
    if not isinstance(findings, list) or not isinstance(verifications, list) or not isinstance(result.get("summary"), str):
        raise GuardError("invalid reviewer result format")
    ids: set[str] = set()
    for finding in findings:
        required = {"id", "severity", "confidence", "location", "violated_contract", "trigger", "execution_path", "actual", "expected", "regression_test"}
        if not required.issubset(finding) or finding["severity"] not in SEVERITIES or not isinstance(finding["location"], dict):
            raise GuardError("invalid finding")
        if not finding["id"] or finding["id"] in ids:
            raise GuardError("duplicate or empty finding id")
        ids.add(finding["id"])
    verification_ids: set[str] = set()
    for item in verifications:
        required = {"finding_id", "origin_result_sha256", "status", "evidence", "regression_test_verified"}
        if not required.issubset(item) or item["status"] not in VERIFICATIONS or not isinstance(item["regression_test_verified"], bool):
            raise GuardError("invalid finding verification")
        if not item["finding_id"] or item["finding_id"] in verification_ids or not item["origin_result_sha256"]:
            raise GuardError("duplicate or incomplete finding verification")
        verification_ids.add(item["finding_id"])
    return result


def make_result_receipt(result: dict[str, Any], manifest: dict[str, Any], preflight: dict[str, Any], turn: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    validate_receipt(preflight, "preflight")
    validate_receipt(turn, "review-turn")
    validate_receipt(snapshot, "snapshot-compare")
    if turn["preflight_receipt_sha256"] != preflight["receipt_sha256"]:
        raise GuardError("turn is linked to another preflight")
    target_manifest = manifest.get("manifest_sha256")
    if not snapshot["match"] or snapshot["before_manifest_sha256"] != target_manifest or snapshot["after_manifest_sha256"] != target_manifest:
        raise GuardError("worktree changed during review")
    validate_review_result(result, manifest, turn)
    result_hash = sha(result)
    finding_hashes = _finding_hashes(result["findings"])
    return seal("review-result", {
        "result_sha256": result_hash, "review_id": result["review_id"], "review_kind": result["review_kind"],
        "sequence": result["sequence"], "parent_review_id": result["parent_review_id"],
        "reviewer_name": result["reviewer_name"], "manifest_sha256": manifest["manifest_sha256"],
        "thread_id": turn["thread_id"], "turn_receipt_sha256": turn["receipt_sha256"],
        "snapshot_receipt_sha256": snapshot["receipt_sha256"], "finding_ids": [x["finding_id"] for x in finding_hashes],
        "finding_hashes": finding_hashes, "findings_sha256": sha(finding_hashes),
        "verification_ids": [x["finding_id"] for x in result["finding_verifications"]],
        "verifications_sha256": sha(result["finding_verifications"]),
    })


def make_validation_receipt(raw: dict[str, Any]) -> dict[str, Any]:
    commands = raw.get("commands")
    if not isinstance(commands, list) or not commands:
        raise GuardError("validation has no commands")
    status = "PASSED"
    for item in commands:
        if not {"command", "exit_code", "output_sha256", "scope", "outcome"}.issubset(item):
            raise GuardError("invalid validation command")
        if item["outcome"] not in {"PASSED", "FAILED", "INCONCLUSIVE"}:
            raise GuardError("invalid validation outcome")
        if item["outcome"] == "INCONCLUSIVE":
            status = "INCONCLUSIVE"
        elif status != "INCONCLUSIVE" and (item["outcome"] == "FAILED" or item["exit_code"] != 0):
            status = "FAILED"
    return seal("deterministic-validation", {"raw_validation_sha256": sha(raw), "status": status, "commands_sha256": sha(commands)})


def make_final_manifest_receipt(manifest: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    validate_manifest(manifest)
    validate_receipt(snapshot, "snapshot-compare")
    target = manifest["manifest_sha256"]
    if not snapshot["match"] or snapshot["before_manifest_sha256"] != target or snapshot["after_manifest_sha256"] != target:
        raise GuardError("final manifest snapshot does not match")
    return seal("final-manifest", {"manifest_sha256": target, "snapshot_receipt_sha256": snapshot["receipt_sha256"]})


def assess_fix(before: dict[str, Any], after: dict[str, Any], semantics: list[str]) -> dict[str, Any]:
    validate_manifest(before)
    validate_manifest(after)
    reasons: list[str] = []
    before_paths = {item["path"] for item in before["scope_files"]}
    after_paths = {item["path"] for item in after["scope_files"]}
    new_paths = sorted(after_paths - before_paths)
    if new_paths:
        reasons.append("new-files:" + ",".join(new_paths))
    if before["head_sha"] != after["head_sha"]:
        reasons.append("head-changed")
    if after.get("diff_bytes", 0) > max(4096, int(before.get("diff_bytes", 0) * 1.75)):
        reasons.append("significant-diff-growth")
    before_fp = before.get("semantic_fingerprints", {})
    after_fp = after.get("semantic_fingerprints", {})
    for path in sorted(set(before_fp) | set(after_fp)):
        categories = set(before_fp.get(path, {})) | set(after_fp.get(path, {}))
        for category in sorted(categories):
            if before_fp.get(path, {}).get(category) != after_fp.get(path, {}).get(category):
                reasons.append(f"auto-semantic:{category}:{path}")
    reasons.extend(f"semantic:{item}" for item in semantics)
    reasons = list(dict.fromkeys(reasons))
    return {"full_review_required": bool(reasons), "reasons": reasons}


def make_escalation_receipt(before: dict[str, Any], after: dict[str, Any], semantics: list[str]) -> dict[str, Any]:
    assessment = assess_fix(before, after, semantics)
    return seal("fix-escalation", {
        "before_manifest_sha256": before["manifest_sha256"], "after_manifest_sha256": after["manifest_sha256"],
        **assessment,
    })


def _index_by(items: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        value = item.get(key)
        if not isinstance(value, str) or not value or value in result:
            raise GuardError(f"duplicate or missing {label} {key}")
        result[value] = item
    return result


def decide_from_artifacts(
    results: list[dict[str, Any]], manifests: list[dict[str, Any]], result_receipts: list[dict[str, Any]],
    classifications: dict[str, Any], validation_receipt: dict[str, Any], final_manifest: dict[str, Any],
    final_manifest_receipt: dict[str, Any], escalation_receipts: list[dict[str, Any]],
    preflight_receipt: dict[str, Any], turn_receipts: list[dict[str, Any]], snapshot_receipts: list[dict[str, Any]],
    max_full_reviews: int = 2, max_incremental_reviews: int = 2,
) -> dict[str, Any]:
    """Derive final state. Any broken evidence chain is INCONCLUSIVE."""
    try:
        for m in manifests:
            validate_manifest(m)
        validate_manifest(final_manifest)
        validate_receipt(preflight_receipt, "preflight")
        if preflight_receipt.get("agent_name") != REVIEWER_NAME or preflight_receipt.get("model") != REVIEWER_MODEL or not preflight_receipt.get("thread_id"):
            raise GuardError("preflight does not prove the required custom reviewer")
        validate_receipt(validation_receipt, "deterministic-validation")
        validate_receipt(final_manifest_receipt, "final-manifest")
        for receipt in turn_receipts:
            validate_receipt(receipt, "review-turn")
        for receipt in snapshot_receipts:
            validate_receipt(receipt, "snapshot-compare")
        for receipt in result_receipts:
            validate_receipt(receipt, "review-result")
        for receipt in escalation_receipts:
            validate_receipt(receipt, "fix-escalation")

        manifest_by_hash = _index_by(manifests, "manifest_sha256", "manifest")
        receipt_by_result = _index_by(result_receipts, "result_sha256", "result receipt")
        turn_by_hash = _index_by(turn_receipts, "receipt_sha256", "turn receipt")
        snapshot_by_hash = _index_by(snapshot_receipts, "receipt_sha256", "snapshot receipt")
        if len(results) != len(result_receipts) or not results:
            raise GuardError("every original reviewer result must have exactly one receipt")
        if len(turn_receipts) != len(result_receipts):
            raise GuardError("every reviewer result must have exactly one turn receipt")
        ordered = sorted(results, key=lambda item: item.get("sequence", 0))
        if [x.get("sequence") for x in ordered] != list(range(1, len(ordered) + 1)):
            raise GuardError("review sequence is not contiguous")
        if ordered[0].get("review_kind") != "FULL" or ordered[0].get("parent_review_id") is not None:
            raise GuardError("review chain must begin with a full review")
        if sum(x.get("review_kind") == "FULL" for x in ordered) > max_full_reviews or sum(x.get("review_kind") == "INCREMENTAL" for x in ordered) > max_incremental_reviews:
            raise GuardError("review budget exceeded")

        findings: dict[str, dict[str, Any]] = {}
        verifications: dict[tuple[str, str], dict[str, Any]] = {}
        reviews: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        previous_id = None
        thread_id = None
        for result in ordered:
            result_hash = sha(result)
            receipt = receipt_by_result.get(result_hash)
            if not receipt:
                raise GuardError("original reviewer result is missing its validated receipt")
            manifest = manifest_by_hash.get(receipt["manifest_sha256"])
            if not manifest:
                raise GuardError("review manifest is missing")
            validate_review_result(result, manifest)
            finding_hashes = _finding_hashes(result["findings"])
            receipt_checks = {
                "review_id": result["review_id"], "review_kind": result["review_kind"],
                "sequence": result["sequence"], "parent_review_id": result["parent_review_id"],
                "reviewer_name": result["reviewer_name"], "manifest_sha256": result["scope_manifest_sha256"],
                "finding_ids": [x["finding_id"] for x in finding_hashes], "finding_hashes": finding_hashes,
                "findings_sha256": sha(finding_hashes),
                "verification_ids": [x["finding_id"] for x in result["finding_verifications"]],
                "verifications_sha256": sha(result["finding_verifications"]),
            }
            if any(receipt.get(key) != expected for key, expected in receipt_checks.items()):
                raise GuardError("review result receipt does not match original result")
            turn = turn_by_hash.get(receipt.get("turn_receipt_sha256"))
            snapshot = snapshot_by_hash.get(receipt.get("snapshot_receipt_sha256"))
            if not turn or turn.get("preflight_receipt_sha256") != preflight_receipt["receipt_sha256"]:
                raise GuardError("review result is not linked to the validated custom-agent preflight")
            if any(turn.get(key) != result.get(key) for key in ("review_id", "review_kind", "sequence", "parent_review_id")):
                raise GuardError("review result is not linked to its completed turn")
            if turn.get("agent_name") != REVIEWER_NAME or turn.get("model") != REVIEWER_MODEL:
                raise GuardError("review turn used the wrong custom reviewer")
            if not snapshot or not snapshot.get("match") or snapshot.get("before_manifest_sha256") != manifest["manifest_sha256"] or snapshot.get("after_manifest_sha256") != manifest["manifest_sha256"]:
                raise GuardError("reviewer changed the worktree or reviewed a different snapshot")
            if previous_id is not None and result.get("parent_review_id") != previous_id:
                raise GuardError("review follow-up chain is broken")
            if thread_id is None:
                thread_id = receipt["thread_id"]
            elif receipt["thread_id"] != thread_id:
                raise GuardError("follow-up used a different reviewer thread")
            previous_id = result["review_id"]
            reviews[result["review_id"]] = (result, receipt)
            for finding in result["findings"]:
                if finding["id"] in findings:
                    raise GuardError("finding id was reused or mutated")
                findings[finding["id"]] = {
                    "finding": finding, "origin_review_id": result["review_id"], "origin_result_sha256": result_hash,
                    "origin_manifest_sha256": result["scope_manifest_sha256"], "finding_content_sha256": sha(finding),
                }
            for item in result["finding_verifications"]:
                if item["finding_id"] not in findings or findings[item["finding_id"]]["origin_result_sha256"] != item["origin_result_sha256"]:
                    raise GuardError("verification has no immutable origin")
                if findings[item["finding_id"]]["origin_review_id"] == result["review_id"]:
                    raise GuardError("a finding cannot verify itself in its origin review")
                verifications[(result["review_id"], item["finding_id"])] = item

        if final_manifest_receipt["manifest_sha256"] != final_manifest["manifest_sha256"]:
            raise GuardError("final manifest receipt mismatch")
        final_snapshot = snapshot_by_hash.get(final_manifest_receipt.get("snapshot_receipt_sha256"))
        if not final_snapshot or not final_snapshot.get("match") or final_snapshot.get("before_manifest_sha256") != final_manifest["manifest_sha256"] or final_snapshot.get("after_manifest_sha256") != final_manifest["manifest_sha256"]:
            raise GuardError("final manifest is not backed by an unchanged snapshot")
        last_result, last_receipt = reviews[ordered[-1]["review_id"]]
        if last_receipt["manifest_sha256"] != final_manifest["manifest_sha256"]:
            raise GuardError("final version was not reviewed")

        lifecycle_list = classifications.get("findings")
        if classifications.get("protocol_version") != PROTOCOL_VERSION or not isinstance(lifecycle_list, list):
            raise GuardError("invalid classification artifact")
        lifecycle = _index_by(lifecycle_list, "finding_id", "classification")
        if set(lifecycle) != set(findings):
            raise GuardError("a validated reviewer finding was omitted or invented")

        escalation_by_pair = {
            (r["before_manifest_sha256"], r["after_manifest_sha256"]): r for r in escalation_receipts
        }
        state = "PASS"
        reasons: list[str] = []
        output_lifecycle: list[dict[str, Any]] = []
        for fid, origin in findings.items():
            finding = origin["finding"]
            entry = lifecycle[fid]
            immutable = {
                "origin_review_id": origin["origin_review_id"], "origin_result_sha256": origin["origin_result_sha256"],
                "origin_manifest_sha256": origin["origin_manifest_sha256"], "original_severity": finding["severity"],
                "finding_content_sha256": origin["finding_content_sha256"],
            }
            if any(entry.get(k) != v for k, v in immutable.items()):
                raise GuardError(f"immutable finding history changed for {fid}")
            classification, resolution = entry.get("classification"), entry.get("resolution")
            allowed = {("CONFIRMED", "OPEN"), ("CONFIRMED", "FIXED"), ("REJECTED", "NOT_APPLICABLE"), ("UNCERTAIN", "OPEN")}
            if classification not in CLASSIFICATIONS or resolution not in RESOLUTIONS or (classification, resolution) not in allowed:
                raise GuardError(f"invalid lifecycle state for {fid}")
            evidence = entry.get("classification_evidence")
            if not isinstance(evidence, dict) or not all(evidence.get(k) for k in ("type", "reference", "details")):
                raise GuardError(f"missing classification evidence for {fid}")
            severity = finding["severity"]
            if classification == "REJECTED" and severity in {"CRITICAL", "HIGH"}:
                evidence_type = evidence.get("type")
                proof = evidence.get("proof")
                if evidence_type not in EVIDENCE_TYPES or not isinstance(proof, dict):
                    raise GuardError(f"rejected {severity} finding lacks objective evidence")
                if evidence_type in {"executable_reproduction", "passing_regression_test"}:
                    if proof.get("validation_receipt_sha256") != validation_receipt["receipt_sha256"] or validation_receipt["status"] != "PASSED":
                        raise GuardError(f"rejected {severity} finding has no passing validation proof")
                else:
                    proof_manifest = manifest_by_hash.get(proof.get("manifest_sha256"))
                    proof_path = proof.get("path")
                    proof_content = proof.get("content_sha256")
                    matched_file = next((x for x in proof_manifest.get("scope_files", []) if x.get("path") == proof_path), None) if proof_manifest else None
                    valid_hashes = {matched_file.get("baseline_sha256"), matched_file.get("current", {}).get("sha256")} if matched_file else set()
                    if not proof_manifest or not matched_file or not proof_content or proof_content not in valid_hashes:
                        raise GuardError(f"rejected {severity} finding has no hash-linked repository proof")
            if classification == "UNCERTAIN" and severity in {"CRITICAL", "HIGH"}:
                state = "INCONCLUSIVE"
                reasons.append(f"{fid} is uncertain {severity}")
            elif classification == "CONFIRMED" and resolution == "OPEN" and severity in BLOCKING:
                if state != "INCONCLUSIVE":
                    state = "FAIL"
                reasons.append(f"{fid} remains confirmed and open")
            elif classification == "CONFIRMED" and resolution == "FIXED":
                fix_manifest = entry.get("fixed_in_manifest_sha256")
                review_id = entry.get("verified_by_review_id")
                verified_result_hash = entry.get("verified_by_result_sha256")
                regression = entry.get("regression_validation")
                if not all((fix_manifest, review_id, verified_result_hash)) or not isinstance(regression, dict):
                    raise GuardError(f"fixed finding {fid} has an incomplete proof chain")
                verification_review = reviews.get(review_id)
                if not verification_review:
                    raise GuardError(f"fixed finding {fid} references an unknown review")
                verify_result, verify_receipt = verification_review
                if verify_receipt["result_sha256"] != verified_result_hash or verify_receipt["manifest_sha256"] != fix_manifest:
                    raise GuardError(f"fixed finding {fid} verification hashes do not match")
                if verify_result["sequence"] <= reviews[origin["origin_review_id"]][0]["sequence"]:
                    raise GuardError(f"fixed finding {fid} was not re-reviewed later")
                verification = verifications.get((review_id, fid))
                if not verification:
                    raise GuardError(f"re-review omitted verification for {fid}")
                if verification["status"] == "STILL_PRESENT":
                    if state != "INCONCLUSIVE":
                        state = "FAIL"
                    reasons.append(f"{fid} is still present")
                elif verification["status"] == "UNRESOLVED":
                    if severity in {"CRITICAL", "HIGH"}:
                        state = "INCONCLUSIVE"
                    elif state != "INCONCLUSIVE":
                        state = "FAIL"
                    reasons.append(f"{fid} verification is unresolved")
                elif not verification["regression_test_verified"]:
                    raise GuardError(f"fixed finding {fid} lacks reviewer regression verification")
                if regression.get("validation_receipt_sha256") != validation_receipt["receipt_sha256"] or validation_receipt["status"] != "PASSED":
                    raise GuardError(f"fixed finding {fid} lacks passing deterministic validation")
                escalation = escalation_by_pair.get((origin["origin_manifest_sha256"], fix_manifest))
                if not escalation:
                    raise GuardError(f"fixed finding {fid} lacks escalation assessment")
                if escalation["full_review_required"] and verify_result["review_kind"] != "FULL":
                    state = "INCONCLUSIVE"
                    full_count = sum(x.get("review_kind") == "FULL" for x in ordered)
                    suffix = "; full-review budget exhausted" if full_count >= max_full_reviews else ""
                    reasons.append(f"{fid} required a full re-review{suffix}")
            output_lifecycle.append({"finding_id": fid, **immutable, "classification": classification, "resolution": resolution})

        if validation_receipt["status"] == "INCONCLUSIVE":
            state = "INCONCLUSIVE"
            reasons.append("deterministic validation is inconclusive")
        elif validation_receipt["status"] == "FAILED" and state != "INCONCLUSIVE":
            state = "FAIL"
            reasons.append("deterministic validation failed")
        return {"state": state, "reasons": reasons, "finding_lifecycle": output_lifecycle}
    except GuardError as exc:
        return {"state": "INCONCLUSIVE", "reasons": [str(exc)], "finding_lifecycle": []}


def _load(path: str) -> Any:
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


def _dump(value: Any) -> None:
    json.dump(value, sys.stdout, indent=2, sort_keys=True, ensure_ascii=True)
    sys.stdout.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture")
    capture.add_argument("--repo", default=".")
    capture.add_argument("--base")
    capture.add_argument("--include", action="append", default=[])
    capture.add_argument("--allow-empty", action="store_true")
    preflight = commands.add_parser("preflight-receipt")
    preflight.add_argument("--metadata", required=True)
    turn = commands.add_parser("turn-receipt")
    turn.add_argument("--metadata", required=True)
    turn.add_argument("--preflight", required=True)
    compare = commands.add_parser("compare")
    compare.add_argument("--before", required=True)
    compare.add_argument("--after", required=True)
    result = commands.add_parser("validate-result")
    for name in ("result", "manifest", "preflight", "turn", "snapshot"):
        result.add_argument(f"--{name}", required=True)
    validation = commands.add_parser("validation-receipt")
    validation.add_argument("--validation", required=True)
    final = commands.add_parser("final-manifest-receipt")
    final.add_argument("--manifest", required=True)
    final.add_argument("--snapshot", required=True)
    assess = commands.add_parser("assess-fix")
    assess.add_argument("--before", required=True)
    assess.add_argument("--after", required=True)
    assess.add_argument("--semantic", action="append", default=[])
    decide = commands.add_parser("decide")
    decide.add_argument("--result", action="append", required=True)
    decide.add_argument("--manifest", action="append", required=True)
    decide.add_argument("--result-receipt", action="append", required=True)
    decide.add_argument("--classifications", required=True)
    decide.add_argument("--validation-receipt", required=True)
    decide.add_argument("--final-manifest", required=True)
    decide.add_argument("--final-manifest-receipt", required=True)
    decide.add_argument("--escalation-receipt", action="append", default=[])
    decide.add_argument("--preflight-receipt", required=True)
    decide.add_argument("--turn-receipt", action="append", required=True)
    decide.add_argument("--snapshot-receipt", action="append", required=True)
    decide.add_argument("--max-full-reviews", type=int, default=2)
    decide.add_argument("--max-incremental-reviews", type=int, default=2)
    args = parser.parse_args()
    try:
        if args.command == "capture":
            value = build_manifest(args.repo, args.base, args.include, args.allow_empty)
        elif args.command == "preflight-receipt":
            value = make_preflight_receipt(_load(args.metadata))
        elif args.command == "turn-receipt":
            value = make_turn_receipt(_load(args.metadata), _load(args.preflight))
        elif args.command == "compare":
            value = make_snapshot_receipt(_load(args.before), _load(args.after))
        elif args.command == "validate-result":
            value = make_result_receipt(_load(args.result), _load(args.manifest), _load(args.preflight), _load(args.turn), _load(args.snapshot))
        elif args.command == "validation-receipt":
            value = make_validation_receipt(_load(args.validation))
        elif args.command == "final-manifest-receipt":
            value = make_final_manifest_receipt(_load(args.manifest), _load(args.snapshot))
        elif args.command == "assess-fix":
            value = make_escalation_receipt(_load(args.before), _load(args.after), args.semantic)
        else:
            value = decide_from_artifacts(
                [_load(p) for p in args.result], [_load(p) for p in args.manifest], [_load(p) for p in args.result_receipt],
                _load(args.classifications), _load(args.validation_receipt), _load(args.final_manifest),
                _load(args.final_manifest_receipt), [_load(p) for p in args.escalation_receipt],
                _load(args.preflight_receipt), [_load(p) for p in args.turn_receipt], [_load(p) for p in args.snapshot_receipt],
                args.max_full_reviews, args.max_incremental_reviews,
            )
        _dump(value)
        return 0 if value.get("state", "PASS") == "PASS" or args.command != "decide" else (2 if value["state"] == "FAIL" else 3)
    except (GuardError, OSError, json.JSONDecodeError) as exc:
        _dump({"state": "INCONCLUSIVE", "error": str(exc)})
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
