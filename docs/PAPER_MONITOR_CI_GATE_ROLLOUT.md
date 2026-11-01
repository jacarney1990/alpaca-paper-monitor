# Paper Monitor CI Gate Rollout Plan

> Staged documentation only. Do not merge or change production without a separate review.

**Goal:** Ensure offline safety tests pass before application code reaches the deployed `main` branch while preserving the existing official SEC/FRED snapshot refresh and paper-only operation.

## Verified baseline (November 1, 2026)

- Repository: `jacarney1990/alpaca-paper-monitor`, default branch `main`.
- `main` is not protected and has no repository rulesets.
- Render's `alpaca-paper-monitor` service deploys changes to `main` automatically.
- `.github/workflows/official-data-refresh.yml` has a scheduled `publish` job that commits `data/official_sources_snapshot.json` directly to `main`, using a `[skip ci]` commit message.
- Draft PR #11 adds `.github/workflows/offline-safety-ci.yml`, a credential-free `safety-tests` job for pull requests targeting `main`. The branch run passed 240 offline tests.
- The existing `investment-paper-pilot.yml` schedule remains fixed to `PAPER_PILOT_MODE: shadow` and `PAPER_PILOT_APPROVED: 'false'`.

## Why protection must not be switched on yet

A rule requiring reviews and successful status checks could reject the existing automated direct commit to `main`. The `[skip ci]` marker does not create a passing required check and does not stop Render's automatic deployment. Merging PR #11 alone adds tests but does not enforce them as a deployment gate.

## Migration choices (choose and test one before enforcing a ruleset)

**Option A — Preserve unattended data refresh (lower manual effort).**
Move official-source snapshot publication to a dedicated data branch or storage location rather than making unreviewed commits to protected `main`. Update and test every *active* consumer of that snapshot; verify source availability, freshness, and permissions. This requires a separately reviewed code change and is not implemented in PR #11.

**Option B — Review each snapshot update (stronger change control, more manual work).**
Change scheduled snapshot publication into an authenticated pull-request workflow, with reviewers and successful tests required before merge. Verify the automation's pull-request creation permissions and note that events created by the default `GITHUB_TOKEN` may not trigger follow-on workflows. Prefer an appropriately scoped GitHub App token over an unrestricted personal token where possible. This also requires a separately reviewed change.

**Option C — Ruleset bypass for one automation (least preferred).**
Investigate a narrowly scoped bot bypass for snapshot-only writes. Do not introduce broad bypass rights that permit changing application code without safety checks.

## Proposed enforcement after migration tests

1. Require pull requests to `main`; disable force pushes and deletion.
2. Require the exact successful GitHub Actions check name `safety-tests` from the new `Offline safety CI` workflow (verify the name GitHub displays on the most recent PR run).
3. Require one review from an authorized reviewer where feasible; document single-maintainer limitations.
4. Preserve offline testing and prohibit order-submission/paid-model credentials in test jobs.
5. Reconcile Render auto-deploy with the protected-branch promotion process so a failing check never deploys code.
6. Verify: successful PR merges, deliberately failing test blocks merge, SEC/FRED publication continues, read-only health endpoint works, paper-mode behavior remains disabled for order submission.

## Rollback

Before changing any production setting, record the existing main commit and Render deploy ID, capture a copy of the current ruleset configuration, and document a reversible path. Do not trigger a redeploy or switch branches as part of this planning document.

## Approval boundary

This file is an implementation checklist, not permission to modify branch protection, deployment settings, scheduled workflows, investment accounts, or secrets. Those changes require a separate explicit go-ahead.
