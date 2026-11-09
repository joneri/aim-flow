# AIM CI Python test matrix

Run a focused module from the repository root with
`python3 -m unittest discover -s tests -p <filename>`. Substitute the exact
filename from the tables. Use Python 3.11 or newer and a POSIX filesystem.
Those are prerequisites. The focused local reference checks use macOS,
Python 3.14.8 and Apple Git 2.54.0.

## Normal validation

These targets are wired into `ci_validate`. The full validation command
also has product and network steps. Run individual targets when the task
only authorizes offline regression checks.

| File | Coverage and requirements | Evidence boundary |
| --- | --- | --- |
| `test_openai_budget.py` | Offline reservation, failure and API response handling. A guarded macOS case needs Seatbelt and the configured Homebrew Node path. | Synthetic provider outcomes. A skipped case supplies no sandbox proof. |
| `test_aim_ci.py` | Issue binding, review, patch, archive, budget and recovery with local fixtures or mocks. Includes a macOS Seatbelt case. | No live provider, CI, merge or release qualification. |
| `test_diagnostics.py` | Temporary journals and protected reads. Exercises read-only status and unavailable accounting. | No provider reconciliation or worker execution. |
| `test_human_review.py` | Cheap policy refusals. Guarded macOS cases use local Git, the synthetic catalog and actual Seatbelt validation. | The fixture reviewer supplies no real human or publication authority. |
| `test_source_free_release.py` | Exercises bootstrap, legacy readiness and incomplete project readiness with stdlib and an empty PATH. Candidate tests also use an existing Git executable and a disposable local commit to check exact source binding. | Candidate packaging uses basic Git commands. The installed smoke test needs Python 3.11+ and no checkout, Git, npm or project scripts. The local suite supplies no hosted-run qualification. |
| `test_project_planning.py`, `ProjectMetadataTests` | Version parsing, the stable exact/caret subset, approval-pin refusal before Git and local container profile validation against an external SHA-256. No Git installation required. | Installed planning against committed Git data is covered separately by `ProjectPlanningTests` with Git 2.54.0+. |
| `test_docker_engine.py` | Real private adapter admission, HTTP request construction with in-process responses, image binding, lifecycle reconciliation and archive allowlist compatibility. | Socket and subprocess execution are guarded. No daemon, image, container or actual isolation check. |
| `test_docker_recipe.py` | In-process observations of Docker preparation, control gaps and journal reconciliation. | Synthetic controls only. No Docker client or transport. |
| `test_recipe_execution.py` | Pre-dispatch gates and a test backend with temporary local evidence. | No native execution or backend resource enforcement. |
| `test_docker_image_evidence.py` | Bounded tar and actual synthetic-byte verification, plus never-started readback and reconciliation through in-process Docker responses. | Socket and subprocess execution are guarded. Synthetic protocol results leave image origin unverified. |
| `test_operator_build.py` | Covers installed operator admission, interruption and exact cleanup with guarded Docker responses. Root intake tests check consent, schema, input pins, state namespace and receipt recovery. The single-operation amendment cases exercise the maintained source worker and ledger, with the credential reader and OpenAI responses replaced by doubles. Normal CI uses real local fixture Git commands and doubles the advertised Git version. | Local Git 2.54.0 can be checked separately. Package tests use synthetic external responses. These tests supply no hosted Git 2.54, live model or backend qualification. Operator runs require a reviewed image and dependency store, which the [existing preparation workflow](aim-ci-onboarding.md#prepare-a-reviewed-npmvite-build) can assemble. |
| `test_build_preparation.py`, `PreparationContractTests` | Checks draft refusal, readiness process and socket guards, local tarball SRI, platform exclusions, protected source refusal and inert upload cleanup. Uses Python 3.11+ and local fixtures. | `reviewed-build-preparation-contract` covers Git-independent preparation contracts. It supplies no Docker or installed Git qualification. |
| `test_source_check.py` and `test_source_subset.py` | Validate one named no-install/no-output check, exact source manifests and bounded original Git membership proofs. | Process execution is guarded; no actual project command or omitted source blob is read. |
| `test_project_intake_grant.py` | Validate explicit project-bound grants, preserved shared budget history, private fake-key handling and installed-context intake recovery. | Provider and installation identity are doubled; no live call or real credential. |
| `test_npm_acquisition.py` | Checks registry URL and response policy, pinned public addresses, byte and time bounds, no-overwrite publication, integrity, and journal recovery with synthetic transport. | Part of `reviewed-build-preparation-contract`. It supplies no live registry or installation qualification. |

The `project-planning-metadata` label corresponds only to
`ProjectMetadataTests`. The `docker-backend-contract` label covers the three
in-process Docker and recipe modules above. They need no Docker daemon, VM,
Node, npm or network access. The separate `docker-image-byte-contract` label
covers the byte verifier and readback protocol fixtures.
The `npm-platform-build-contract` label runs `test_npm_platform.py` with
Python 3.11+ and `test_npm_platform_build.cjs` with the built-in Node.js test
runner (`node --test`, Node.js 24 in hosted validation). These tests use local
fixture bytes and a mock cacache adapter. The related Docker protocol tests
use in-process responses. They provide no real npm or Docker runtime
qualification.

```sh
python3 -m unittest discover -s tests -p test_project_planning.py -k ProjectMetadataTests
```

## Project-bound intake regression

Run `python3 -m unittest discover -s tests -p test_project_intake_grant.py`
with Python 3.11+ for the version 3 local intake and private-key contract.
It covers exact project/task/source/request bindings, unchanged monetary
ceilings, explicit one-slot extension after both synthetic Liam grants,
immutable prefix backups, global rollback/orphan refusal, credential
path binding, full installed-context API admission and receipt resume,
private fake-key file reads, and source-only review wording. Installation
identity checks in the API fixture are doubled; CLI/package installation
coverage remains in `test_project_installation.py` and
`test_operator_build.py`. All provider replies are synthetic. No real
credential, external project, live provider or VM is used.

## Focused local checks outside normal validation

| File or class | Use and requirements | Evidence boundary |
| --- | --- | --- |
| `test_project_planning.py`, `ProjectPlanningTests` | Real Git 2.54.0+ on PATH, temporary local Git repos and a fresh control installation. Runs installed discovery, adaptation and planning. Missing or older Git fails setup explicitly. | No project script or real approval. Runtime pins for Node and npm identify executable sentinels that must remain unused. |
| `test_build_preparation.py`, `InstalledPreparationTests` | Uses an actual controller installation, Git 2.54.0+ and a committed local fixture. Verifies source-bound drafting, acquisition preview, preparation preview, exact Git tree and tarball binding, a generated plan accepted by the installed build preview, and lost-reply reconciliation. | Draft and preview CLI tests use real Git without network or Docker dispatch. Staging and recovery tests mock Docker. |
| `test_project_installation.py` | Real Git 2.54.0+ on PATH, temporary repositories and installed controller bytes. Checks project receipts, explicit source-check choices, receipt consumption and installed intake CLI routing. | No project scripts or live effects. Intake API routing is doubled; full guarded API behavior is covered by `test_project_intake_grant.py`. |
| `test_control_package.py` | Temporary archive and installation, Git 2.54.0+ for checkout cases and POSIX shell fixtures. | No worker or remote target. Use the source-free suite for routine bootstrap coverage. |
| `test_hosted_product.py` | Bash, Ruby YAML and jq. Checks the registered hosted product, workflow argument vectors with tool sentinels, evidence metadata and local archive verification. | No hosted workflow, npm execution or real release. |
| `test_effects.py` | Local filesystem and locking, an isolated helper Python process and synthetic effects. | No real remote effect or provider reconciliation. |
| `test_installed_launch.py` | Patched launcher, runtime, worker and browser proof with actual local receipt/state writes. | No installed worker, browser or runtime qualification. |
| `test_installed_runtime.py` | Temporary retained state and changed-binding refusals. | No real launch, product validation or browser run. |
| `test_target_checkout.py` | Local Git repos and controlled worker fixtures for origin, controls and shared state. | No live remote identity or authorization. |

Run the installed Git fixture deliberately after checking `git --version`.
The hosted workflow supplies no pinned Git 2.54.0+ toolchain for this target.

```sh
python3 -m unittest discover -s tests -p test_project_planning.py -k ProjectPlanningTests
```

## Product, browser and fault integration

Run these separately on a reviewed local fixture setup with the prerequisites
already available. They exercise more than the metadata path.

| File | Prerequisites and scope | Evidence boundary |
| --- | --- | --- |
| `test_browser_probe.py` | Committed FakeATG and catalog fixtures, Git, tar, Node/npm, locked FakeATG modules, Playwright/Chromium and loopback port 4173. Builds artifacts and serves them locally. | No hosted deploy, product acceptance or general browser/network isolation. |
| `test_local_ci.py` | Committed product fixtures, Git, tar and macOS Seatbelt. FakeATG needs its locked modules, Node/npm and Playwright/Chromium. Runs actual validation and build. | Local artifacts and evidence. No hosted CI, hosted scan or release. |
| `test_offline_journeys.py` | macOS Seatbelt, local Git, shell/tar and synthetic catalog worker setup. The deployment probe needs Node at `/opt/homebrew/bin/node`, installed Playwright, its matching Chromium in the macOS Playwright cache and a free loopback port 4173. A supplied browser binding instead needs its reviewed Node, module and Chromium paths. Exercises staging, validation and restart with synthetic transports. | No real GitHub, model or deployment effect. |
| `test_release_checkout.py` | Committed FakeATG and catalog fixtures, Git, tar, Node/npm and preinstalled locked FakeATG modules. Runs builds with a GitHub sentinel. | No remote authorization, hosted promotion or production probe. |

For a real adopter run, a qualified backend and verified dependency store
remain necessary. Runtime limits, process and network isolation, loopback,
artifact validation, separate operator authority and any live or hosted
effects still need their own evidence. See the
[first-adopter model](aim-ci-first-adopter.md) and
[planning and execution boundary](aim-ci-plan-recipe.md).
