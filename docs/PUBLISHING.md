# Publish the prepared Beta

Target: public `joneri/aim-flow`, MIT. The intended Pages site is
`https://joneri.github.io/aim-flow/`. Local package verification is recorded
below; confirm the exact remote commit, hosted checks and live download before
claiming a new version is published. Do not replace an existing repository or
assume remote main is unchanged from a cached local observation.

## Active release: beta.12

Source VERSION and the active download are `0.1.0-beta.12`. This release adds
funding continuation to the initial workflow, onboarding and combined intake
without resetting historical accounting. The prior beta.11 and earlier ZIPs
remain unchanged. Active pins live in
`site/downloads/release.json` and `site/downloads/SHA256SUMS`.

The branded bundle check with `--current-source` must match every controller
file, wrapper notice and active version to the reviewed release checkout. An
internally consistent historical archive is not evidence of a current download.
Publication is verified only after exact-commit CI, Pages and actual HTTPS
version/hash checks succeed. No customer application or retained pilot controller
is modified by this release.

## Network and publication prerequisites

Check that the intended destination and operation are permitted before fetching
or publishing. If access is denied, stop and resolve that restriction through
an approved route. Do not switch endpoints, proxies, accounts or machines to
circumvent it. A successful one-off fetch does not grant broader persistent
access or authorize pushes, merges, repository settings or Pages publication.

During beta.9 preparation, an earlier GitHub fetch was blocked. A later scoped
approved fetch succeeded, and the reviewed release branch and draft PR were
published. That initial restriction is historical, not a current blanket claim
that GitHub is inaccessible. Record the outcome of each subsequent step.

## Publication sequence

1. Recheck the local release branch, clean status, archive and independent pins.
2. Refresh remote main once through the newly permitted route. Review any new
   remote changes before integration; do not force-push or overwrite other work.
3. With current authorization for the destination and content, publish the
   reviewed isolated branch and open a **draft PR**. A PR run cannot deploy Pages.
4. Verify remote commit/tree and the exact commit's portable CI results. The
   workflow requires Git 2.54+; the beta.9 PR runner passed that prerequisite. It runs
   the 17 selected contracts plus package/source/site checks; full suites require
   explicit manual opt-in. Linux CI does
   not run the 13 macOS-only sandbox execution fixtures.
5. Confirm authorization for the main-branch update and Pages publication.
   Configure Pages to use GitHub Actions only when authorized. The existing
   workflow publishes `site/` after checks on main push only.
6. Verify the actual HTTPS page, download, version, checksum, guides and notices
   before calling the release public. Optional tags/pre-releases are separate
   publication steps and are not created by this checklist.

Publication does not authorize AIWebSite changes, production deployment, budget
reset, new credentials or inherited project permissions. Keep incomplete and
unknown effects, previous controllers and their receipts intact.
