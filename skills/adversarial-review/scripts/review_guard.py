#!/usr/bin/env python3
"""Deterministic integrity guard for the adversarial-review skill."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any


PROTOCOL_VERSION = "1"
BLOCKING_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM"}
SEVERITIES = BLOCKING_SEVERITIES | {"LOW"}
CLASSIFICATIONS = {"CONFIRMED", "REJECTED", "UNCERTAIN"}
REVIEW_MODES = {"STRICT", "BEST_EFFORT"}
RUNTIME_ATTESTATION_KEYS = {"correct_reviewer", "effective_sandbox_read_only"}
CORE_INTEGRITY_KEYS = {
    "reviewer_spawned",
    "reviewer_completed",
    "result_valid",
    "scope_complete",
    "worktree_unchanged",
    "final_version_reviewed",
    "followups_delivered",
}
REJECTION_EVIDENCE_TYPES = {
    "executable_reproduction",
    "passing_regression_test",
    "acceptance_criterion",
    "documented_contract",
    "type_invariant",
    "validation_logic",
    "repository_evidence",
}
SEMANTIC_ESCALATIONS = {
    "public-api",
    "schema",
    "persistence",
    "concurrency",
    "core-invariant",
    "architecture",
    "core-algorithm",
}


class GuardError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def run_git(repo: Path, *args: str, check: bool = True) -> bytes:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    process = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and process.returncode != 0:
        message = process.stderr.decode("utf-8", "replace").strip()
        raise GuardError(f"git {' '.join(args)} failed: {message}")
    return process.stdout


def git_text(repo: Path, *args: str) -> str:
    return run_git(repo, *args).decode("utf-8", "strict").strip()


def normalize_repo(repo_arg: str) -> Path:
    requested = Path(repo_arg).resolve()
    root = Path(git_text(requested, "rev-parse", "--show-toplevel")).resolve()
    return root


def normalize_path(raw_path: str) -> str:
    candidate = PurePosixPath(raw_path.replace("\\", "/"))
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        raise GuardError(f"unsafe repository path: {raw_path!r}")
    return candidate.as_posix()


def parse_name_status(data: bytes, layer: str) -> list[dict[str, Any]]:
    tokens = data.decode("utf-8", "surrogateescape").split("\0")
    if tokens and tokens[-1] == "":
        tokens.pop()
    result: list[dict[str, Any]] = []
    index = 0
    names = {
        "A": "ADDED",
        "M": "MODIFIED",
        "D": "DELETED",
        "R": "RENAMED",
        "C": "COPIED",
        "T": "TYPE_CHANGED",
        "U": "UNMERGED",
    }
    while index < len(tokens):
        status_code = tokens[index]
        index += 1
        if not status_code:
            raise GuardError(f"malformed {layer} name-status output")
        kind = status_code[0]
        if kind not in names or index >= len(tokens):
            raise GuardError(f"unsupported {layer} status: {status_code!r}")
        if kind in {"R", "C"}:
            if index + 1 >= len(tokens):
                raise GuardError(f"malformed {layer} rename/copy record")
            old_path = normalize_path(tokens[index])
            path = normalize_path(tokens[index + 1])
            index += 2
            result.append(
                {
                    "layer": layer,
                    "change_type": names[kind],
                    "status_code": status_code,
                    "old_path": old_path,
                    "path": path,
                }
            )
        else:
            path = normalize_path(tokens[index])
            index += 1
            result.append(
                {
                    "layer": layer,
                    "change_type": names[kind],
                    "status_code": status_code,
                    "path": path,
                }
            )
    return result


def untracked_changes(repo: Path) -> list[dict[str, Any]]:
    data = run_git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    paths = data.decode("utf-8", "surrogateescape").split("\0")
    return [
        {
            "layer": "untracked",
            "change_type": "UNTRACKED",
            "status_code": "??",
            "path": normalize_path(path),
        }
        for path in paths
        if path
    ]


def path_fingerprint(repo: Path, relative_path: str) -> dict[str, Any]:
    path = repo / relative_path
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return {"kind": "missing", "size": None, "sha256": None}
    except OSError as exc:
        raise GuardError(f"cannot stat {relative_path}: {exc}") from exc

    if stat.S_ISLNK(mode):
        try:
            target = os.readlink(path)
        except OSError as exc:
            raise GuardError(f"cannot read symlink {relative_path}: {exc}") from exc
        payload = b"symlink\0" + os.fsencode(target)
        return {"kind": "symlink", "size": len(payload), "sha256": sha256(payload)}
    if stat.S_ISREG(mode):
        digest = hashlib.sha256()
        size = 0
        try:
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
        except OSError as exc:
            raise GuardError(f"cannot read {relative_path}: {exc}") from exc
        return {"kind": "file", "size": size, "sha256": digest.hexdigest()}
    if stat.S_ISDIR(mode):
        submodule_head = run_git(path, "rev-parse", "HEAD", check=False).decode(
            "utf-8", "replace"
        ).strip()
        submodule_status = run_git(path, "status", "--porcelain=v2", "-z", check=False)
        payload = b"directory\0" + submodule_head.encode() + b"\0" + submodule_status
        return {"kind": "directory", "size": None, "sha256": sha256(payload)}
    raise GuardError(f"unsupported filesystem object in scope: {relative_path}")


def baseline_fingerprint(repo: Path, reference: str, path: str) -> str | None:
    process = subprocess.run(
        ["git", "-c", "core.quotepath=false", "show", f"{reference}:{path}"],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode != 0:
        return None
    return sha256(process.stdout)


def patch_stats(parts: list[tuple[str, bytes]]) -> dict[str, Any]:
    additions = 0
    deletions = 0
    binary_parts = 0
    total_bytes = 0
    digest = hashlib.sha256()
    for label, data in parts:
        label_bytes = label.encode("utf-8")
        digest.update(len(label_bytes).to_bytes(4, "big"))
        digest.update(label_bytes)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
        total_bytes += len(data)
        if b"GIT binary patch" in data or b"Binary files " in data:
            binary_parts += 1
        for line in data.splitlines():
            if line.startswith(b"+") and not line.startswith(b"+++"):
                additions += 1
            elif line.startswith(b"-") and not line.startswith(b"---"):
                deletions += 1
    return {
        "sha256": digest.hexdigest(),
        "bytes": total_bytes,
        "additions": additions,
        "deletions": deletions,
        "changed_lines": additions + deletions,
        "binary_parts": binary_parts,
    }


def collect_diff_data(
    repo: Path, merge_base_sha: str | None
) -> tuple[list[dict[str, Any]], list[tuple[str, bytes]]]:
    changes: list[dict[str, Any]] = []
    parts: list[tuple[str, bytes]] = []
    if merge_base_sha:
        changes.extend(
            parse_name_status(
                run_git(repo, "diff", "--name-status", "-z", "-M", f"{merge_base_sha}..HEAD"),
                "branch",
            )
        )
        parts.append(
            (
                "branch",
                run_git(
                    repo,
                    "diff",
                    "--binary",
                    "--full-index",
                    "--no-ext-diff",
                    f"{merge_base_sha}..HEAD",
                ),
            )
        )
    changes.extend(
        parse_name_status(
            run_git(repo, "diff", "--cached", "--name-status", "-z", "-M", "HEAD"),
            "staged",
        )
    )
    changes.extend(
        parse_name_status(
            run_git(repo, "diff", "--name-status", "-z", "-M"), "unstaged"
        )
    )
    changes.extend(untracked_changes(repo))
    parts.extend(
        [
            (
                "staged",
                run_git(
                    repo,
                    "diff",
                    "--cached",
                    "--binary",
                    "--full-index",
                    "--no-ext-diff",
                    "HEAD",
                ),
            ),
            (
                "unstaged",
                run_git(
                    repo,
                    "diff",
                    "--binary",
                    "--full-index",
                    "--no-ext-diff",
                ),
            ),
        ]
    )
    return changes, parts


def build_manifest(repo_arg: str, base: str | None, includes: list[str]) -> dict[str, Any]:
    repo = normalize_repo(repo_arg)
    head_sha = git_text(repo, "rev-parse", "HEAD")
    merge_base_sha = git_text(repo, "merge-base", "HEAD", base) if base else None
    status_raw = run_git(repo, "status", "--porcelain=v2", "-z", "--untracked-files=all")

    changes, patch_parts = collect_diff_data(repo, merge_base_sha)

    for include in includes:
        path = normalize_path(include)
        fingerprint = path_fingerprint(repo, path)
        if fingerprint["kind"] == "missing":
            raise GuardError(f"explicit scope path does not exist: {path}")
        changes.append(
            {
                "layer": "explicit",
                "change_type": "INSPECTED",
                "status_code": "I",
                "path": path,
            }
        )

    changes.sort(key=lambda item: (item["path"], item["layer"], item["status_code"]))
    aggregated: dict[str, dict[str, Any]] = {}
    baseline_ref = merge_base_sha or head_sha
    for change in changes:
        path = change["path"]
        entry = aggregated.setdefault(
            path,
            {
                "path": path,
                "layers": [],
                "change_types": [],
                "previous_paths": [],
                "requires_full_content": False,
            },
        )
        entry["layers"].append(change["layer"])
        entry["change_types"].append(change["change_type"])
        if old_path := change.get("old_path"):
            entry["previous_paths"].append(old_path)
        if change["change_type"] == "UNTRACKED":
            entry["requires_full_content"] = True

    scope_files: list[dict[str, Any]] = []
    for path in sorted(aggregated):
        entry = aggregated[path]
        entry["layers"] = sorted(set(entry["layers"]))
        entry["change_types"] = sorted(set(entry["change_types"]))
        entry["previous_paths"] = sorted(set(entry["previous_paths"]))
        entry["current"] = path_fingerprint(repo, path)
        baseline_path = entry["previous_paths"][0] if entry["previous_paths"] else path
        entry["baseline_sha256"] = baseline_fingerprint(repo, baseline_ref, baseline_path)
        scope_files.append(entry)

    diff = patch_stats(patch_parts)
    manifest: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "repository_root": str(repo),
        "head_sha": head_sha,
        "base_ref": base,
        "merge_base_sha": merge_base_sha,
        "status_porcelain_v2_sha256": sha256(status_raw),
        "scope_complete": True,
        "scope_empty": not scope_files,
        "changes": changes,
        "scope_files": scope_files,
        "untracked_files": [
            {
                "path": entry["path"],
                "size": entry["current"]["size"],
                "sha256": entry["current"]["sha256"],
            }
            for entry in scope_files
            if "UNTRACKED" in entry["change_types"]
        ],
        "diff": diff,
    }

    if git_text(repo, "rev-parse", "HEAD") != head_sha:
        raise GuardError("HEAD changed while the manifest was being captured")
    status_after = run_git(repo, "status", "--porcelain=v2", "-z", "--untracked-files=all")
    if sha256(status_after) != sha256(status_raw):
        raise GuardError("Git status changed while the manifest was being captured")
    _, patch_parts_after = collect_diff_data(repo, merge_base_sha)
    if patch_stats(patch_parts_after) != diff:
        raise GuardError("diff changed while the manifest was being captured")
    for entry in scope_files:
        if path_fingerprint(repo, entry["path"]) != entry["current"]:
            raise GuardError(
                f"scope file changed while the manifest was being captured: {entry['path']}"
            )

    manifest["manifest_sha256"] = sha256(canonical_bytes(manifest))
    return manifest


def load_json(path: str) -> Any:
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise GuardError(f"cannot load valid JSON from {path}: {exc}") from exc


def write_json(path: str | None, value: Any) -> None:
    rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)


def validate_manifest(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise GuardError("manifest must be a JSON object")
    supplied_hash = manifest.get("manifest_sha256")
    if not isinstance(supplied_hash, str):
        raise GuardError("manifest_sha256 is missing")
    unhashed = dict(manifest)
    unhashed.pop("manifest_sha256", None)
    if sha256(canonical_bytes(unhashed)) != supplied_hash:
        raise GuardError("manifest_sha256 does not match manifest content")
    if manifest.get("protocol_version") != PROTOCOL_VERSION:
        raise GuardError("unsupported manifest protocol_version")
    if manifest.get("scope_complete") is not True:
        raise GuardError("manifest scope is not complete")
    if not isinstance(manifest.get("scope_files"), list):
        raise GuardError("manifest scope_files must be an array")
    return manifest


def valid_confidence(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return 0 <= value <= 100
    if isinstance(value, float):
        return math.isfinite(value) and 0.0 <= value <= 1.0
    return False


def validate_review_result(
    result: Any, manifest: dict[str, Any], review_id: str, review_kind: str
) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise GuardError("review result must be a JSON object")
    expected = {
        "protocol_version": PROTOCOL_VERSION,
        "reviewer_name": "adversarial_reviewer",
        "review_id": review_id,
        "review_kind": review_kind,
        "status": "COMPLETED",
        "scope_manifest_sha256": manifest["manifest_sha256"],
    }
    for key, value in expected.items():
        if result.get(key) != value:
            raise GuardError(f"review result {key} must equal {value!r}")
    reviewed_paths = result.get("reviewed_paths")
    if not isinstance(reviewed_paths, list) or any(
        not isinstance(path, str) or not path for path in reviewed_paths
    ):
        raise GuardError("reviewed_paths must be a non-empty string array")
    required_paths = {entry["path"] for entry in manifest["scope_files"]}
    if not required_paths.issubset(set(reviewed_paths)):
        missing = sorted(required_paths - set(reviewed_paths))
        raise GuardError(f"reviewer did not attest to reviewing scope paths: {missing}")
    summary = result.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise GuardError("review summary must be non-empty")
    findings = result.get("findings")
    if not isinstance(findings, list):
        raise GuardError("findings must be an array")
    required_finding_fields = {
        "id",
        "severity",
        "confidence",
        "location",
        "violated_contract",
        "trigger",
        "execution_path",
        "actual",
        "expected",
        "regression_test",
    }
    seen_ids: set[str] = set()
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise GuardError(f"finding {index} must be an object")
        missing = required_finding_fields - finding.keys()
        if missing:
            raise GuardError(f"finding {index} is missing fields: {sorted(missing)}")
        if not isinstance(finding["id"], str) or not finding["id"].strip():
            raise GuardError(f"finding {index} has an invalid id")
        if finding["id"] in seen_ids:
            raise GuardError(f"duplicate finding id: {finding['id']}")
        seen_ids.add(finding["id"])
        if finding["severity"] not in SEVERITIES:
            raise GuardError(f"finding {finding['id']} has an invalid severity")
        if not valid_confidence(finding["confidence"]):
            raise GuardError(f"finding {finding['id']} has an invalid confidence")
        location = finding["location"]
        if not isinstance(location, dict) or not isinstance(location.get("path"), str):
            raise GuardError(f"finding {finding['id']} has an invalid location")
        for field in required_finding_fields - {"id", "severity", "confidence", "location"}:
            if not isinstance(finding[field], str) or not finding[field].strip():
                raise GuardError(f"finding {finding['id']} has an empty {field}")
    return result


def assess_fix(
    reviewed: dict[str, Any], current: dict[str, Any], semantic: list[str]
) -> dict[str, Any]:
    reasons: list[str] = []
    reviewed_paths = {entry["path"] for entry in reviewed["scope_files"]}
    current_paths = {entry["path"] for entry in current["scope_files"]}
    new_paths = sorted(current_paths - reviewed_paths)
    if new_paths:
        reasons.append("previously-unreviewed-paths:" + ",".join(new_paths))

    added_after_review = sorted(
        entry["path"]
        for entry in current["scope_files"]
        if entry["path"] not in reviewed_paths
        and ({"ADDED", "UNTRACKED"} & set(entry["change_types"]))
    )
    if added_after_review:
        reasons.append("new-files:" + ",".join(added_after_review))
    if current.get("head_sha") != reviewed.get("head_sha"):
        reasons.append("head-changed")

    before_lines = int(reviewed.get("diff", {}).get("changed_lines", 0))
    after_lines = int(current.get("diff", {}).get("changed_lines", 0))
    growth_limit = before_lines + max(200, math.ceil(before_lines * 0.5))
    if after_lines > growth_limit:
        reasons.append(f"significant-diff-growth:{before_lines}->{after_lines}")

    invalid_semantic = set(semantic) - SEMANTIC_ESCALATIONS
    if invalid_semantic:
        raise GuardError(f"unsupported semantic escalation: {sorted(invalid_semantic)}")
    reasons.extend(f"semantic:{item}" for item in sorted(set(semantic)))
    return {"full_review_required": bool(reasons), "reasons": reasons}


def runtime_preflight(
    mode: str, identity_verifiable: bool, sandbox_read_only_verifiable: bool
) -> dict[str, Any]:
    if mode not in REVIEW_MODES:
        raise GuardError(f"unsupported review mode: {mode!r}")
    reasons: list[str] = []
    if not identity_verifiable:
        reasons.append("runtime:custom-agent-identity-unverifiable")
    if not sandbox_read_only_verifiable:
        reasons.append("runtime:effective-sandbox-unverifiable")

    if mode == "STRICT" and reasons:
        return {
            "mode": mode,
            "proceed": False,
            "degraded": False,
            "state": "INCONCLUSIVE",
            "reasons": reasons,
        }
    return {
        "mode": mode,
        "proceed": True,
        "degraded": bool(reasons),
        "state": "UNVERIFIED" if reasons else None,
        "reasons": reasons,
    }


def decide_state(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise GuardError("decision input must be a JSON object")

    mode = payload.get("mode", "STRICT")
    if mode not in REVIEW_MODES:
        return {
            "mode": mode,
            "state": "INCONCLUSIVE",
            "inconclusive_reasons": ["mode:invalid"],
            "unverified_reasons": [],
            "confirmed_blocking": [],
        }

    integrity = payload.get("integrity")
    if not isinstance(integrity, dict):
        raise GuardError("decision input integrity must be an object")

    core_failures = [
        f"integrity:{key}"
        for key in sorted(CORE_INTEGRITY_KEYS)
        if integrity.get(key) is not True
    ]
    attestation_failures = [
        f"integrity:{key}"
        for key in sorted(RUNTIME_ATTESTATION_KEYS)
        if integrity.get(key) is not True
    ]
    inconclusive_reasons = list(core_failures)
    unverified_reasons: list[str] = []
    if mode == "STRICT":
        inconclusive_reasons.extend(attestation_failures)
    else:
        unverified_reasons.extend(attestation_failures)

    validation = payload.get("validation")
    if validation not in {"PASSED", "FAILED", "INCONCLUSIVE"}:
        inconclusive_reasons.append("validation:missing-or-invalid")
    elif validation == "INCONCLUSIVE":
        inconclusive_reasons.append("validation:inconclusive")

    escalation = payload.get("escalation", {})
    if not isinstance(escalation, dict):
        inconclusive_reasons.append("escalation:invalid")
    elif escalation.get("required") is True and escalation.get("full_review_completed") is not True:
        inconclusive_reasons.append("escalation:full-review-not-completed")

    findings = payload.get("findings")
    if not isinstance(findings, list):
        inconclusive_reasons.append("findings:missing-or-invalid")
        findings = []

    confirmed_blocking: list[str] = []
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            inconclusive_reasons.append(f"finding:{index}:invalid")
            continue
        finding_id = str(finding.get("id", index))
        severity = finding.get("severity")
        classification = finding.get("classification")
        if severity not in SEVERITIES or classification not in CLASSIFICATIONS:
            inconclusive_reasons.append(f"finding:{finding_id}:unclassified")
            continue
        if classification == "CONFIRMED" and severity in BLOCKING_SEVERITIES:
            confirmed_blocking.append(finding_id)
        if classification == "UNCERTAIN" and severity in {"CRITICAL", "HIGH"}:
            inconclusive_reasons.append(f"finding:{finding_id}:uncertain-{severity.lower()}")
        if classification == "REJECTED" and severity in {"CRITICAL", "HIGH"}:
            evidence = finding.get("rejection_evidence")
            if (
                not isinstance(evidence, dict)
                or evidence.get("type") not in REJECTION_EVIDENCE_TYPES
                or not isinstance(evidence.get("reference"), str)
                or not evidence["reference"].strip()
                or not isinstance(evidence.get("details"), str)
                or not evidence["details"].strip()
            ):
                inconclusive_reasons.append(
                    f"finding:{finding_id}:rejection-lacks-objective-evidence"
                )

    if inconclusive_reasons:
        state = "INCONCLUSIVE"
    elif mode == "BEST_EFFORT" and unverified_reasons:
        state = "UNVERIFIED"
    elif validation == "FAILED" or confirmed_blocking:
        state = "FAIL"
    else:
        state = "PASS"
    return {
        "mode": mode,
        "state": state,
        "inconclusive_reasons": sorted(set(inconclusive_reasons)),
        "unverified_reasons": sorted(set(unverified_reasons)),
        "confirmed_blocking": confirmed_blocking,
    }


def yes_no(value: str) -> bool:
    return value == "yes"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser(
        "preflight", help="decide whether the current Codex runtime can start the review"
    )
    preflight.add_argument("--mode", choices=sorted(REVIEW_MODES), default="STRICT")
    preflight.add_argument("--identity-verifiable", choices=["yes", "no"], required=True)
    preflight.add_argument(
        "--sandbox-read-only-verifiable", choices=["yes", "no"], required=True
    )

    capture = subparsers.add_parser("capture", help="capture a frozen change manifest")
    capture.add_argument("--repo", default=".")
    capture.add_argument("--base")
    capture.add_argument("--include", action="append", default=[])
    capture.add_argument("--output", required=True)

    compare = subparsers.add_parser("compare", help="compare two frozen manifests")
    compare.add_argument("--before", required=True)
    compare.add_argument("--after", required=True)

    validate_result_parser = subparsers.add_parser(
        "validate-result", help="validate the exact reviewer JSON envelope"
    )
    validate_result_parser.add_argument("--input", required=True)
    validate_result_parser.add_argument("--manifest", required=True)
    validate_result_parser.add_argument("--review-id", required=True)
    validate_result_parser.add_argument(
        "--review-kind", required=True, choices=["FULL", "INCREMENTAL"]
    )

    assess = subparsers.add_parser(
        "assess-fix", help="decide whether a fix requires a new full review"
    )
    assess.add_argument("--reviewed", required=True)
    assess.add_argument("--current", required=True)
    assess.add_argument("--semantic", action="append", default=[])

    decide = subparsers.add_parser(
        "decide", help="compute PASS, FAIL, INCONCLUSIVE, or UNVERIFIED"
    )
    decide.add_argument("--input", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "preflight":
            write_json(
                None,
                runtime_preflight(
                    args.mode,
                    yes_no(args.identity_verifiable),
                    yes_no(args.sandbox_read_only_verifiable),
                ),
            )
            return 0
        if args.command == "capture":
            write_json(args.output, build_manifest(args.repo, args.base, args.include))
            return 0
        if args.command == "compare":
            before = validate_manifest(load_json(args.before))
            after = validate_manifest(load_json(args.after))
            match = before["manifest_sha256"] == after["manifest_sha256"]
            write_json(
                None,
                {
                    "match": match,
                    "before": before["manifest_sha256"],
                    "after": after["manifest_sha256"],
                },
            )
            return 0 if match else 3
        if args.command == "validate-result":
            manifest = validate_manifest(load_json(args.manifest))
            result = validate_review_result(
                load_json(args.input), manifest, args.review_id, args.review_kind
            )
            write_json(
                None,
                {
                    "valid": True,
                    "result_sha256": sha256(canonical_bytes(result)),
                    "findings": len(result["findings"]),
                },
            )
            return 0
        if args.command == "assess-fix":
            reviewed = validate_manifest(load_json(args.reviewed))
            current = validate_manifest(load_json(args.current))
            write_json(None, assess_fix(reviewed, current, args.semantic))
            return 0
        if args.command == "decide":
            write_json(None, decide_state(load_json(args.input)))
            return 0
    except GuardError as exc:
        sys.stderr.write(f"review_guard: {exc}\n")
        return 2
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
