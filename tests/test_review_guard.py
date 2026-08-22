from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUARD_PATH = ROOT / "skills/adversarial-review/scripts/review_guard.py"
SPEC = importlib.util.spec_from_file_location("review_guard", GUARD_PATH)
assert SPEC and SPEC.loader
review_guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review_guard)


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


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

    def test_manifest_includes_staged_unstaged_and_untracked(self) -> None:
        (self.repo / "tracked.txt").write_text("staged\n", encoding="utf-8")
        git(self.repo, "add", "tracked.txt")
        (self.repo / "tracked.txt").write_text("unstaged\n", encoding="utf-8")
        (self.repo / "new.py").write_text("print('full file')\n", encoding="utf-8")

        manifest = review_guard.build_manifest(str(self.repo), None, [])

        layers = {
            (change["path"], change["layer"], change["change_type"])
            for change in manifest["changes"]
        }
        self.assertIn(("tracked.txt", "staged", "MODIFIED"), layers)
        self.assertIn(("tracked.txt", "unstaged", "MODIFIED"), layers)
        self.assertIn(("new.py", "untracked", "UNTRACKED"), layers)
        self.assertEqual(manifest["untracked_files"][0]["path"], "new.py")
        self.assertTrue(manifest["scope_complete"])

    def test_snapshot_hash_changes_when_worktree_changes(self) -> None:
        before = review_guard.build_manifest(str(self.repo), None, ["tracked.txt"])
        (self.repo / "tracked.txt").write_text("after\n", encoding="utf-8")
        after = review_guard.build_manifest(str(self.repo), None, ["tracked.txt"])
        self.assertNotEqual(before["manifest_sha256"], after["manifest_sha256"])

    def test_branch_manifest_includes_added_deleted_and_renamed(self) -> None:
        git(self.repo, "branch", "base-snapshot")
        git(self.repo, "mv", "tracked.txt", "renamed.txt")
        (self.repo / "deleted.txt").unlink()
        (self.repo / "added.txt").write_text("new\n", encoding="utf-8")
        git(self.repo, "add", "deleted.txt", "added.txt")
        git(self.repo, "commit", "-qm", "branch changes")

        manifest = review_guard.build_manifest(str(self.repo), "base-snapshot", [])

        changes = {
            (change["path"], change["layer"], change["change_type"])
            for change in manifest["changes"]
        }
        self.assertIn(("renamed.txt", "branch", "RENAMED"), changes)
        self.assertIn(("deleted.txt", "branch", "DELETED"), changes)
        self.assertIn(("added.txt", "branch", "ADDED"), changes)
        deleted = next(
            entry for entry in manifest["scope_files"] if entry["path"] == "deleted.txt"
        )
        self.assertIsNone(deleted["current"]["sha256"])
        self.assertIsNotNone(deleted["baseline_sha256"])

    def review_result(self, manifest: dict, confidence: int | float = 99) -> dict:
        return {
            "protocol_version": "1",
            "reviewer_name": "adversarial_reviewer",
            "review_id": "review-1",
            "review_kind": "FULL",
            "status": "COMPLETED",
            "scope_manifest_sha256": manifest["manifest_sha256"],
            "reviewed_paths": [entry["path"] for entry in manifest["scope_files"]],
            "summary": "One concrete defect found.",
            "findings": [
                {
                    "id": "F1",
                    "severity": "HIGH",
                    "confidence": confidence,
                    "location": {"path": "tracked.txt", "line": 1, "symbol": "sample"},
                    "violated_contract": "Sample contract.",
                    "trigger": "Sample trigger.",
                    "execution_path": "Sample path.",
                    "actual": "Wrong result.",
                    "expected": "Correct result.",
                    "regression_test": "Sample regression test.",
                }
            ],
        }

    def test_valid_review_result_covers_every_scope_path(self) -> None:
        (self.repo / "new.py").write_text("pass\n", encoding="utf-8")
        manifest = review_guard.build_manifest(str(self.repo), None, [])
        result = self.review_result(manifest)
        validated = review_guard.validate_review_result(result, manifest, "review-1", "FULL")
        self.assertEqual(validated, result)

    def test_fractional_confidence_is_accepted_without_rewriting_result(self) -> None:
        (self.repo / "new.py").write_text("pass\n", encoding="utf-8")
        manifest = review_guard.build_manifest(str(self.repo), None, [])
        result = self.review_result(manifest, confidence=0.99)

        validated = review_guard.validate_review_result(result, manifest, "review-1", "FULL")

        self.assertEqual(validated["findings"][0]["confidence"], 0.99)
        self.assertIsInstance(validated["findings"][0]["confidence"], float)

    def test_out_of_range_fractional_confidence_is_rejected(self) -> None:
        (self.repo / "new.py").write_text("pass\n", encoding="utf-8")
        manifest = review_guard.build_manifest(str(self.repo), None, [])
        result = self.review_result(manifest, confidence=1.01)

        with self.assertRaises(review_guard.GuardError):
            review_guard.validate_review_result(result, manifest, "review-1", "FULL")

    def test_empty_or_wrong_reviewer_result_is_invalid(self) -> None:
        manifest = review_guard.build_manifest(str(self.repo), None, ["tracked.txt"])
        with self.assertRaises(review_guard.GuardError):
            review_guard.validate_review_result({}, manifest, "review-1", "FULL")

    def test_strict_preflight_stops_before_expensive_review_without_runtime_attestation(self) -> None:
        result = review_guard.runtime_preflight("STRICT", False, False)
        self.assertFalse(result["proceed"])
        self.assertEqual(result["state"], "INCONCLUSIVE")
        self.assertIn("runtime:custom-agent-identity-unverifiable", result["reasons"])
        self.assertIn("runtime:effective-sandbox-unverifiable", result["reasons"])

    def test_best_effort_preflight_can_continue_degraded(self) -> None:
        result = review_guard.runtime_preflight("BEST_EFFORT", False, False)
        self.assertTrue(result["proceed"])
        self.assertTrue(result["degraded"])
        self.assertEqual(result["state"], "UNVERIFIED")

    def test_uncertain_high_forces_inconclusive(self) -> None:
        payload = self.valid_decision_payload()
        payload["findings"] = [
            {"id": "F1", "severity": "HIGH", "classification": "UNCERTAIN"}
        ]
        self.assertEqual(review_guard.decide_state(payload)["state"], "INCONCLUSIVE")

    def test_rejected_high_requires_objective_evidence(self) -> None:
        payload = self.valid_decision_payload()
        payload["findings"] = [
            {"id": "F1", "severity": "HIGH", "classification": "REJECTED"}
        ]
        self.assertEqual(review_guard.decide_state(payload)["state"], "INCONCLUSIVE")
        payload["findings"][0]["rejection_evidence"] = {
            "type": "documented_contract",
            "reference": "docs/contracts.md:12",
            "details": "The contract explicitly permits this state.",
        }
        self.assertEqual(review_guard.decide_state(payload)["state"], "PASS")

    def test_confirmed_medium_forces_fail(self) -> None:
        payload = self.valid_decision_payload()
        payload["findings"] = [
            {"id": "F1", "severity": "MEDIUM", "classification": "CONFIRMED"}
        ]
        self.assertEqual(review_guard.decide_state(payload)["state"], "FAIL")

    def test_execution_failure_forces_inconclusive(self) -> None:
        payload = self.valid_decision_payload()
        payload["integrity"]["reviewer_completed"] = False
        self.assertEqual(review_guard.decide_state(payload)["state"], "INCONCLUSIVE")

    def test_non_read_only_reviewer_forces_inconclusive_in_strict_mode(self) -> None:
        payload = self.valid_decision_payload()
        payload["integrity"]["effective_sandbox_read_only"] = False
        self.assertEqual(review_guard.decide_state(payload)["state"], "INCONCLUSIVE")

    def test_best_effort_runtime_attestation_gap_returns_unverified(self) -> None:
        payload = self.valid_decision_payload(mode="BEST_EFFORT")
        payload["integrity"]["correct_reviewer"] = False
        payload["integrity"]["effective_sandbox_read_only"] = False

        decision = review_guard.decide_state(payload)

        self.assertEqual(decision["state"], "UNVERIFIED")
        self.assertEqual(decision["inconclusive_reasons"], [])
        self.assertIn("integrity:correct_reviewer", decision["unverified_reasons"])
        self.assertIn("integrity:effective_sandbox_read_only", decision["unverified_reasons"])

    def test_best_effort_does_not_hide_non_attestation_integrity_failure(self) -> None:
        payload = self.valid_decision_payload(mode="BEST_EFFORT")
        payload["integrity"]["correct_reviewer"] = False
        payload["integrity"]["result_valid"] = False
        self.assertEqual(review_guard.decide_state(payload)["state"], "INCONCLUSIVE")

    def test_best_effort_with_full_attestation_can_still_pass(self) -> None:
        payload = self.valid_decision_payload(mode="BEST_EFFORT")
        self.assertEqual(review_guard.decide_state(payload)["state"], "PASS")

    def test_unreviewed_final_version_forces_inconclusive(self) -> None:
        payload = self.valid_decision_payload()
        payload["integrity"]["final_version_reviewed"] = False
        self.assertEqual(review_guard.decide_state(payload)["state"], "INCONCLUSIVE")

    def test_uncertain_medium_does_not_block_pass(self) -> None:
        payload = self.valid_decision_payload()
        payload["findings"] = [
            {"id": "F1", "severity": "MEDIUM", "classification": "UNCERTAIN"}
        ]
        self.assertEqual(review_guard.decide_state(payload)["state"], "PASS")

    def test_new_file_requires_full_review(self) -> None:
        before = review_guard.build_manifest(str(self.repo), None, ["tracked.txt"])
        (self.repo / "new.py").write_text("pass\n", encoding="utf-8")
        after = review_guard.build_manifest(str(self.repo), None, ["tracked.txt"])
        assessment = review_guard.assess_fix(before, after, [])
        self.assertTrue(assessment["full_review_required"])
        self.assertTrue(any(reason.startswith("new-files:") for reason in assessment["reasons"]))

    def test_semantic_fix_requires_full_review(self) -> None:
        manifest = review_guard.build_manifest(str(self.repo), None, ["tracked.txt"])
        assessment = review_guard.assess_fix(manifest, manifest, ["public-api"])
        self.assertEqual(
            assessment,
            {"full_review_required": True, "reasons": ["semantic:public-api"]},
        )

    @staticmethod
    def valid_decision_payload(mode: str = "STRICT") -> dict:
        return {
            "mode": mode,
            "integrity": {
                "reviewer_spawned": True,
                "correct_reviewer": True,
                "effective_sandbox_read_only": True,
                "reviewer_completed": True,
                "result_valid": True,
                "scope_complete": True,
                "worktree_unchanged": True,
                "final_version_reviewed": True,
                "followups_delivered": True,
            },
            "validation": "PASSED",
            "escalation": {"required": False, "full_review_completed": False},
            "findings": [],
        }


if __name__ == "__main__":
    unittest.main()
