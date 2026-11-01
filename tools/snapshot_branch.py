"""Publish official SEC/FRED public snapshots on a data-only Git branch.

Never commits to main, places orders, calls AI models, or changes deployments.
Usage (from the repository root):
  python tools/snapshot_branch.py prepare
  python tools/snapshot_branch.py publish
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

DATA_BRANCH = "data/official-snapshots"
SNAPSHOT = Path("data/official_sources_snapshot.json")
REMOTE_HEAD = f"refs/heads/{DATA_BRANCH}"
TRACKING_REF = f"refs/remotes/origin/{DATA_BRANCH}"


def _git(repo: Path, *args: str, capture: bool = False, check: bool = True):
    return subprocess.run(
        ["git", *args], cwd=repo, check=check,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def _fetch(repo: Path) -> None:
    _git(repo, "fetch", "--no-tags", "origin", f"{REMOTE_HEAD}:{TRACKING_REF}")


def _validate(raw: bytes) -> dict:
    """Fail closed before copying a snapshot into the public data branch."""
    data = json.loads(raw)
    if (
        not isinstance(data, dict)
        or data.get("schema") != 1
        or data.get("read_only") is not True
        or data.get("brokerage_orders") != 0
        or data.get("model_inference_calls") != 0
        or not isinstance(data.get("sec"), dict)
        or not isinstance(data.get("fred"), dict)
        or not isinstance(data.get("updated_at"), str)
    ):
        raise ValueError("Invalid or unsafe official public snapshot")
    return data


def prepare(repo: Path) -> None:
    """Load last published data locally so outages preserve recent observations."""
    repo = repo.resolve()
    _fetch(repo)
    raw = _git(repo, "show", f"{TRACKING_REF}:{SNAPSHOT.as_posix()}", capture=True).stdout
    _validate(raw)
    destination = repo / SNAPSHOT
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
        temporary.write(raw)
        name = temporary.name
    try:
        os.replace(name, destination)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def publish(repo: Path) -> bool:
    """Commit only the validated snapshot to the isolated data branch."""
    repo = repo.resolve()
    raw = (repo / SNAPSHOT).read_bytes()
    _validate(raw)
    _fetch(repo)
    with tempfile.TemporaryDirectory(prefix="official-snapshot-") as temp:
        worktree = Path(temp) / "publish"
        _git(repo, "worktree", "add", "--detach", str(worktree), TRACKING_REF)
        try:
            destination = worktree / SNAPSHOT
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(repo / SNAPSHOT, destination)
            _git(worktree, "add", "--", SNAPSHOT.as_posix())
            diff = _git(worktree, "diff", "--cached", "--quiet", check=False)
            if diff.returncode == 0:
                return False
            if diff.returncode != 1:
                raise subprocess.CalledProcessError(diff.returncode, "git diff --cached")
            _git(worktree, "-c", "user.name=github-actions[bot]",
                 "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                 "commit", "-m", "Refresh official SEC and FRED snapshot [skip ci]")
            _git(worktree, "push", "origin", f"HEAD:{REMOTE_HEAD}")
            return True
        finally:
            _git(repo, "worktree", "remove", "--force", str(worktree))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "publish"))
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(Path.cwd())
        print("Loaded last public snapshot from isolated data branch")
    else:
        updated = publish(Path.cwd())
        print("Published public snapshot to data branch" if updated
              else "Public snapshot unchanged; no commit")


if __name__ == "__main__":
    main()
