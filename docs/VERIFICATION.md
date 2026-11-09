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

This summary does not publish private operator journals. The retained qualification receipt is identified by SHA-256 `4b53c2e5bc6b95a2541b4646f73007fbcdf6487ff24e7ae757bc864eb88a191d`. A digest is an integrity reference, not independent public verification. Beta.2 has a product-only package layout and reviewed source changes that remove unrelated legacy integrations. Its regression and installation checks are separate from the earlier runtime result. Beta.3 adds proposal-only local task profiles with separate regression and installed-preview checks. Beta.4 removes inherited product-size caps and verifies larger generated inputs, measured source inventories and budget-safe preflight behavior separately. Beta.5 adds separately reviewed private-candidate preparation with generated fixtures, exact-consent refusals, resume and withdrawal checks. No VM, model call or project execution was repeated for these packaging updates.

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
