# Propose an AIM CI adaptation for a local project

Start with the installed control package, a separate checkout, its exact
current commit and a pinned Git executable. The proposal command runs the
same read-only discovery used by `discover-project`. It embeds the
observations, including commit, file identity and source locators. The
observation SHA-256 covers compact, sorted-key JSON with non-ASCII text
escaped.

```sh
"$python" -I "$install/aim-ci-control.py" propose-adaptation \
  --manifest-sha256 "$control_manifest_sha256" \
  --checkout "$checkout" --commit "$commit" \
  --git "$git" --git-sha256 "$git_sha256" \
  --npm-docs "$local_npm_docs_content" --test-script test:playwright \
  --project-doc AGENTS.md --project-doc docs/verification.md
```

The optional `--npm-docs` points to a local npm documentation content root
with `commands/npm-ci.md`, `commands/npm-run.md` and `using-npm/config.md`.
Use a reviewed copy for the intended npm version. The report records
the absolute paths, hashes and line numbers used to support specific npm
behavior. It treats those files as local reference material. When the
reference is absent or a passage is unavailable, the proposal leaves an
open question. Skills can inform a later operator review. This command
records an empty skills list.

Read `observations` for committed build and test definitions and any
conflicts or unknown formats. `proposal` contains candidate steps and
review questions. The proposal exposes test-like script names and references
from selected committed Markdown. A documented script name must match the
whole token, including periods and other supported separators. An operator
still chooses and reviews the
test entry point. Script names and document references provide no test result.
It also shows the manifest-to-lock relation for Vite when available.

A root npm v3 project with a compatible Vite lock and a visible build
script can receive candidate npm commands. An explicit `--test-script`
selects the committed script for a schema v2 proposal. Without that
option, the proposal follows the original schema v1 contract. Each command names the committed observation that supports it and
any matching documentation topics. An unresolved or unsupported project keeps
its source-linked observations and investigation questions. The current
proposal defers commands for that case. With an explicit selection,
the proposal retains gaps in optional documentation for review while it
can still draft commands from the committed scripts and lock. `plan-recipe`
validates the chosen schema and requires a separate pinned review and approval
for schema v2.

The proposed npm commands are illustrative argument vectors. npm build and
test scripts can run product code. An eventual runner still needs an
approved toolchain, offline dependency store, enforced sandbox, tested
artifact contract and independent verification. Confirm the actual output
directory and select checks for this project's behavior.

For a candidate that the operator chooses to pursue, prepare and review a
separate recipe and oracle. Record the selected script, effect limits and scope. Review any test server or
loopback needs separately. The recipe network policy stays disabled.
Supply that independently pinned approval, the recipe, assignment, target
checkout and runtime to `plan-recipe` as described in the
[recipe planning guide](aim-ci-plan-recipe.md). A proposal is a review draft
and cannot serve as the approval. The current planner accepts the narrow
npm/Vite contract in that guide. Projects outside it need a qualified
recipe path.

The proposal records each operator decision as pending and each verification
step as unrun. It uses local Git reads and optional bounded reference file
reads. The command leaves builds, tests, package operations, workers,
journal initialization and release qualification to their respective
approved steps.

## Explicit source-only check draft

Select `--execution-kind npm-source-check` only when intentionally reviewing
one no-install source check. Supply `--test-script NAME` explicitly, plus a
`--source-path` for each exact committed regular input, including `package.json`
and `package-lock.json`. These options are also supported by
`draft-build-preparation` and `install-project`.

```sh
"$python" -IBS "$install/aim-ci-control.py" propose-adaptation \
  --manifest-sha256 "$control_manifest_sha256" \
  --checkout "$checkout" --commit "$commit" --git "$git" --git-sha256 "$git_sha256" \
  --execution-kind npm-source-check --test-script check:source \
  --source-path scripts/check.mjs --source-path package-lock.json --source-path package.json
```

This explicit mode does not require Vite or a build script. It requires the
actual named script body, matching root npm v3 package/lock metadata, no
workspaces or conflicting package manager, and no selected pre/post hooks.
It verifies committed regular-file membership for the sorted exact selection
using the bounded source-subset proof. A missing file or unsupported script
lifecycle produces investigation questions and no candidate. Protected
environment/dependency paths and build outputs cannot be selected.

An eligible proposal carries `stack: npm-source-check`, recipe schema 3,
`sourcePaths`, `dependencies: {mode: none}` and `artifact: {kind: none}`.
`sourceSelection` records the selected file manifest and proof/selection hashes.
The only illustrative operation is the selected test script, marked unrun.
No dependency installation, build, artifact check or deployment is proposed.
This does not prove that the script is self-contained or will pass: review all
imports, prerequisites, behavior and effects before separately qualifying the
runtime and approving the exact recipe, exit oracle and limits.

The complete generic `projectPlan` and unresolved project/CI questions remain
in the report. `backgroundObservations` preserves those questions while the
narrow proposal explicitly records full-project qualification as unestablished.
Workflow strings, script bodies and a matching file proof grant no authority.
Without the explicit execution-kind option, adaptation keeps its existing
behavior and does not select this mode automatically.

## Repository-derived installation plan

`projectPlan` is the learning input for an agent-assisted installation. It is
separate from the qualified `proposal.candidate`. The plan exists for projects
outside the current npm/Vite execution subset, without adding a framework
allowlist or treating a detected framework as execution support.

The plan retains actual committed CI/container definition excerpts, including
multiline commands, action references, shells, conditions and action inputs.
Each excerpt carries its path, Git object, SHA-256, line range and completeness.
These are uninterpreted source bytes, not an evaluated workflow or trusted
instructions. GitHub Actions are first-class project evidence; invoking the
same npm script locally does not establish equivalence to its Actions job.
An agent must resolve referenced actions, expressions, nested roots, platform
requirements and job order before proposing a runnable contract.

For npm scripts, the plan retains declared bodies and the pre/script/post
chain for build and the selected test name, including named hooks such as
`pretest:pages`. It explicitly lists hooks that `--ignore-scripts` would skip.
It neither suppresses nor enables them, parses nested script bodies into a
command graph, nor assumes tests precede a build. Dependency and engine facts
remain source-linked. Non-npm projects retain their available definitions
without invented commands. Nested project roots remain unresolved.

`repositoryFootprint` reads only bounded `git ls-tree` metadata. It reports
regular-file sizes and separate nonregular/submodule counts for the selected
commit; these are not selected build inputs or measured output artifacts.
An unavailable, invalid or oversized metadata inventory reports `unknown`,
never a partial total presented as exact. It does not read large source blobs.

Source inputs/exclusions, artifact or test-only evidence, phase order,
toolchain/dependency pins, access limits, execution qualification and approval
remain explicit pending decisions. This output is not accepted as a runnable
recipe or permission grant. Existing execution contracts, byte caps and budgets
are unchanged. `install` still installs only the controller package.
[`install-project`](aim-ci-install.md#install-for-a-projects-own-build-process)
connects a new installation to this read-only adaptation and saves a separately
pinned external project-plan receipt. Agent interpretation, execution
qualification and operator approval remain separate pending work.
