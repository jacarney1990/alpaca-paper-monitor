"""Offline integration tests for the isolated public snapshot publisher.

Tests use only temporary local Git repositories, not GitHub, APIs, or trading.
"""
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "snapshot_branch.py"
SPEC = importlib.util.spec_from_file_location("snapshot_branch", SCRIPT)
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)


def git(*args, cwd=None):
    return subprocess.run(["git", *map(str, args)], cwd=cwd,
                          check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE).stdout.decode().strip()


def snapshot(when="2026-10-31T00:00:00+00:00"):
    return {
        "schema": 1,
        "updated_at": when,
        "sec": {"status": "ok", "filings": {}, "covered": 0},
        "fred": {"status": "ok", "series": {"UNRATE": []}},
        "read_only": True,
        "model_inference_calls": 0,
        "brokerage_orders": 0,
    }


class PublicSnapshotBranchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.remote = base / "origin.git"
        self.checkout = base / "repo"
        git("init", "--bare", self.remote)
        git("clone", self.remote, self.checkout)
        git("checkout", "-b", "main", cwd=self.checkout)
        git("config", "user.name", "Offline Test", cwd=self.checkout)
        git("config", "user.email", "offline@example.invalid", cwd=self.checkout)
        self.file = self.checkout / publisher.SNAPSHOT
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.write_snapshot(snapshot())
        git("add", "data/official_sources_snapshot.json", cwd=self.checkout)
        git("commit", "-m", "initial public snapshot", cwd=self.checkout)
        git("push", "-u", "origin", "main", cwd=self.checkout)
        git("branch", publisher.DATA_BRANCH, cwd=self.checkout)
        git("push", "origin", publisher.DATA_BRANCH, cwd=self.checkout)

    def write_snapshot(self, data):
        self.file.write_text(json.dumps(data) + "\n", encoding="utf-8")

    def head(self, branch):
        return git("--git-dir", self.remote, "rev-parse",
                   f"refs/heads/{branch}")

    def published(self):
        raw = git("--git-dir", self.remote, "show",
                  f"refs/heads/{publisher.DATA_BRANCH}:{publisher.SNAPSHOT.as_posix()}")
        return json.loads(raw)

    def test_publish_updates_data_branch_not_main_and_is_idempotent(self):
        original_main = self.head("main")
        modified = snapshot("2026-11-01T00:00:00+00:00")
        self.write_snapshot(modified)
        self.assertTrue(publisher.publish(self.checkout))
        first_publish = self.head(publisher.DATA_BRANCH)
        self.assertNotEqual(first_publish, original_main)
        self.assertEqual(self.head("main"), original_main)
        self.assertEqual(self.published(), modified)
        self.assertFalse(publisher.publish(self.checkout))
        self.assertEqual(self.head(publisher.DATA_BRANCH), first_publish)
        self.assertEqual(self.head("main"), original_main)

    def test_prepare_preserves_latest_published_data_not_stale_main(self):
        newer = snapshot("2026-11-01T00:00:00+00:00")
        self.write_snapshot(newer)
        publisher.publish(self.checkout)
        self.write_snapshot(snapshot())  # stale checkout of main
        publisher.prepare(self.checkout)
        self.assertEqual(json.loads(self.file.read_text()), newer)
        self.assertEqual(self.head("main"),
                         git("rev-parse", "HEAD", cwd=self.checkout))

    def test_rejects_unsafe_and_malformed_snapshots_without_pushing(self):
        original = self.head(publisher.DATA_BRANCH)
        for bad in ({"read_only": False}, {"brokerage_orders": 1},
                    {"model_inference_calls": 1}, {"schema": 2},
                    {"private_account_number": "never_publish"}):
            with self.subTest(bad=bad):
                data = snapshot()
                data.update(bad)
                self.write_snapshot(data)
                with self.assertRaises(ValueError):
                    publisher.publish(self.checkout)
                self.assertEqual(self.head(publisher.DATA_BRANCH), original)
        self.file.write_text("{corrupted", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            publisher.publish(self.checkout)
        self.assertEqual(self.head(publisher.DATA_BRANCH), original)

    def test_prepare_fails_closed_if_public_data_branch_missing(self):
        git("push", "origin", ":" + publisher.DATA_BRANCH, cwd=self.checkout)
        with self.assertRaises(subprocess.CalledProcessError):
            publisher.prepare(self.checkout)


if __name__ == "__main__":
    unittest.main()
