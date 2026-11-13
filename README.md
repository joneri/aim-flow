<p align="left"><img src="site/assets/aim-flow-beta-light.svg" alt="AIM Flow Beta" width="300"></p>

# AIM Flow · Beta

**Understand the project. Review the next step. Keep the evidence.**

AIM Flow helps you understand a software project, choose the next step and check what happened. It reads the project's committed files and workflow, proposes a plan for that project and keeps evidence for the operations you approve. The goal is to make AI-assisted development easier to review and repeat.

**Version:** `0.1.0-beta.10` (release candidate; [PR #2](https://github.com/joneri/aim-flow/pull/2)) · **Code license:** [MIT](LICENSE) · **Status:** early technical beta for trusted, reviewed projects.

[Website](https://joneri.github.io/aim-flow/) · [Start safely](docs/QUICKSTART.md) · [What the beta proves](docs/BETA-SCOPE.md) · [Roadmap](docs/ROADMAP.md) · [CI: 17-case default](docs/CI.md)

## What you can explore now

- **Project discovery:** inspect committed project metadata, scripts, CI and container definitions without running project code.
- **Project-aware installation:** retain a source-bound adaptation draft separately from the immutable controller.
- **Bounded source checks:** explicitly select one supported npm script and its committed inputs, then separately review preparation and execution. This mode installs no dependencies and produces no application build.
- **Proposal-only task intake:** preview a project-bound request before choosing a build setup. No model call or change is made by the preview. Select an output allowance for the exact request. Actual API use requires a matching grant, consent, provider preflight and existing budget history.
- **Project size:** read complete selected source and project definitions without fixed product file-size, file-count or request-byte caps. Provider, approved spending and reviewed runtime limits still apply. See [size handling](docs/aim-ci-size-handling.md).
- **Reviewed private candidates:** prepare an isolated local code candidate from an independently reviewed retained proposal, with new exact consent. This leaves the source checkout untouched and does not run or publish the project.
- **Private failure diagnostics:** retain selected metadata and visible output for new incomplete responses, separately from accepted proposals. This evidence stays private and cannot authorize a retry. See [handling and retention](docs/aim-ci-onboarding.md#concise-patches-and-private-incomplete-response-evidence).
- **Sol through OpenAI API:** the downloadable `aim-flow.py deliver-api` command previews exact Sol/xhigh patch requests, preserves campaign budget history and retains outcomes without replay. It returns a proposal for separate review and application. See [the API guide](docs/API-DELIVERY.md).
- **Inspectable receipts:** retain hashes, exact intent, results and recovery/cleanup evidence.

An earlier controller revision completed a real source-check qualification on 9 November 2026. Its check passed and its owned containers and derived image were removed. The current distribution is checked separately with offline regression and installation tests; later reviewed development also qualified one project-specific frontend/backend aggregate. Packaging does not repeat that execution or qualify other projects. See the [evidence summary](docs/VERIFICATION.md).

## First look

Use an explicitly selected **Python 3.11+** executable. On macOS, `/usr/bin/python3` may be too old.

```sh
python3 aim-flow.py --version
python3 aim-flow.py --help
```

Start with [archive verification and read-only installation checks](docs/QUICKSTART.md). Project inspection additionally requires **Git 2.54+** with an independently checked executable pin. Execution requires its selected adapter’s reviewed runtime and separate consent: the local sandbox adapter is macOS-specific; the container adapter uses an already-prepared Linux Docker runtime. This is not a one-command hosted service.

## What Beta means

AIM Flow does **not** yet offer universal build support, unattended delivery for arbitrary projects, multi-user governance, a qualified hostile-code sandbox or a general browser-testing path. A source check is not an application build, deployment or product-owner acceptance. See [current boundaries](docs/BETA-SCOPE.md) before planning a pilot.

## Project background

AIM Flow is the standalone public beta of AIM's delivery-controller work. This repository contains the product's code, documentation and website. You do not need a separate development repository or demo application to install it.

The [source inventory](provenance/upstream-files.json) records where the code came from and which parts have changed. Installation commands and requirements are explained in the [quickstart](docs/QUICKSTART.md).

[Artwork attribution and notices](THIRD-PARTY-NOTICES.md) · [Source inventory](provenance/upstream-files.json) · [Release process](docs/RELEASING.md) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md)

Included in beta.9: [project-specific agent onboarding](docs/PROJECT-ONBOARDING.md) exports committed evidence and retains source-cited installation, test, CI and deployment reasoning in a separate project instance. The optional `onboard-api` route can dispatch a separately approved Sol assessment and retain the resulting instance. A first live pilot returned useful but incomplete analysis. A later compact live assessment produced a reviewed, source-bound instance. Assessment alone does not execute its proposed plan or authorize project actions.

Included in beta.9: [project-local validation handoffs](docs/PROJECT-VALIDATION.md) consume a reviewed onboarding instance and explicit project declaration. They preserve actual npm roots, lifecycle hooks and CI distinctions without executing the project or upgrading the existing runners.

The project-local validation route now supports explicitly authorized, single-attempt offline npm jobs and source-bound execution receipts. See [project validation](docs/PROJECT-VALIDATION.md) for the preview, containment, runtime review and verification boundaries.

The Linux container route can run reviewed Node 24 npm jobs with
a private loopback namespace, offline cache and retained execution evidence.
AIWebSite's real `npm ci` and combined `ci:release-readiness` frontend/backend
chain passed through a fresh installed Flow controller. Schema v2 binds a
separately reviewed utility image and an explicitly executable private workspace.
This local aggregate is separate from hosted CI, native Windows, browser
acceptance and publication of this locally prepared package; see the
[container scope](docs/PROJECT-VALIDATION.md#linux-container-backend-jobs).

Included in beta.10: [the GitHub delivery adapter](docs/GITHUB-DELIVERY.md)
binds an explicitly authorized PR merge to exact merged-main push CI and artifact
metadata. It preserves unknown outcomes and resumes observation read-only. Its
offline contracts are not live delivery qualification or a full Flow pilot.

Included in beta.10: [exact candidate publication](docs/GITHUB-PUBLICATION.md)
verifies a retained Flow-produced candidate, uses a create-only branch guard,
and creates a new draft PR from explicitly declared text. It feeds the separate
merge/main-push handoff without editing an existing PR. Offline integration is
separate from live publication and full-pilot qualification.

Included in beta.10: [the accepted-local-work delivery chain](docs/DELIVERY-CHAIN.md)
reconstructs the actual four-phase local acceptance before creating a new scoped
GitHub grant. Flow renders the draft body, publishes its exact candidate, waits
for exact PR checks, merges under guards and retains the exact main-push handoff.
It does not adopt an old manual PR. The separate release-continuation adapter invokes the project’s existing release tooling under its own scoped authority.


Included in beta.10: [budget-governed repair iteration](docs/CANDIDATE-REPAIR.md),
source-bound incremental verification, a reviewed project-native host adapter,
separate [owner verification-scope decisions](docs/DELIVERY-CHAIN.md), and
[runtime inventory recovery](docs/PROJECT-COMMAND.md) without repeating a completed
package installation. Concrete failed tests remain failures. Unavailable
nonessential verification methods are recorded honestly rather than silently
becoming new requirements. See [remaining autonomy gaps](docs/AUTONOMY-GAPS.md).
