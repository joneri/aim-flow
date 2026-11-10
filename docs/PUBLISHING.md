# Publish the prepared Beta

Target: public `joneri/aim-flow`, MIT. The intended Pages site is
`https://joneri.github.io/aim-flow/`. Local package verification is recorded
below; confirm the exact remote commit, hosted checks and live download before
claiming a new version is published. Do not replace an existing repository or
assume remote main is unchanged from a cached local observation.

## Current reviewed download

- Version: `0.1.0-beta.9`.
- ZIP: `site/downloads/aim-flow-0.1.0-beta.9.zip`.
- ZIP SHA-256: `fe22b1a178498470d1d2fba9be6f9d4931634b968295f52efa7be8ef494ed734`.
- Controller archive SHA-256: `6f7305874c69603135ac34b66f621b09f4eca0bf2366235ec10dc1b863f1ad7f`.
- Installed manifest SHA-256: `3daca37e331e30429a27786dd98d73b74237c2b8316581ea574bba7f89a96d95`.
- Bootstrap SHA-256: `c74237ca0e65bcb495f60c82d9b69ad492ae2e95fba2c7f310b792f04604aa41`.

The previous beta.8 ZIP remains unchanged. The bootstrap is bound to this
release's exact 61-file inventory; matching schema numbers do not make older
bootstraps interchangeable. End users need independently trusted expected pins.

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
   both Node suites and reports platform-specific Python skips. Linux CI does
   not run the 13 macOS-only sandbox execution fixtures.
5. Confirm authorization for the main-branch update and Pages publication.
   Configure Pages to use GitHub Actions only when authorized. The existing
   workflow publishes `site/` after checks on main push or manual main dispatch.
6. Verify the actual HTTPS page, download, version, checksum, guides and notices
   before calling beta.9 public. Optionally create an authorized pre-release
   tagged `v0.1.0-beta.9` with this ZIP and checksum file.

Publication does not authorize AIWebSite changes, production deployment, budget
reset, new credentials or inherited project permissions. Keep incomplete and
unknown effects, previous controllers and their receipts intact.
