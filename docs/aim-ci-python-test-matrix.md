# AIM Flow Beta offline test matrix

Use Python 3.11+ and a POSIX filesystem. The complete local suite also needs Git 2.54+ for real temporary-repository inspection. The reference machine uses Python 3.14.8 and Apple Git 2.54.0. These tests do not grant model, container or deployment permission.

```sh
python3 -B -m unittest discover -s tests -p 'test_*.py'
node --test tests/test_npm_platform_build.cjs
python3 scripts/verify_provenance.py
```

## Included coverage

- `test_control_package.py` and `test_source_free_release.py`: product-only membership, schema, integrity, fresh bootstrap installation, read-only readiness and malformed/linked input refusal.
- `test_project_installation.py` and `test_project_planning.py`: exact committed project learning, source-bound receipts, explicit script selection and installation/planning without executing project scripts.
- `test_source_check.py` and `test_source_subset.py`: bounded no-install source checks, selected-file inventories and exact source membership.
- `test_project_intake_grant.py` and `test_model_support.py`: project-bound grants, preserved budget history, private fake-key handling and model-support safety fences. Provider replies are synthetic; no real credential or model call is used.
- `test_operator_build.py` and `test_build_preparation.py`: explicit operator consent, preparation/build bindings, interruption, status and owned-resource cleanup using guarded Docker responses.
- `test_docker_engine.py`, `test_docker_image_evidence.py`, `test_docker_recipe.py` and `test_recipe_execution.py`: adapter admission, request/response contracts, byte readback, pre-dispatch gates and recovery with protocol fixtures.
- `test_npm_acquisition.py` and `test_npm_platform.py`: locked tarball, registry-policy, platform-selection and integrity contracts with synthetic network transport.
- `test_npm_platform_build.cjs`: source-only dispatch and helper behavior using local fixture bytes and a synthetic cache adapter.

- `test_intake_profile.py`: strict proposal-only profiles without build placeholders, installed no-key preview, source/grant binding, separately reviewed private-candidate consent/resume/withdrawal and execution refusal. Uses real temporary repositories and Git 2.54+; explicitly skips when that version is unavailable. Synthetic provider replies do not qualify live intake.

- `test_project_discovery_sizes.py`: complete committed definitions, selected documents, workflows and repository inventories beyond the former fixed discovery limits.
- `test_content_size_contracts.py`: larger generated requirements, exact patches and request output allowances, with original path, source and provider safeguards retained.

## Evidence boundaries

Mocked Docker and provider results are not real runtime or live-model qualification. Local Git fixture tests do not execute project scripts. Skipped cases provide no evidence for their skipped path. The public CI workflow requires Git 2.54+ and runs the complete offline Python suite plus the Node helper tests before publication. The full Mac suite and exact totals are recorded in [release notes](RELEASE-NOTES.md).

The separate package smoke check verifies the branded ZIP against trusted source bootstrap bytes, checks all payload hashes, installs a fresh controller and expects unconfigured readiness exit 3:

```sh
python3 scripts/check_bundle.py site/downloads/aim-flow-0.1.0-beta.5.zip
```

A passed source check is not an application build, browser-test result or product acceptance. This packaging cleanup does not repeat a real execution qualification. See [Beta scope](BETA-SCOPE.md) and [verification](VERIFICATION.md).
