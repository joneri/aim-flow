# AIM Flow 0.1.0-beta.1

Initial public packaging of the existing bounded controller as AIM Flow Beta. Publication/hosting are separate from local qualification; consult the repository's actual release and Pages status rather than treating this file as deployment evidence.

## Included

- Exact 52-file controller extraction from `joneri/agenticpipeline` commit `783cb975f897b3179f3a8492a98b24fcc48dead6`.
- MIT license, compatibility/source inventory, current quickstart, Beta boundaries and release checklist.
- AIM Flow source-checkout convenience wrapper, separate from installed `aim-ci` command names.
- Approved official AIM A identity, static GitHub Pages site and matching launch material. Original AIM source attribution is retained separately from the MIT code license.
- Branded download wrapper with the unchanged controller archive, bootstrap, license and instructions.

## Local checks, 9 November 2026

- Python 3.14.8 / macOS / Apple Git 2.54.0: **265 tests run, 264 passed, 1 skipped**, 214.891 seconds.
- Skipped: separate candidate validation needs two verified fixture packages. This omission is not counted as runtime proof.
- Node 26.7.0: **12 tests passed**, 0 failed/skipped.
- Extraction: **69 preserved files verified**, including the 52 controller files and selected tests/context.
- The v0.1.0-beta.1 controller archive built in this extraction was byte-for-byte identical to the same version built in the upstream checkout.
- Controller archive SHA-256: `e32a0f3fbed1e26b8a233304069692fda73214672418e3242f7d5916a9708b33`.
- Controller manifest SHA-256: `1e9480aca8c42cbd7296fca8b2bc489b1f9ef65454e78d352da7f9fcf2ad36ae`.

- Static site: local assets/anchors/downloads checked; original design reviewed at desktop and 390/320-pixel mobile widths. No runtime JavaScript, external font requests, forms or analytics.
- Branded download smoke test: eight outer-file hashes and MIT notice checked; bootstrap verification, fresh installation and expected unconfigured readiness exit 3 passed.

The prepared GitHub workflow runs a smaller portable contract subset, not the full Mac validation, and then publishes the static site for an authorized main-branch push once Pages is configured. No VM, model, external provider, GitHub CI, deployment or hosted website verification was performed by these local packaging checks. Prior runtime evidence and its limits are summarized separately in [VERIFICATION.md](VERIFICATION.md).
