<p align="left"><img src="site/assets/aim-flow-beta-light.svg" alt="AIM Flow Beta" width="300"></p>

# AIM Flow · Beta

**Understand the project. Review the next step. Keep the evidence.**

AIM Flow is an experimental local controller for making AI-assisted delivery more deliberate and inspectable. It reads a repository's committed workflow, proposes a project-specific plan and binds approved operations to exact inputs. The goal is a repeatable path from intent to verified delivery, with clear boundaries rather than a blanket promise of autonomy.

**Version:** `0.1.0-beta.1` · **Code license:** [MIT](LICENSE) · **Status:** early technical beta for trusted, reviewed projects.

[Website](https://joneri.github.io/aim-flow/) · [Start safely](docs/QUICKSTART.md) · [What the beta proves](docs/BETA-SCOPE.md) · [Roadmap](docs/ROADMAP.md)

## What you can explore now

- **Project discovery:** inspect committed project metadata, scripts, CI and container definitions without running project code.
- **Project-aware installation:** retain a source-bound adaptation draft separately from the immutable controller.
- **Bounded source checks:** explicitly select one supported npm script and its committed inputs, then separately review preparation and execution. This mode installs no dependencies and produces no application build.
- **Explicit intake:** preview project-bound model intake. Actual API use requires its own grant, consent and existing budget history.
- **Inspectable receipts:** retain hashes, exact intent, results and recovery/cleanup evidence.

A real maintained-controller source-check qualification completed on 9 November 2026. Its check passed and its owned containers and derived image were removed. That proof covers one reviewed fixture and runtime, not arbitrary repositories, hostile code or product acceptance. See the [evidence summary](docs/VERIFICATION.md).

## First look

Use an explicitly selected **Python 3.11+** executable. On macOS, `/usr/bin/python3` may be too old.

```sh
python3 aim-flow.py --version
python3 aim-flow.py --help
```

Start with [archive verification and read-only installation checks](docs/QUICKSTART.md). Project inspection additionally requires **Git 2.54+** with an independently checked executable pin. The execution path requires a reviewed, pinned Linux runtime and separate consent. This is not a one-command hosted service.

## What Beta means

AIM Flow does **not** yet offer universal build support, unattended delivery for arbitrary projects, multi-user governance, a qualified hostile-code sandbox or a general browser-testing path. A source check is not an application build, deployment or product-owner acceptance. See [current boundaries](docs/BETA-SCOPE.md) before planning a pilot.

## Compatibility and provenance

The engine comes from [`joneri/agenticpipeline` at `783cb975`](https://github.com/joneri/agenticpipeline/tree/783cb975f897b3179f3a8492a98b24fcc48dead6). The 52 controller files are preserved byte for byte. `aim-ci-*` filenames, Python module names and archive formats remain unchanged for compatibility. `aim-flow.py` is a source-checkout convenience entry point for the control CLI. It is not included in the unchanged installed controller archive; installed users run `aim-ci-control.py`.

The retained `aim-ci.py`, `aim-ci-release.py` and `api-story-probe.py` are legacy source entry points. Do not use them as a generic AIM Flow runtime: their original registered route targets the upstream repository. They are fenced in a verified installation. Profiles and the two FakeATG dependency manifests are package compatibility inputs, not a bundled demo application or a grant to deploy it.

[Artwork attribution and notices](THIRD-PARTY-NOTICES.md) · [Source inventory](provenance/upstream-files.json) · [Release process](docs/RELEASING.md) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md)
