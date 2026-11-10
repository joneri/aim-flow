# Publish the prepared Beta

Target: public `joneri/aim-flow`, MIT. The intended Pages site is
`https://joneri.github.io/aim-flow/`. Beta.9 is prepared locally, not verified
as published. Do not replace an existing repository or assume remote main is
unchanged from a cached local observation.

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

## Publication currently blocked

A previous `git fetch origin` was explicitly blocked by execution-network policy
at `https://github.com:443`. No remote refresh, push or draft PR has been retried
for beta.9. This is not evidence of an authentication error, missing repository,
merge conflict or failed hosted CI. Do not use another endpoint, proxy, connector
or machine to get around the denial.

A supported permission change must allow the intended GitHub operation before
retrying it. No verified permission UI or pending approval form is available in
this handoff; a chat response alone is not proof the network policy changed.
GitHub CLI would additionally need authorized `api.github.com:443` access, which
has not been probed here. Existing account/repository rights remain separate.

## Steps after access is permitted

1. Recheck the local release branch, clean status, archive and independent pins.
2. Refresh remote main once through the newly permitted route. Review any new
   remote changes before integration; do not force-push or overwrite other work.
3. With current authorization for the destination and content, publish the
   reviewed isolated branch and open a **draft PR**. A PR run cannot deploy Pages.
4. Verify remote commit/tree and the exact commit's portable CI results. The
   workflow requires Git 2.54+; runner availability is not yet verified. It runs
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
