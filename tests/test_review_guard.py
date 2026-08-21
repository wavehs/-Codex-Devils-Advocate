from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUARD_PATH = ROOT / ".agents/skills/adversarial-review/scripts/review_guard.py"
SPEC = importlib.util.spec_from_file_location("review_guard", GUARD_PATH)
assert SPEC and SPEC.loader
review_guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review_guard)


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, check=check, capture_output=True, text=True)


class ReviewGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name)
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.com")
        (self.repo / "tracked.txt").write_text("before\n", encoding="utf-8")
        (self.repo / "deleted.txt").write_text("remove me\n", encoding="utf-8")
        git(self.repo, "add", "tracked.txt", "deleted.txt")
        git(self.repo, "commit", "-qm", "initial")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def preflight(self) -> dict:
        return review_guard.make_preflight_receipt({
            "probe_id": "probe-1", "configured_agent_name": "adversarial_reviewer",
            "selected_agent_name": "adversarial_reviewer", "config_status": "LOADED",
            "config_path": ".codex/agents/adversarial-reviewer.toml", "model": "gpt-5.6-terra",
            "model_status": "AVAILABLE", "routing_status": "SELECTED", "spawn_status": "COMPLETED",
            "metadata_status": "AVAILABLE", "followup_status": "SUPPORTED",
            "thread_id": "thread-1", "spawn_id": "spawn-1",
        })

    def turn(self, preflight: dict, review_id: str, kind: str, sequence: int, parent: str | None) -> dict:
        return review_guard.make_turn_receipt({
            "turn_id": f"turn-{sequence}", "review_id": review_id, "review_kind": kind,
            "sequence": sequence, "parent_review_id": parent,
            "agent_name": "adversarial_reviewer", "model": "gpt-5.6-terra", "thread_id": "thread-1",
            "turn_mode": "SPAWN" if sequence == 1 else "FOLLOWUP",
            "delivery_status": "DELIVERED", "completion_status": "COMPLETED", "result_status": "RECEIVED",
        }, preflight)

    @staticmethod
    def finding(severity: str = "HIGH") -> dict:
        return {
            "id": "F1", "severity": severity, "confidence": "HIGH",
            "location": {"path": "tracked.txt", "line": 1},
            "violated_contract": "balance must not be negative", "trigger": "negative debit",
            "execution_path": "debit -> store", "actual": "negative balance", "expected": "rejection",
            "regression_test": "reject negative debit",
        }

    def lifecycle(self, verification_status: str = "FIXED", include_verification: bool = True, second_kind: str = "INCREMENTAL") -> dict:
        (self.repo / "tracked.txt").write_text("bug\n", encoding="utf-8")
        m1 = review_guard.build_manifest(str(self.repo), None, ["tracked.txt"])
        preflight = self.preflight()
        t1 = self.turn(preflight, "R1", "FULL", 1, None)
        r1 = {
            "protocol_version": "2", "reviewer_name": "adversarial_reviewer", "review_id": "R1",
            "review_kind": "FULL", "sequence": 1, "parent_review_id": None, "status": "COMPLETED",
            "scope_manifest_sha256": m1["manifest_sha256"], "reviewed_paths": ["tracked.txt"],
            "summary": "Found a blocking defect.", "findings": [self.finding()], "finding_verifications": [],
        }
        s1 = review_guard.make_snapshot_receipt(m1, m1)
        rr1 = review_guard.make_result_receipt(r1, m1, preflight, t1, s1)

        (self.repo / "tracked.txt").write_text("fixed\n", encoding="utf-8")
        m2 = review_guard.build_manifest(str(self.repo), None, ["tracked.txt"])
        t2 = self.turn(preflight, "R2", second_kind, 2, "R1")
        verifications = []
        if include_verification:
            verifications = [{
                "finding_id": "F1", "origin_result_sha256": review_guard.sha(r1),
                "status": verification_status, "evidence": "The guarded path now rejects the trigger.",
                "regression_test_verified": True,
            }]
        r2 = {
            "protocol_version": "2", "reviewer_name": "adversarial_reviewer", "review_id": "R2",
            "review_kind": second_kind, "sequence": 2, "parent_review_id": "R1", "status": "COMPLETED",
            "scope_manifest_sha256": m2["manifest_sha256"], "reviewed_paths": ["tracked.txt"],
            "summary": "Rechecked the fix.", "findings": [], "finding_verifications": verifications,
        }
        s2 = review_guard.make_snapshot_receipt(m2, m2)
        rr2 = review_guard.make_result_receipt(r2, m2, preflight, t2, s2)
        validation = review_guard.make_validation_receipt({"commands": [{
            "command": "pytest", "exit_code": 0, "output_sha256": "a" * 64,
            "scope": "regression", "outcome": "PASSED",
        }]})
        escalation = review_guard.make_escalation_receipt(m1, m2, [])
        classifications = {"protocol_version": "2", "findings": [{
            "finding_id": "F1", "origin_review_id": "R1", "origin_result_sha256": review_guard.sha(r1),
            "origin_manifest_sha256": m1["manifest_sha256"], "original_severity": "HIGH",
            "finding_content_sha256": review_guard.sha(r1["findings"][0]),
            "classification": "CONFIRMED", "resolution": "FIXED",
            "classification_evidence": {"type": "executable_reproduction", "reference": "test_negative", "details": "Reproduced before the fix."},
            "fixed_in_manifest_sha256": m2["manifest_sha256"], "verified_by_review_id": "R2",
            "verified_by_result_sha256": review_guard.sha(r2),
            "regression_validation": {"validation_receipt_sha256": validation["receipt_sha256"]},
        }]}
        final_receipt = review_guard.make_final_manifest_receipt(m2, s2)
        return {
            "results": [r1, r2], "manifests": [m1, m2], "result_receipts": [rr1, rr2],
            "classifications": classifications, "validation": validation, "final_manifest": m2,
            "final_receipt": final_receipt, "escalations": [escalation], "preflight": preflight,
            "turns": [t1, t2], "snapshots": [s1, s2],
        }

    @staticmethod
    def decide(data: dict, **kwargs: object) -> dict:
        return review_guard.decide_from_artifacts(
            data["results"], data["manifests"], data["result_receipts"], data["classifications"],
            data["validation"], data["final_manifest"], data["final_receipt"], data["escalations"], **kwargs,
            preflight_receipt=data["preflight"], turn_receipts=data["turns"], snapshot_receipts=data["snapshots"],
        )

    def test_manifest_includes_staged_unstaged_and_untracked(self) -> None:
        (self.repo / "tracked.txt").write_text("staged\n", encoding="utf-8")
        git(self.repo, "add", "tracked.txt")
        (self.repo / "tracked.txt").write_text("unstaged\n", encoding="utf-8")
        (self.repo / "new.py").write_text("print('full file')\n", encoding="utf-8")
        manifest = review_guard.build_manifest(str(self.repo), None, [])
        layers = {(c["path"], c["layer"], c["change_type"]) for c in manifest["changes"]}
        self.assertIn(("tracked.txt", "staged", "MODIFIED"), layers)
        self.assertIn(("tracked.txt", "unstaged", "MODIFIED"), layers)
        self.assertIn(("new.py", "untracked", "UNTRACKED"), layers)
        self.assertEqual(manifest["untracked_files"][0]["text_utf8"], "print('full file')\n")
        self.assertTrue(manifest["untracked_files"][0]["content_base64"])

    def test_empty_scope_is_rejected_by_default(self) -> None:
        with self.assertRaisesRegex(review_guard.GuardError, "scope contains no files"):
            review_guard.build_manifest(str(self.repo), None, [])

    def test_intentional_empty_scope_requires_explicit_flag(self) -> None:
        manifest = review_guard.build_manifest(str(self.repo), None, [], allow_empty=True)
        self.assertTrue(manifest["scope_empty"])
        with self.assertRaises(review_guard.GuardError):
            review_guard.validate_manifest(manifest)

    def test_unmerged_repository_is_rejected(self) -> None:
        git(self.repo, "checkout", "-qb", "other")
        (self.repo / "tracked.txt").write_text("other\n", encoding="utf-8")
        git(self.repo, "commit", "-qam", "other")
        git(self.repo, "checkout", "-q", "master")
        (self.repo / "tracked.txt").write_text("master\n", encoding="utf-8")
        git(self.repo, "commit", "-qam", "master")
        git(self.repo, "merge", "other", check=False)
        with self.assertRaisesRegex(review_guard.GuardError, "unresolved merge conflicts"):
            review_guard.build_manifest(str(self.repo), None, ["tracked.txt"])

    def test_branch_manifest_includes_add_delete_and_rename(self) -> None:
        git(self.repo, "branch", "base-snapshot")
        git(self.repo, "mv", "tracked.txt", "renamed.txt")
        (self.repo / "deleted.txt").unlink()
        (self.repo / "added.txt").write_text("new\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "changes")
        manifest = review_guard.build_manifest(str(self.repo), "base-snapshot", [])
        changes = {(c["path"], c["change_type"]) for c in manifest["changes"]}
        self.assertIn(("renamed.txt", "RENAMED"), changes)
        self.assertIn(("deleted.txt", "DELETED"), changes)
        self.assertIn(("added.txt", "ADDED"), changes)

    def test_wrong_reviewer_identity_fails_preflight(self) -> None:
        metadata = {
            "probe_id": "p", "configured_agent_name": "adversarial_reviewer", "selected_agent_name": "generic",
            "config_status": "LOADED", "config_path": "adversarial-reviewer.toml", "model": "gpt-5.6-terra",
            "model_status": "AVAILABLE", "routing_status": "SELECTED", "spawn_status": "COMPLETED",
            "metadata_status": "AVAILABLE", "followup_status": "SUPPORTED", "thread_id": "t", "spawn_id": "s",
        }
        with self.assertRaisesRegex(review_guard.GuardError, "selected_agent_name"):
            review_guard.make_preflight_receipt(metadata)

    def test_confirmed_fixed_and_verified_passes(self) -> None:
        decision = self.decide(self.lifecycle())
        self.assertEqual(decision["state"], "PASS", decision)
        self.assertEqual(decision["finding_lifecycle"][0]["resolution"], "FIXED")

    def test_still_present_fails(self) -> None:
        decision = self.decide(self.lifecycle("STILL_PRESENT"))
        self.assertEqual(decision["state"], "FAIL", decision)

    def test_missing_fix_verification_is_inconclusive(self) -> None:
        decision = self.decide(self.lifecycle(include_verification=False))
        self.assertEqual(decision["state"], "INCONCLUSIVE")
        self.assertIn("omitted verification", decision["reasons"][0])

    def test_omitted_finding_is_inconclusive(self) -> None:
        data = self.lifecycle()
        data["classifications"]["findings"] = []
        self.assertEqual(self.decide(data)["state"], "INCONCLUSIVE")

    def test_changed_original_severity_is_inconclusive(self) -> None:
        data = self.lifecycle()
        data["classifications"]["findings"][0]["original_severity"] = "LOW"
        self.assertEqual(self.decide(data)["state"], "INCONCLUSIVE")

    def test_final_manifest_must_equal_verified_review_manifest(self) -> None:
        data = self.lifecycle()
        (self.repo / "tracked.txt").write_text("changed after review\n", encoding="utf-8")
        m3 = review_guard.build_manifest(str(self.repo), None, ["tracked.txt"])
        data["final_manifest"] = m3
        s3 = review_guard.make_snapshot_receipt(m3, m3)
        data["final_receipt"] = review_guard.make_final_manifest_receipt(m3, s3)
        data["snapshots"].append(s3)
        data["manifests"].append(m3)
        self.assertEqual(self.decide(data)["state"], "INCONCLUSIVE")

    def test_reviewer_worktree_mutation_invalidates_result(self) -> None:
        (self.repo / "tracked.txt").write_text("review target\n", encoding="utf-8")
        before = review_guard.build_manifest(str(self.repo), None, ["tracked.txt"])
        (self.repo / "outside.txt").write_text("reviewer mutation\n", encoding="utf-8")
        after = review_guard.build_manifest(str(self.repo), None, ["tracked.txt"])
        snapshot = review_guard.make_snapshot_receipt(before, after)
        preflight = self.preflight()
        turn = self.turn(preflight, "R1", "FULL", 1, None)
        result = {
            "protocol_version": "2", "reviewer_name": "adversarial_reviewer", "review_id": "R1",
            "review_kind": "FULL", "sequence": 1, "parent_review_id": None, "status": "COMPLETED",
            "scope_manifest_sha256": before["manifest_sha256"],
            "reviewed_paths": [item["path"] for item in before["scope_files"]],
            "summary": "No finding.", "findings": [], "finding_verifications": [],
        }
        with self.assertRaisesRegex(review_guard.GuardError, "worktree changed"):
            review_guard.make_result_receipt(result, before, preflight, turn, snapshot)

    def test_confirmed_open_high_fails(self) -> None:
        data = self.lifecycle()
        item = data["classifications"]["findings"][0]
        item.update({"classification": "CONFIRMED", "resolution": "OPEN"})
        for key in ("fixed_in_manifest_sha256", "verified_by_review_id", "verified_by_result_sha256", "regression_validation"):
            item.pop(key, None)
        self.assertEqual(self.decide(data)["state"], "FAIL")

    def test_incremental_fix_with_new_file_requires_full_review(self) -> None:
        (self.repo / "tracked.txt").write_text("bug\n", encoding="utf-8")
        before = review_guard.build_manifest(str(self.repo), None, ["tracked.txt"])
        (self.repo / "new.py").write_text("pass\n", encoding="utf-8")
        after = review_guard.build_manifest(str(self.repo), None, ["tracked.txt"])
        assessment = review_guard.assess_fix(before, after, [])
        self.assertTrue(assessment["full_review_required"])
        self.assertTrue(any(x.startswith("new-files:") for x in assessment["reasons"]))

    def test_automatic_public_api_change_requires_full_review(self) -> None:
        (self.repo / "api.ts").write_text("export function debit(x: number) { return x }\n", encoding="utf-8")
        before = review_guard.build_manifest(str(self.repo), None, ["api.ts"])
        (self.repo / "api.ts").write_text("export function debit(x: string) { return x }\n", encoding="utf-8")
        after = review_guard.build_manifest(str(self.repo), None, ["api.ts"])
        assessment = review_guard.assess_fix(before, after, [])
        self.assertTrue(any(x.startswith("auto-semantic:public-api:") for x in assessment["reasons"]))

    def test_required_full_review_budget_exhausted_is_inconclusive(self) -> None:
        data = self.lifecycle()
        data["escalations"][0] = review_guard.seal("fix-escalation", {
            "before_manifest_sha256": data["manifests"][0]["manifest_sha256"],
            "after_manifest_sha256": data["manifests"][1]["manifest_sha256"],
            "full_review_required": True, "reasons": ["semantic:architecture"],
        })
        decision = self.decide(data, max_full_reviews=1)
        self.assertEqual(decision["state"], "INCONCLUSIVE")
        self.assertTrue(any("budget exhausted" in x for x in decision["reasons"]))

    def test_rejected_high_with_fake_evidence_is_inconclusive(self) -> None:
        data = self.lifecycle()
        item = data["classifications"]["findings"][0]
        item.update({"classification": "REJECTED", "resolution": "NOT_APPLICABLE"})
        item["classification_evidence"] = {"type": "seems_intentional", "reference": "opinion", "details": "Looks intended."}
        for key in ("fixed_in_manifest_sha256", "verified_by_review_id", "verified_by_result_sha256", "regression_validation"):
            item.pop(key, None)
        self.assertEqual(self.decide(data)["state"], "INCONCLUSIVE")

    def test_rejected_high_with_unlinked_repository_evidence_is_inconclusive(self) -> None:
        data = self.lifecycle()
        item = data["classifications"]["findings"][0]
        item.update({"classification": "REJECTED", "resolution": "NOT_APPLICABLE"})
        item["classification_evidence"] = {
            "type": "repository_evidence", "reference": "tracked.txt:1", "details": "Claimed contract.",
            "proof": {"manifest_sha256": "0" * 64, "path": "tracked.txt", "content_sha256": "1" * 64},
        }
        for key in ("fixed_in_manifest_sha256", "verified_by_review_id", "verified_by_result_sha256", "regression_validation"):
            item.pop(key, None)
        self.assertEqual(self.decide(data)["state"], "INCONCLUSIVE")

    def test_rejected_high_with_hash_linked_repository_evidence_passes(self) -> None:
        data = self.lifecycle()
        item = data["classifications"]["findings"][0]
        origin_manifest = data["manifests"][0]
        origin_file = next(x for x in origin_manifest["scope_files"] if x["path"] == "tracked.txt")
        item.update({"classification": "REJECTED", "resolution": "NOT_APPLICABLE"})
        item["classification_evidence"] = {
            "type": "documented_contract", "reference": "tracked.txt:1", "details": "The reviewed contract permits it.",
            "proof": {"manifest_sha256": origin_manifest["manifest_sha256"], "path": "tracked.txt", "content_sha256": origin_file["current"]["sha256"]},
        }
        for key in ("fixed_in_manifest_sha256", "verified_by_review_id", "verified_by_result_sha256", "regression_validation"):
            item.pop(key, None)
        self.assertEqual(self.decide(data)["state"], "PASS")

    def test_uncertain_high_is_inconclusive(self) -> None:
        data = self.lifecycle()
        item = data["classifications"]["findings"][0]
        item.update({"classification": "UNCERTAIN", "resolution": "OPEN"})
        for key in ("fixed_in_manifest_sha256", "verified_by_review_id", "verified_by_result_sha256", "regression_validation"):
            item.pop(key, None)
        self.assertEqual(self.decide(data)["state"], "INCONCLUSIVE")


if __name__ == "__main__":
    unittest.main()
