# Local AIM CI control installation

This package carries the product-only AIM Flow controller, its neutral offline profile and independent validation helpers. Legacy application and production-release integrations are excluded. The archive contains an explicit list of files and a
versioned manifest with a SHA-256 for each file. The archive digest identifies
the packaged bytes, including local changes in the source checkout. The package
is separate from the earlier `pilot.sh` demonstration.

## Build a local release bundle

From a trusted source checkout, generate a release bundle in a new directory.

```sh
python3 aim-ci-control.py build-release --version 0.10.0-local.1 --output /path/to/new-release
```

The bundle contains a standalone `aim-ci-bootstrap.pyz`, the separate control
archive, `release.json` and a recipient README. Send the bootstrap and expected
archive digest through a trusted channel outside the archive. The recipient
can install and inspect a local project without the development checkout.
The README is also available as the [onboarding guide](aim-ci-onboarding.md).
The source commands `build`, `verify` and `install` remain available. Both
installation entry points use the same archive verifier.

## Install for a project's own build process

`install-project` connects installation to read-only discovery and adaptation.
It is available in the source manager and in a current installed manager;
the standalone bootstrap still provides ordinary `install`. Choose a separate
new controller directory and a new project-plan receipt file. Both parents
must already exist. Keep the controller and receipt outside the project and
the invoking control checkout or installation. The receipt must also be
outside the new controller directory.

```sh
"$python" -IBS /path/to/aim-ci-control.py install-project \
  --archive "$archive" --sha256 "$archive_sha256" \
  --destination /path/to/new-project-controller \
  --project-plan-output /path/to/project-evidence/install-plan.json \
  --checkout "$checkout" --commit "$commit" \
  --git "$git" --git-sha256 "$git_sha256" \
  --project-doc README.md --project-doc docs/verification.md \
  --test-script test:pages
```

Use Python 3.11 or newer and an explicit Git 2.54 or newer executable with
its independently verified SHA-256. Supply the checkout's full current HEAD
commit. `--project-doc` and `--test-script` are optional. A named test is an
observation selection, not permission to execute it. Committed project files
are read even when the worktree has uncommitted changes; those changes stay
untouched. The command refuses an existing destination or receipt, linked
paths, overlapping directories, a changed HEAD or a mismatched executable pin.

The command verifies and installs the archive, runs `propose-adaptation` from
that verified installation using isolated Python and a minimal environment,
then rechecks the controller bytes, pinned Git, HEAD and tree. The private
controller directory contains exactly its package files. The separate receipt
binds the archive and manifest hashes, final controller path, project commit
and tree, Git identity, selected documents and test, and complete adaptation
draft. Save the returned `projectPlan.sha256` separately and verify it before
using the receipt later. Exclusive creation prevents this command from
overwriting an earlier receipt; the digest identifies its exact bytes.

The draft learns from the project's committed CI/container definitions,
script bodies and lifecycle hooks, with source identities and unresolved
questions. It is framework-neutral learning input, including for projects
outside the current qualified execution subset. It does not execute actions,
scripts, builds, tests, installers or containers, contact services, or create
journals, budgets or approval records. The checkout is not modified and no
project evidence is added to the controller package.

Successful installation returns `mode: installed-project-review-required`,
`ready: false` and `executionAuthorized: false`. Review the actual CI flow,
command order, lifecycle hooks, dependencies, source scope, artifact and
independent checks before qualifying and separately approving an execution
contract. This receipt is not accepted as an executable recipe or approval.
If inspection fails after the controller was created, the error identifies
the retained installation and reports project installation as incomplete.
Inspect that retained directory, preserve any evidence, and select new output
paths for another attempt. Earlier controller archives remain usable through
ordinary `install`; `install-project` requires a current archive carrying this
feature.

For an explicitly selected source-only npm check, add
`--execution-kind npm-source-check`, `--test-script NAME`, and one
`--source-path PATH` for every exact committed input, including `package.json`
and `package-lock.json`. The command never infers this execution mode from a
framework, script name or workflow. The receipt retains the sorted selection
and a source-bound candidate for separate review when the selected files and
script meet the source-check contract. No dependencies, build, artifacts or
deployment are included. Selected pre/post hooks reject that candidate rather
than being silently skipped.

The new controller can consume its frozen receipt while drafting preparation.
Repeat the exact same document, test, execution-kind and source-path selections:

```sh
"$python" -IBS /path/to/new-project-controller/aim-ci-control.py draft-build-preparation \
  --manifest-sha256 "$manifest_sha256" \
  --checkout "$checkout" --commit "$commit" --git "$git" --git-sha256 "$git_sha256" \
  --project-plan /path/to/project-evidence/install-plan.json \
  --project-plan-sha256 "$project_plan_sha256" \
  --project-doc README.md --project-doc docs/verification.md \
  --execution-kind npm-source-check --test-script check:source \
  --source-path package.json --source-path package-lock.json --source-path scripts/check.mjs
```

The two receipt flags must be supplied together. The draft verifies the receipt
hash, installed controller identity, exact project commit/tree, Git, explicit
selection and frozen adaptation before recording `projectInstallation`
provenance. A different controller, changed receipt or changed selection is
refused. That provenance remains draft-level evidence. The separately reviewed
completed configuration still binds its own source and execution contract;
the installation receipt grants no runtime authority or automatic approval.

```sh
python3 /path/to/new-control-install/aim-ci-control.py readiness \
  --manifest-sha256 "$manifest_sha256"
```

The output file and installation directory must be new. For a valid installation,
readiness reports `packageVerified: true` and exits **3** with `ready: false`.
Exit **1** means a digest, package or configuration check failed. The default
inspection reads the manifest and its listed files and checks which tools are
on PATH. It reports the minimum installed-runtime Python version and adds
`unsupported-python-version` when the selected Python is too old. It performs
no authentication, network request or credential read.
The installed manager exposes the actual available commands through `aim-ci-control.py --help`. Installation and readiness create no runtime state or budget.

The neutral catalog profile is an offline testing aid. Explicit project discovery and reviewed source/build workflows remain the user-facing paths. Git reads require Git 2.54+ and disable lazy fetching and all transport protocols. Profiles and installed bytes must match the reviewed package manifest.

## Runtime requirements and support

The bootstrap requires Python 3.11+ and POSIX descriptor-relative filesystem operations. Project inspection additionally requires an independently pinned Git 2.54+ executable. Local packaging regression uses macOS, Python 3.14.8 and Apple Git 2.54.0. Runtime qualification is a separate claim.

Reviewed execution needs pinned Node/npm and the reviewed Linux runtime described in the preparation configuration. No application dependency manifests, credentials, budgets or journals are included. Setup on another operator's machine requires its own review. Existing consent and budget history must be preserved; installation grants no execution or provider authority.

## Installed local task intake

`run-local-intake` is a separate explicit intake-only entry point, not an
effect of installation or project discovery. It accepts an independently
reviewed project profile and [typed local task](aim-ci.md#typed-local-task),
the exact installed manifest, an existing established shared worker budget
and its original legacy ledger. It never creates a new budget to bypass an
existing limit. A real dispatch additionally requires a separately approved
project-bound version 3 grant and consent to the exact preview intent.

Start with a read-only preview. The explicit key-file path must identify a
private, single-link regular file owned by the invoking principal, outside
the controller, project, state and admission inputs. Preview checks the path
and metadata but does not open, read or hash credential contents. The
credential destination is fixed to OpenAI at `https://api.openai.com/v1`.
No ambient API key or configurable provider URL is selected by this command.

```sh
"$python" -IBS "$installation/aim-ci-control.py" run-local-intake --preview \
  --manifest-sha256 "$manifest_sha256" \
  --profile "$profile" --profile-sha256 "$profile_sha256" \
  --task-file "$task" --task-sha256 "$task_sha256" \
  --key-file "$private_key_file" \
  --state-dir "$existing_worker_state" --legacy-budget "$original_legacy_budget"
```

Without a grant, preview reports `liveModelAvailable: false` and provides
`projectGrantRequirements` as review input: exact operation, project/task/source,
request/scope, original ledger identities and effective ceilings. These values
are not an approved grant and allocate no extra budget or generation slot.
The output contains selected source and task text; keep it local.

After separately reviewing the grant and its limits, repeat the same preview
with paired `--live-grant "$grant" --live-grant-sha256 "$grant_sha256"`.
Review that grant-bound preview and retain its exact `intentSha256`. Only an
explicit invocation with those same inputs and `--authorize-intent` can
dispatch the bound intake:

```sh
"$python" -IBS "$installation/aim-ci-control.py" run-local-intake \
  --authorize-intent "$intent_sha256" --manifest-sha256 "$manifest_sha256" \
  --profile "$profile" --profile-sha256 "$profile_sha256" \
  --task-file "$task" --task-sha256 "$task_sha256" \
  --live-grant "$grant" --live-grant-sha256 "$grant_sha256" \
  --key-file "$private_key_file" \
  --state-dir "$existing_worker_state" --legacy-budget "$original_legacy_budget"
```

The controller checks the grant, exact consent and shared-budget reservation
before opening the private credential descriptor or contacting the provider.
Intake produces only a local proposal. It does not change the project, create
a candidate or PR, run a build, merge, deploy or grant product acceptance.
Retain the original inputs and state. Add `--resume` to the same authorized
invocation to inspect/reconcile its retained attempt without reading credential
contents or replaying a provider request. `--resume` cannot accompany
`--preview`. An uncertain or failed paid request retains its reservation and
does not authorize an automatic retry. The existing `preview-root-admission`
commands retain their prior read-only behavior.

## Hosted integration design

A hosted integration needs separate approval for its target, operators and
credentials. A reviewed target record should identify the repository, allowed
trigger and actor, target branch, registered recipe, product profile digest, approved runners,
control archive and manifest digests, runtime identity and allowed destination.
Fetch immutable inputs from a trusted location and verify their expected
digests before importing the package or running repository code. Bind the
validated assignment identity to that target record, the exact commit and the
retained evidence. The catalog
profile remains offline-only. The existing `active_solo_poc` authority and
hosted rules govern any future authorization.

| Phase | Identity to retain | Rights and required checks |
| --- | --- | --- |
| Admission | Issue revision, assignment digest, authenticated actor, target record, base commit | Read issue and repository metadata. Verify opt-in, scope, actor and cumulative budget before allocating any approved model access. |
| Candidate and CI | Assignment, exact candidate commit, control and profile digests, runtime identity, checks, artifact and SBOM digests | Use isolated, read-only source access with no release credentials. Require repository tests, real dependency audit, reviewed severity policy and artifact provenance tied to the exact commit. |
| Review and merge | Candidate head, independent review evidence, required check runs and merge commit | Grant PR and merge permissions only to the approved actor. Recheck the head and rules immediately before merge. Revalidate the exact merge commit for release. |
| Release and observation | Assignment, merge commit, signed attestation, artifact digest, authorized environment and browser evidence | Give only the release job environment-scoped deployment rights. Verify signing identity, provenance, required audit/checks and approval for the exact artifact before promotion. Verify the served bytes and product behavior at the approved destination. |

A future qualification must demonstrate real signing and independent signature
verification, least-privilege permissions, hosted audit and release gates, and
recovery with original journals after an interrupted operation. Technical proof
and product-owner acceptance have separate evidence. No hosted integration is
activated by the local setup.

## Installed offline catalog

An installed package can run an explicitly assigned synthetic catalog issue
through the same controller, real Git, local CI, artifact promotion and
Chromium verification on loopback. It needs a trusted, separate Git checkout
with the committed
`catalog/` product, the pinned HEAD on the local bare origin's `main`, and an
independently provisioned runtime. Keep the checkout, origin, runtime,
installation and new synthetic state in separate directories. The worker
copies the pinned commit into its private Git repositories. The catalog is an
offline verification target. Product acceptance remains an owner decision.

Use an already available compatible Playwright installation and its matching
Chromium headless shell. Copy `playwright`, `playwright-core` and
`@playwright/test` into the same layout under the new runtime's
`node_modules/`. Copy the selected Chromium revision into `browsers/`.
Use regular files with their executable bits preserved. The runtime can be
prepared before installing the control package. It requires no downloads at
execution. Select absolute Node and Git executable paths, using Git 2.54+
and a Git executable named `git`. On macOS, `xcrun --find git` identifies
the selected developer-tool binary. Python, macOS sandbox-exec and tar are
also included in the runtime identity. macOS and the chosen toolchain supply
their system libraries.

Setup, preflight, execution and status require Python 3.11 or newer. The
installed catalog job was exercised with Python 3.14.8, macOS 27.3, Apple Git
2.54.0, Node 26.7.0 and Playwright 1.62.1 on one machine. Use the same explicit
Python executable throughout. A second operator and machine still need
qualification.

Prepare the runtime from the local modules and browser selected above.
The Chromium argument is relative to `browsers/`, for example
`chromium_headless_shell-1234/chrome-headless-shell-mac-arm64/chrome-headless-shell`.
Choose an absolute configuration path outside the installation, runtime,
checkout, origin and state. The configuration and state parents must already
exist. Run setup from outside the development checkout.

Save the assignment as a separate JSON file. The installed recipe
`catalog-heading-v1` changes two exact lines in the trusted commit. It
updates the `TITLE = '…'` line in `catalog/catalog.py` and the matching expected
heading in `catalog/tests/test_catalog.py`. Both lines must exist exactly once
at the target commit. The assignment names only the source path. The recipe
supplies the fixed test path and assertion. It accepts a synthetic issue number
between 1 and 999999 and short title and heading strings made of ASCII
letters, digits, spaces or hyphens. The before and after headings must differ.

```json
{
  "version": 1,
  "recipe": "catalog-heading-v1",
  "issueNumber": 217,
  "title": "Use the seasonal catalog heading",
  "path": "catalog.py",
  "fromHeading": "Synthetic catalog",
  "toHeading": "Seasonal catalog"
}
```

The recipe retains the existing test, including its price assertion, the
independent oracle and the exact four-item fixture output. The product and browser checks still require the expected item order,
prices and escaped HTML, plus the requested heading. The configuration
selects the registered recipe and data values. It cannot supply code, shell
commands, a different recipe, product tests or arbitrary datasets.

```sh
manager=/path/to/install/aim-ci-control.py
assignment_sha256=$(shasum -a 256 "$assignment" | cut -d " " -f 1)
"$python" -I "$manager" setup-offline \
  --manifest-sha256 "$manifest_sha256" --product catalog \
  --assignment "$assignment" --assignment-sha256 "$assignment_sha256" \
  --checkout "$checkout" --origin "$local_bare_origin" --head "$catalog_head" \
  --runtime "$runtime" --node "$node" --git "$git" --chromium "$relative_chromium" \
  --state "$new_synthetic_state" --output "$config"
```

Setup verifies the package, checkout, origin, explicit runtime and assignment.
It saves the assignment and its canonical digest in one new configuration file
and prints its `configSha256`. Save that digest separately. The state binding
and result receipt also identify the assignment. Use the operator-approved
runtime as input to setup. Hashing pins those bytes for later verification. It
does not establish their provenance.

```sh
"$python" -I "$manager" preflight-offline --config "$config" --config-sha256 "$config_sha256"
"$python" -I "$manager" run-configured-offline --config "$config" --config-sha256 "$config_sha256"
"$python" -I "$manager" status-configured-offline --config "$config" --config-sha256 "$config_sha256"
```

Preflight rechecks the config, installed manifest, package bytes, runtime and
target commit and the two expected heading lines. It reports new or
retained state without creating state or launching a worker. The run command is the explicit start and uses an isolated
HOME. Leave loopback port 4173 available. The direct commands
`pin-runtime`, `run-installed-offline` and `status-installed-offline` remain
available with explicit arguments. Direct run and status require the assignment
file and its digest.

Keep the original configuration, installation, runtime and target paths to read
a retained run. Moving a configuration or pointing it at a different
installation is rejected. Changes to the assignment, runtime, target, profile
or manifest require a new reviewed configuration and an explicitly separate
synthetic attempt. A different configuration cannot claim an existing attempt's budget
history. Setup leaves retained journals in place. Earlier configurations use a
different schema and remain readable with their original package.

Status reports that no installed run exists before the first start. Repeating
the run command with the same complete state reads its receipt.
Ctrl-C stops the foreground process. Preserve the state after an interruption,
including the binding, worker state and vendor records. An attempt without a
final receipt requires manual inspection. General installed replay and
migration are unsupported.
