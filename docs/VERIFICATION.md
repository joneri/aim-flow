# Beta verification

## Prior real runtime qualification (earlier controller revision)

On 9 November 2026, controller source `783cb975f897b3179f3a8492a98b24fcc48dead6` completed the maintained installed schema 3 `npm-source-check` path against a reviewed synthetic fixture.

- Actual named command: pinned Node/npm with `--ignore-scripts run check:source`.
- Result: exit 0; `source-check-passed`.
- Dependency installs, application builds and artifacts: none.
- Exactly one VM start and one executing check-container start.
- Staging/readback containers were not started.
- All owned containers and the derived image were confirmed absent afterward; the base image was retained and the VM stopped.
- No model calls, downloads, publication or deployment.

This summary does not publish private operator journals. The retained qualification receipt is identified by SHA-256 `4b53c2e5bc6b95a2541b4646f73007fbcdf6487ff24e7ae757bc864eb88a191d`. A digest is an integrity reference, not independent public verification. Beta.2 has a product-only package layout and reviewed source changes that remove unrelated legacy integrations. Its regression and installation checks are separate from the earlier runtime result. Beta.3 adds proposal-only local task profiles with separate regression and installed-preview checks. Beta.4 removes inherited product-size caps and verifies larger generated inputs, measured source inventories and budget-safe preflight behavior separately. Beta.5 adds separately reviewed private-candidate preparation with generated fixtures, exact-consent refusals, resume and withdrawal checks. Beta.6 verifies selectable output allowance, historical accounting and retained-candidate reconstruction using offline fixtures. Beta.7 verifies private terminal diagnostics, pessimistic accounting for malformed usage and concise patch guidance using synthetic provider replies. No VM, model call or project execution was repeated for these packaging updates.

## What this does not prove

Qualification of arbitrary repositories, hostile-code isolation, independent resource enforcement, an external watchdog, browser tests, hosted effects and product acceptance remain outside this proof. See [Beta scope](BETA-SCOPE.md).

## Packaging regression

The current distribution includes focused package, installation, planning, source-check, intake, preparation and synthetic Docker protocol regression tests. They are offline contract tests; mocked Docker/provider results are not runtime or live-model qualification.

Run the selected suite with Python 3.11+ and Git 2.54+:

```sh
python3 -B -m unittest discover -s tests -p 'test_*.py'
node --test tests/test_npm_platform_build.cjs
python3 scripts/verify_provenance.py
```

[Beta release notes](RELEASE-NOTES.md) record actual results and any omitted checks. The `aim-ci-python-test-matrix.md` describes the offline contracts and their evidence boundaries.

## Sol Responses API proposals (beta.8)

`test_api_delivery.py` exercises exact preview authorization, strict Sol/xhigh requests, token/rate bounds, combined historical-and-new USD20 liability, campaign authority, durable reservation before dispatch, source/history drift, private terminal retention, unknown transport outcomes and no replay. `test_installed_api_delivery.py` repeats the same offline contracts against a freshly installed controller, without a checkout dependency. No test calls OpenAI or reads a real credential.

The legacy mini controller's models, prices and budget rules remain unchanged. Only its credential transport accepts an optional scoped wall deadline with legacy defaults preserved. A prior live Sol proposal is separate pilot evidence, not a repeat qualification of this packaged release or full AIM orchestration.

## Project-specific agent onboarding (unreleased development)

`test_project_onboarding.py` checks the installed prepare/record/verify handoff with Python and nested JavaScript fixtures: exact citations, project-local reasoning, source/controller/Git drift, inert hostile text, schema/authority refusals, step ordering, protected inputs and exclusive private outputs. This verifies evidence retention and validation only. It does not show that Flow independently called an agent, understood a project correctly, executed CI, or deployed anything. See [onboarding boundaries](PROJECT-ONBOARDING.md).

`test_onboarding_api_delivery.py` exercises authorized dispatch with an injected provider, including a source-free installed runner, exact request disclosure, per-assessment and shared-campaign ceilings, durable reservations, source drift, invalid citations, authority claims, refusals, incomplete/unknown outcomes and no replay. No live provider call or credential read is made by these tests.


The compact onboarding correction adds local shape/length/array bounds, canonical wire-order checks, compact reference enrichment, CRLF preservation, excerpt omission disclosure, exact consent changes, omitted-gap rejection and aggregate quote-expansion limits. Its provider replies remain injected offline fixtures. The initial verbose live pilot returned an incomplete answer at its output cap; independently verified partial source citations are not evidence of a completed model assessment or plan qualification. See the [live-pilot boundary](PROJECT-ONBOARDING.md#initial-live-pilot-and-recovery-boundary).


## Project-local validation handoffs (unreleased development)

`test_project_validation.py` exercises a fresh installed controller against a separately pinned predecessor inventory and real committed fixture metadata. It covers normal npm install/run lifecycle declarations, independent job scopes, preserved agent unknowns/review qualifications, index-only and dirty-source exclusion, inventory/bytecode/link refusal, controller/interpreter/instance pins, exact verifier protocol, streaming metadata limits, invalid declarations and source/handoff drift. No npm or project command is executed by planning tests.

The real AIWebSite handoff preserves root `npm ci` then `npm run ci:release-readiness`, separate root/API locks, three independent hosted references and a component-only `test:release-process` selection. Installed planning and freshness verification passed at the reviewed commit. This is an executable-command handoff, not runner dispatch or successful CI.

A separate observed release-process attempt launched the actual npm script in a committed private snapshot under tested process-tree network and write restrictions. It stopped at fixture Git initialization because Apple Git required xcrun-cache writes outside the allowed root and access to `/dev/null`. The component did not pass; no uncontained retry or production deployment occurred. Runtime qualification remains open independently of handoff correctness.

## Project-specific execution development, 2026-11-10

The project onboarding branch adds an explicit offline npm-job executor, separate from assessment/planning and acceptance. Final regression: 433 Python tests (432 passed, one existing fixture-package skip), including 13 new execution tests; installed Sol contracts: 28; Node platform tests: 16. Provenance covers 89 source files and 59 installed controller files. Static-site and whitespace checks passed.

A fresh installed controller executed the real AIWebSite release-process component under Node 24.19.0: 55 reported checks and exit 0. It then executed a source-bound offline root `npm ci` and the complete `ci:frontend` chain: 282 packages installed, prebuild ZIP generation, 261 Astro pages, package checks, Node Windows-contract checks and 55 release-process checks. Source, runtime, control, log and generated-output identities were separately verified. This was not a synthetic stand-in for the project scripts.

The initial frontend attempt correctly stopped when Info-ZIP requested a temporary sibling outside the declared exact archive-file boundary. Its failed receipt is retained. A subsequent explicit attempt used documented `ZIPOPT=-b` to place that temporary archive in already-permitted private scratch; no network or write boundary was broadened. A separately reviewed exact copied-source write exception supports the package test's timestamp mutation, with final source bytes and mode restored. npm 11.17 allowScripts warnings remain in evidence; the esbuild postinstall trace specifically shows exit 0, but no universal dependency-hook or hosted-npm equivalence is claimed.

Backend HTTP fixtures require a separately qualified loopback profile and were not run. The full `ci:release-readiness` aggregate, hosted Actions, native Windows/PowerShell, browser acceptance and deployment remain unqualified. No model request, push, publication or deployment was made in this execution increment. The beta.8 download is unchanged; its bundle-equality gate intentionally differs from this unreleased source and requires a new versioned release rather than relabeling beta.8.
