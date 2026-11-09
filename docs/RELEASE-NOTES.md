# AIM Flow 0.1.0-beta.2

Product-only Beta packaging with the approved official AIM A identity.

## Changes

- Removed unrelated legacy application integrations, manifests, profile, API probe script and application-specific verifier fixtures.
- Moved generic model support into a normal controller module.
- Introduced one exact product-only controller archive layout (schema 2). Older mixed archive layouts are intentionally not accepted by this distribution.
- Retained project discovery/installation, bounded source-check planning/execution, separately approved local intake and neutral catalog offline test coverage.
- Kept MIT code licensing separate from original AIM artwork attribution and font notices.
- Rebuilt the downloadable Beta and replaced previous-version links in the website and documentation.

## Verification

Local validation on 9 November 2026:

- Python 3.14.8 / Apple Git 2.54: **270 tests run, 269 passed, 1 skipped**, 212.609 seconds.
- Skipped: the separate-candidate validation case requires two verified fixture packages.
- Node: **12 tests passed**, no failures or skips.
- Independent dependency review: all 50 packaged inputs present, all packaged modules import, no removed-helper or stale-schema references.
- Source inventory: 68 files verified (45 unchanged upstream files, 21 reviewed modifications, 2 added files).
- Branded download: trusted-source bootstrap match, eight payload hashes, fresh installation and expected unconfigured readiness exit 3 passed.
- Static website: 18 local links/assets checked.
- Controller archive SHA-256: `1608173c6e9e57414868ad07dd7b9ee6907241ca2092811781765b7eb8a69d04`.
- Controller manifest SHA-256: `c00b9fdcdc5b5c100e0ec44e624cc9379790526c3a8994e140d750943b7c1453`.
- Download ZIP SHA-256: `40169aa3648383fadcdfd4619e1853a18d63eed19368a9821ed8927c29cf3add`.

 Earlier runtime proof is described separately in [VERIFICATION.md](VERIFICATION.md); this cleanup does not claim a new VM/model/runtime qualification.

The GitHub workflow runs portable offline contracts, verifies the downloadable bundle and builds another bundle from source before it deploys the static site. A source-check pass is not an application build, deployment or product-owner acceptance.
