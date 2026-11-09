# AIM Flow 0.1.0-beta.3

Preview a local task before choosing a build setup.

## Changes

- Added a strict proposal-only local task profile. A no-key preview binds the selected committed source, Git identity and task without requiring invented build/runtime/reviewer fields.
- Bound the proposal-only scope into the request, intent, grant and retained state. Existing shared-budget and exact-consent requirements remain in place.
- Explicitly refused candidate preparation, technical validation, execution and conversion of a retained proposal to an execution profile.
- Ported only the applicable controller, test and documentation changes from upstream commit `af7e45f744c6dd58f57cb714a4ed424f7a9077b1`; retained the product-only archive boundary.
- Updated the quickstart, website download and release metadata. No customer source, private state or qualification journals are included.

## Verification

Local validation on 9 November 2026:

- Python 3.14.8 / Apple Git 2.54.0: **277 tests run, 276 passed, 1 skipped**, 275.785 seconds.
- The seven new profile tests passed, including a real installed no-key preview and refusal cases. The existing separate-candidate validation case remains skipped because it needs two verified fixture packages.
- Node: **12 tests passed**, no failures or skips.
- Independent review: controller change matches the reviewed upstream patch; candidate/refusal, consent, scope, budget and resume boundaries remain enforced.
- Source inventory: 69 files verified. All 50 controller payload files in the download match the reviewed current source exactly.
- Branded download: trusted-source bootstrap match, eight payload hashes, fresh installation and expected unconfigured readiness exit 3 passed.
- Static website: 18 local links/assets checked. Recursive current-file and archive scan: 184 members, no excluded legacy-demo matches.
- Controller archive SHA-256: `dcaae17ef81363d3564beb08933e59fcf8e15d84c64297856c86ec6b71769181`.
- Controller manifest SHA-256: `d7104a7d39e186f763a739c2df3995d0aa44ce075b0518393af672845afbf270`.
- Download ZIP SHA-256: `83e6a7e587d3e57b95cbc799738853012f058c3cb4881815253f3d746371ff43`.

No live model, VM or project-script execution was performed for this release. Proposal preview is not live board integration, build readiness or product acceptance. The public workflow runs portable contracts and explicitly reports a skip for profile tests if Git 2.54+ is unavailable; the real-Git profile results above are local.

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
