<p align="left"><img src="site/assets/aim-flow-beta-light.svg" alt="AIM Flow Beta" width="300"></p>

# AIM Flow · Beta

**Understand the project. Review the next step. Keep the evidence.**

AIM Flow helps you understand a software project, choose the next step and check what happened. It reads the project's committed files and workflow, proposes a plan for that project and keeps evidence for the operations you approve. The goal is to make AI-assisted development easier to review and repeat.

**Version:** `0.1.0-beta.8` · **Code license:** [MIT](LICENSE) · **Status:** early technical beta for trusted, reviewed projects.

[Website](https://joneri.github.io/aim-flow/) · [Start safely](docs/QUICKSTART.md) · [What the beta proves](docs/BETA-SCOPE.md) · [Roadmap](docs/ROADMAP.md)

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

An earlier controller revision completed a real source-check qualification on 9 November 2026. Its check passed and its owned containers and derived image were removed. The current distribution is checked separately with offline regression and installation tests; the Sol packaging update does not repeat or extend that runtime proof. See the [evidence summary](docs/VERIFICATION.md).

## First look

Use an explicitly selected **Python 3.11+** executable. On macOS, `/usr/bin/python3` may be too old.

```sh
python3 aim-flow.py --version
python3 aim-flow.py --help
```

Start with [archive verification and read-only installation checks](docs/QUICKSTART.md). Project inspection additionally requires **Git 2.54+** with an independently checked executable pin. The execution path requires a reviewed, pinned Linux runtime and separate consent. This is not a one-command hosted service.

## What Beta means

AIM Flow does **not** yet offer universal build support, unattended delivery for arbitrary projects, multi-user governance, a qualified hostile-code sandbox or a general browser-testing path. A source check is not an application build, deployment or product-owner acceptance. See [current boundaries](docs/BETA-SCOPE.md) before planning a pilot.

## Project background

AIM Flow is the standalone public beta of AIM's delivery-controller work. This repository contains the product's code, documentation and website. You do not need a separate development repository or demo application to install it.

The [source inventory](provenance/upstream-files.json) records where the code came from and which parts have changed. Installation commands and requirements are explained in the [quickstart](docs/QUICKSTART.md).

[Artwork attribution and notices](THIRD-PARTY-NOTICES.md) · [Source inventory](provenance/upstream-files.json) · [Release process](docs/RELEASING.md) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md)

Unreleased development: [project-specific agent onboarding](docs/PROJECT-ONBOARDING.md) exports committed evidence and retains source-cited installation, test, CI and deployment reasoning in a separate project instance. The optional `onboard-api` route can dispatch a separately approved Sol assessment and retain the resulting instance. Its dispatch contracts are offline-tested; no route executes the proposed plan.
