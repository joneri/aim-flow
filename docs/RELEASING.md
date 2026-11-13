# Release checklist

1. Choose and review a fixed upstream controller commit. Maintain the explicit product-only package allowlist plus deliberate tests/documentation. Preserve unchanged upstream source identities and record reviewed modifications in `provenance/upstream-files.json`.
2. Review license/rights for the controller, website, brand and included dependency metadata. A repository being visible is not a license grant. Do not publish before the owner resolves distribution terms.
3. Run the 17-case smoke selection and the provenance/package/site checks. Full Python/Node regression is manual opt-in only when explicitly requested; never start it automatically on PR or main. See [CI selection](CI.md). Report skipped tests and unqualified effects honestly.
4. Build the distributable **branded ZIP** with `python3 scripts/package_beta.py --output /absolute/path/to/new-output`. It contains the current product-only controller bundle plus MIT notice, third-party notice and current instructions. Do not distribute the raw `aim-flow.py build-release` output alone without its license. Run `python3 scripts/check_bundle.py PATH_TO_ZIP --current-source` to compare every controller file and active version against the release checkout. Verify the ZIP file hashes, bootstrap/archive independently and install into a fresh private directory. Confirm expected unconfigured readiness exit 3.
5. Review and test the static `site/` directory. It needs no package install, analytics, form service or external font.
6. Search the exact staged files for credentials, private paths, operator evidence and accidental application assets. Never publish `.env`, `.aim`, `.sandbox` or runtime journals.
7. After owner approval and permitted network access, refresh the intended repository and publish only reviewed files through an isolated branch/draft PR. Create a repository only if it does not already exist and that creation is authorized. Keep any release marked pre-release. The website presents the same Beta scope as the repository.
8. Set the repository’s Pages build source to **GitHub Actions**. The prepared workflow publishes `site/` on a `main` push only after the selected offline checks pass. Manual test dispatch does not deploy. Pull requests never deploy. It uses the `github-pages` environment and job-scoped Pages permissions; it does not migrate any upstream app release automation.
9. Verify the expected remote commit, required CI and actual HTTPS page after publication. Until these checks pass, call it prepared, not live.

Do not migrate the upstream live automation, repository protections, budgets, API credentials or deployment profiles merely because a new repository exists.


The full installed-lineage suite is available only through manual workflow
dispatch with `full_regression: true`, and its deadline remains configurable.
The automatic default is 17 selected cases. The hosted runner ceiling is an
execution limit, not permission for live API calls or customer deployments.
