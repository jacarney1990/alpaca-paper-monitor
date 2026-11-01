# Protected-Main SEC/FRED Snapshot Publication — Staged Migration

**Status:** Proposal only. The currently deployed `main` workflow is not changed by this branch.

## Goal

Preserve free, unattended official-source refreshes while allowing `main` to become a protected code branch with required offline tests. No paid AI calls, trading, broker orders, or private account exports.

## Repository configuration

- Repository: `jacarney1990/alpaca-paper-monitor`.
- Code: `main`; Render's existing paper-monitor service auto-deploys `main`.
- Public data: `data/official-snapshots`, seeded from the last public snapshot on `main` (as of 2026-10-31). This branch does not control deployment.
- Current pre-migration process: `.github/workflows/official-data-refresh.yml` publishes directly to `main`. It is deliberately unchanged on the deployed branch.
- Proposed process (this PR): run offline tests, fetch the previous snapshot from the fixed data branch, read public SEC/FRED sources, validate the output, and commit **only** `data/official_sources_snapshot.json` to the fixed data branch.

The data branch contains a copy of the public repository at its seed commit; after initialization only the public snapshot file is updated. The publisher uses temporary Git worktrees. It never pushes to `main` and does not modify its HEAD.

## Activation order (separate approval required)

1. Merge and observe draft CI PR #11, if independently approved. Confirm `Offline safety CI / safety-tests` succeeds on relevant pull requests and new changes.
2. Once PR #11 is merged, retarget this stacked PR to `main`. Ensure the diff contains only the publication workflow, `tools/snapshot_branch.py`, its isolated offline tests, and this documentation.
3. Verify `data/official-snapshots` still exists with a readable, validated public snapshot.
4. Review/merge this publisher PR separately. Its first scheduled or manually authorized run should preserve the latest old data and push only a new data-branch snapshot. Check GitHub run logs and SHA of `main` afterward.
5. Enumerate and migrate any **active** consumers using the old `main/data/official_sources_snapshot.json` feed. The unfinished Multi-AI Studio branch currently references that path; it is out of scope and not modified here. Do not represent that excluded consumer as upgraded.
6. After data publication and consumer behavior have been tested, configure branch protection/rulesets requiring PR review and the exact `safety-tests` check for `main`; verify intentionally failing tests block merging.
7. Confirm the paper-monitor's live `/health` reports `order_submission: false` and Render deployments follow only reviewed commits.

## Evidence and safe verification

- `tests/test_snapshot_branch_publisher.py` uses an isolated local **bare Git remote**. It checks that publishing changes the data branch without changing `main`, unchanged snapshots do not create commits, last-published data is restored when a checkout is stale, and unsafe or unrecognized fields are rejected.
- `python -m unittest discover -s tests -v` runs without API or trading credentials.
- The normal scheduled workflow has **not** run with this new publisher. Offline simulations are not a substitute for verifying the first scheduled production run.
- `[skip ci]` commit text does not guarantee that Render or other integrations will skip deploying changes. The publisher avoids `main` entirely.

## Rollback

Before any future merge, record the latest `main` and Render deploy SHAs. If publication fails after activation, revert the workflow commit through a reviewed PR; data-branch commits remain isolated from production. Do not remove branch protection or allow direct production pushes as an emergency workaround without separately reviewing access implications.

## Approval boundary

This branch and draft PR are non-production proposals. No live brokerage orders, production deployment switches, secret updates, notification changes, or app publication are authorized by this document.
