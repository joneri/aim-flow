# Plan an operator-approved npm/Vite recipe

The installed control package can produce a read-only plan for a small static
Vite repository. Python 3.11 or newer runs the planner. Planning calls a
pinned local Git 2.54 or newer executable for inspection. A host plan
checks the local Node and npm CLI paths and hashes. A container plan
binds the declared tools and image from a separately reviewed profile.

Select a separate checkout of the repository and the exact commit to
review. The checkout must be at the approved local branch and commit and
have one matching local `remote.origin.url` with the form
`https://host/owner/project.git`. The check reads Git metadata. It does not
authenticate the remote. Select a repository whose root contains a
`package.json` with a `build` script and a v3 `package-lock.json`.
Schema v1 also requires `scripts.test` and exact declaration-to-lock
equality for Vite. Schema v2 accepts one explicitly selected test script
and checks the supported stable exact or caret relation. The planner
reads committed bytes, so uncommitted edits are excluded.

Prepare a separate recipe directory containing three JSON files. In
`recipe-manifest.json`, set `format` to `aim-ci-recipe`, choose an `id`
and semantic `version`, and put the file hashes in `files` as
`{"recipe.json":"…","oracle.json":"…"}`. Use the manifest hash as the
recipe package pin. Obtain it from the recipe review records, outside the
directory you are verifying.

```json
{
  "version": 1,
  "stack": "npm-vite-static",
  "operations": {
    "dependencies": ["ci", "--offline", "--ignore-scripts", "--no-audit", "--no-fund"],
    "test": ["--ignore-scripts", "run", "test"],
    "build": ["--ignore-scripts", "run", "build"]
  },
  "allowedPaths": ["src/main.js"],
  "forbiddenPaths": ["tests"],
  "artifact": {"directory": "dist", "requiredFiles": ["index.html"], "maximumBytes": 2000000},
  "limits": {"network": "disabled", "timeoutSeconds": 120, "memoryMiB": 1024, "maxProcesses": 16}
}
```

For schema v2, set `version` to `2`, replace the `operations` object
with `"testScript": "test:playwright"`, and retain the other fields.
The selector is a literal committed script name with the same ASCII syntax
as discovery. The planner supplies the dependency, build and test argument
vectors. The test vector ends with `run` and the selected name. Schema v1
retains its fixed `operations` object and original approval format.

Save those fields in `recipe.json`. Use committed, exact file paths for
the change allowlist. The planner also protects control, dependency and
artifact paths. The `oracle.json` file can declare a required heading
through `{"kind":"static-heading-v1","file":"index.html","heading":"Service status"}`.
The plan lists the oracle and artifact checks for a future runner.

Review the package, its scripts, the commit and the planned permissions.
The approval file lives outside the product checkout and recipe package.
Use this shape with the reviewed values.

```json
{
  "version": 1,
  "kind": "operator-record",
  "reviewReference": "Review identifier from the operator",
  "controlManifestSha256": "…",
  "recipeManifestSha256": "…",
  "target": {"repository": "https://example.com/team/site.git", "branch": "main", "commit": "…"},
  "runtime": {
    "git": {"path": "/absolute/real/path/to/git", "sha256": "…"},
    "node": {"path": "/absolute/real/path/to/node", "sha256": "…"},
    "npm": {"path": "/absolute/real/path/to/npm-cli.js", "sha256": "…"}
  },
  "committedInputs": {"package.json": "…", "package-lock.json": "…"},
  "state": {"mode": "uninitialized", "path": "/absolute/path/to/new-planning-state"}
}
```

For schema v2, prepare a separate review file with the fields below.
Set the approval's additional `reviewSha256` to the digest of that file.
Supply the review and its independently provided digest separately from the
approval and its digest. Keep both outside the product and recipe packages.

```json
{
  "version": 1,
  "decision": "reviewed-for-planning",
  "kind": "operator-record",
  "reviewReference": "Review identifier from the operator",
  "controlManifestSha256": "…",
  "recipeManifestSha256": "…",
  "target": {"repository": "https://example.com/team/site.git", "branch": "main", "commit": "…"},
  "assignmentSha256": "…",
  "runtimeSha256": "…",
  "committedInputs": {"package.json": "…", "package-lock.json": "…"},
  "testScript": "test:playwright"
}
```

Use the same kind and review reference in both files. The runtime SHA-256
covers the approval's runtime object encoded as compact UTF-8 JSON with
sorted keys and unescaped non-ASCII text. A retained state for schema v2
also binds `reviewSha256`. Synthetic review and approval files use
`kind: synthetic-fixture`. They demonstrate binding and planning only.
Hashes verify bytes against the supplied pins. Operator identity and
execution authority require their own controls.

The npm path names the real executable JavaScript CLI file. The planned
argument vectors begin with the pinned Node executable and that file. Pin
the approval bytes separately, using the operator's trusted review channel.
The planner checks agreement with that pin. It does not cryptographically
authenticate the operator or grant execution rights.

An assignment JSON file contains `version: 1`, `id`, `title`, an
`acceptance` string and a `paths` array selecting exact reviewed files.
Retain its SHA-256 outside the file. An assignment can describe a change
within those bounds. It cannot add commands or expand the allowlist.

```sh
"$python" -I "$install/aim-ci-control.py" plan-recipe \
  --manifest-sha256 "$control_manifest_sha256" \
  --recipe-package "$recipe" --recipe-sha256 "$recipe_manifest_sha256" \
  --approval "$approval" --approval-sha256 "$approval_sha256" \
  --review "$review" --review-sha256 "$review_sha256" \
  --assignment "$assignment" --assignment-sha256 "$assignment_sha256" \
  --checkout "$checkout"
```

Use the two review options for schema v2. Omit them for schema v1.

### Describe a reviewed container

For the schema v2 npm/Vite recipe, create a profile outside the checkout
and packages, separate from the assignment, approval and review. It
describes a preloaded local Docker image ID, the in-container Node and
npm JavaScript CLI, and the SHA-256 of the intended offline dependency
store. Use lowercase hexadecimal digests and distinct absolute container
paths. The format accepts only the fields shown.

```json
{
  "version": 1,
  "imageDigest": "sha256:…",
  "tools": {
    "node": {"path": "/opt/aim-ci/node", "sha256": "…"},
    "npm": {"path": "/opt/aim-ci/npm-cli.js", "sha256": "…"}
  },
  "dependencyStoreSha256": "…"
}
```

1. Obtain the SHA-256 of the profile file from the trusted review channel.
   Add that digest as `containerProfileSha256` to the approval and review.
2. Set the approval and review record versions to `2`. In the approval,
   keep only `git` in the `runtime` object. In the review, set
   `gitReaderSha256` to the canonical JSON hash of that object,
   `runtimeSha256` to the canonical JSON hash of the profile's `tools`
   object, and `targetTree` to the reviewed Git tree ID. The review still
   binds the control and recipe, target, committed input digests, literal
   script and assignment. The approval still binds the review file digest.
3. Run the same `plan-recipe` command with
   `--container-profile "$profile" --container-profile-sha256 "$profile_sha256"`
   in addition to the existing arguments.

The version 2 plan records the locally checked Git pin, the matched profile
and review pins, the source commit and tree, and the declared container
tools, image and dependency identity. Image, tool and dependency bytes
inside the image still need independent verification. The input hashes
establish agreement with the supplied records. Human identity and
execution authority are separate.

Container plans bind `containerProfileSha256`, `targetTree` and
`gitReaderSha256` in retained state, alongside the existing binding fields.
A state recorded with another runtime, dependency profile or review cannot
be reused with the changed identity.

### Read the plan

The plan goes to standard output. Its status is `planned-only`, and every
command, oracle and release requirement is marked as pending. It reports
the exact source tree, committed input digests and visible npm scripts.
Even with npm's lifecycle suppression flags, the explicit build and test
scripts execute product code during a future run. Dependency code and npm
configuration also require review. A committed lockfile establishes the
chosen versions and recorded integrity values. A future runner must
verify an offline dependency store and enforce the sandbox and resource
limits. Audit, SBOM, signing, provenance, release authorization and
destination checks remain pending.

An uninitialized state path must be absent. The plan leaves it absent and
reports the budget as uninitialized. A retained planning state uses
`mode: retained`, the original absolute path and a `files` map with
reviewed digests for `binding.json`, `legacy.json`,
`budget-established.json` and `budget.json`. Its binding contains the
control and recipe manifest hashes, approved target, assignment file
hash, canonical runtime hash and absolute `statePath`. Plan output reports retained limits and
call count after validation. State initialization and migration are
outside this command. Synthetic fixture approval uses
`kind: synthetic-fixture` and represents test data.

One repository with one declared Vite recipe is the qualification boundary.
Recipe execution, code adapters, second-operator qualification and hosted
release require separate work.

For schema v2, the Vite comparison uses the same complete stable exact or
single caret subset as discovery. A mismatch or unsupported syntax stops
planning. The plan binds the manifest's declaration and lock resolution
separately, and identifies the selected script by its committed source
locator. These checks establish consistency within the subset. Script
behavior, dependency installation and oracle suitability remain open.
The recipe's network policy stays disabled. Any test server, including a
Playwright loopback server, needs separate runtime and effect review.

## Check execution readiness

Save the schema v2 plan and retain its SHA-256 separately. Use the same
installed control package and its pinned manifest.

```sh
"$python" -I "$install/aim-ci-control.py" check-recipe-execution \
  --manifest-sha256 "$control_manifest_sha256" \
  --plan "$plan" --plan-sha256 "$plan_sha256"
```

The command checks the saved plan pin, identity fields, resource limits and
installed manifest. It reports gaps for the selected backend and exits with
code 3 before dispatch. A malformed plan or mismatched pin exits with code 1.
For native plans, network and workspace write policies are available.
For container plans, Docker policy, image, tools, dependencies and all
requested runtime controls, including loopback denial, remain unqualified
in the public check. Readiness leaves the target and effect journals alone.

The internal unit-test seam accepts a trusted in-process stub and a
separately pinned synthetic authorization. It compares the frozen plan
with the current trusted test plan and binds the selected effect to one
journal. A duplicate returns its recorded simulation. An unknown outcome
stays unknown. Simulated outcomes have a distinct status and a null actual
exit code. This seam is only available to test code.

### Docker preparation

Trusted controller code can supply a separately pinned Docker policy to
`check_recipe_execution`. The policy binds one local Unix endpoint, image
digest, adapter digest, reviewed container runtime digest and control
manifest. For a container plan, preparation compares the image and runtime
identities with that policy. The private lifecycle also compares the
declared dependency store identity with its separately supplied store pin.
The public command supplies no policy or transport and blocks before
dispatch.

The preparation record separates requested controls from observations.
Admission requires at least 128 MiB. The adapter requests equal memory and
combined memory-and-swap limits, a process limit, a read-only root filesystem
and network mode `none`. Docker still permits internal loopback, so the
strict loopback requirement remains unqualified.

The fixture path uses the existing effect journal to persist a stable
intent before requesting a simulated outcome. Reconciliation requires the
same effect, container name and policy identity, the independently
specified fixture container ID and a terminal outcome. Unknown outcomes
stay unknown. Fixture exit, signal, OOM, timeout and cleanup data sit under
`fixtureLifecycle`.

The internal Docker lifecycle adapter constructs Engine API v1.43 requests over
a pinned Unix endpoint. It expects a preloaded image identified by its local
Docker image ID and a reviewed image layout with the fixed
`/opt/aim-ci/run-recipe` entrypoint. The image declares the exact source commit,
tree, reviewed in-container Node/npm identity, and independently pinned offline
dependency store. The adapter sends one literal named test to the entrypoint.
It supplies no host bind mounts or image pulls. The current host-tool plan does
not satisfy the container-runtime check.

The journal records the deadline and create intent before dispatch, then retains
the first confirmed full container ID. Start, termination, and removal use that
ID. Uncertain operations are reconciled by observation. A lost start reply
followed by a still-created container past the deadline leaves start and
cleanup unknown. A terminal observation and confirmed container absence are
separate records. The controller must keep calling the lifecycle adapter to
observe the deadline. It has no independent watchdog, so a deadline or client
timeout alone supplies no proof of whole-job termination during controller
failure.

In a bounded local run, the private adapter ran one controller-owned npm
fixture without third-party dependencies. The image entrypoint verified the
pinned source and Node/npm bytes before npm ran the selected script. The
fixture used two distinct empty npm config files to isolate the user and
global config scopes. It started a detached child and emitted readiness.
The adapter observed the running container, sent an exact-ID kill and
confirmed removal. The bound cgroup and four tracked processes were absent
afterward.

The kill request was sent 0.382 seconds after the recorded 15-second
deadline, including controller polling and observation overhead. Docker
reported about 15.120 seconds from start to exit. This run tested a deadline
managed by an active controller. An exact hard cutoff and recovery after
controller failure remain unqualified. The adapter removes the container
after observing its terminal state, so any required logs or readiness
evidence must be captured before removal. The current interface returns
lifecycle evidence and has no general artifact verifier.

Normal validation exercises the contract with in-memory responses. The
local fixture result applies to the private adapter and that fixture. The
public planner can bind a reviewed container profile for the npm/Vite
recipe. General repository execution, independent resource
and filesystem enforcement, and strict loopback denial require separate
qualification.

### Verify image input bytes

The internal controller can verify a reviewed container plan using an
externally pinned plan, profile and fixed entrypoint digest. The maintained
layout reads only the Node and npm CLI paths in the profile,
`/opt/aim-ci/run-recipe`, and these paths under `/opt/aim-ci/qualification`.

- `source.commit` is the raw Git commit object payload without its Git
  object header. `source/` contains the complete committed source tree.
  The verifier reconstructs the Git SHA-1 tree from actual file bytes,
  literal ASCII paths and executable modes, then checks the commit, tree,
  committed package and lock hashes, and the selected script.
- `dependency-store/index.json` is compact sorted UTF-8 JSON without a
  trailing newline. Its SHA-256 is the reviewed `dependencyStoreSha256`.
  It has `kind: npm-v3-tarballs-v1`, `lockSha256`, and a `packages` map.
  Each non-root npm v3 lock locator maps to its exact `integrity` and
  a `sha256` for `blobs/<sha256>.tgz`. Every lock entry needs coverage,
  including optional entries. Shared blobs may serve multiple entries.
  The verifier compares the actual tarball SHA-256 and single SHA-512 SRI
  against the index and lock. Workspaces, links, bundled entries, and
  dependencies outside the supported HTTPS tarball layout are refused.

Readback accepts bounded uncompressed tar with literal paths, regular files
and required directories. Unsupported metadata, linked or duplicate members,
unexpected paths and mismatches are refused. Node bytes stream separately
from Docker's small JSON response path. Dependency tarballs stay compressed.

The internal acquisition uses the reviewed Unix socket policy and one
controller-owned acquisition ID and private journal. It verifies the
reported image, creates an inert staging container from that exact image
ID, then reads the six paths by full container ID. Inspect must confirm
the same image and ownership, no mounts and the never-started state
before and after the reads. The verifier requires confirmed exact-ID absence
after unforced cleanup. Journaled uncertain operations are reconciled without
replaying a create, copy or removal. This evidence relies on the pinned
local daemon and private controller journal.

The offline verifier accepts six local archives named `node.tar`,
`npm.tar`, `entrypoint.tar`, `commit.tar`, `source.tar` and `store.tar`.
It can verify matching bytes and reports their image origin as unverified.
The in-process protocol tests carry the same unverified-origin boundary.
Runtime closure, offline installation, vulnerability checks, isolation
and execution authority require separate evidence. The public readiness
command still blocks execution.

### Platform store and private build

The private build accepts the separately versioned
`npm-v3-platform-tarballs-v1` index. Its exact fields are `kind`,
`lockSha256`, `platform`, `included` and `excluded`. It binds the complete
committed lock hash even when the store holds only some tarballs.
`platform` has the keys `os`, `cpu` and `libc`. Version 1 supports Linux
glibc with `arm64` or `x64`.

- `included` maps every applicable non-root lock locator to its exact
  single SHA-512 `integrity` and blob `sha256`. The image must contain the
  complete applicable set, including applicable optional native packages.
- `excluded` maps each non-applicable optional locator to its nonmatching
  axes in `os`, `cpu`, `libc` order. A mismatch on a required entry refuses
  the whole store. The verifier computes the partition from the pinned
  lock rather than trusting omission claims in the index.
- npm 10.9.9 can also prune dependencies of an off-platform optional
  package. Preparation and readback refuse graphs where the v1 selector
  partition cannot prove the same included set. Off-platform locators need
  a matching optional referrer at their locked location and no possible
  required or peer referrer. They cannot have nested, optional or peer
  children. A regular child must resolve to one top-level locator
  and have a separately provable required path from the root through
  exact or caret versions. Root development declarations that overlap
  other scopes refuse. A child used only by the off-platform branch requires
  a different preparation route.
- The selector subset follows `checkList` in
  `npm-install-checks/lib/index.js`. It accepts a single selector, a list
  of positive and negative selectors, or the sole literal `any`. Empty,
  ambiguous, duplicate and malformed selectors, links, workspaces, bundled
  entries and unsupported integrity or source formats refuse.
- The index and exact unique blob inventory use the same canonical JSON
  and digest-addressed layout as the full store. Reported platform
  coverage includes the included, excluded and full-lock counts.
  `fullLockVerified` is false for this contract. The existing
  `npm-v3-tarballs-v1` contract still requires every lock entry.
  Existing v1 platform indexes remain usable when the pinned lock passes
  these checks and recomputes the same partition. A previously accepted
  graph outside this subset is refused even if its stored index is pinned.

The platform readback variant measures
`/opt/aim-ci/npm_platform_build.cjs` against the helper bytes in the installed
controller manifest. A private controller caller supplies the pinned plan
and a separately pinned trusted image readback receipt to the existing
Docker job. The receipt binds the source commit and tree, runtime and store
pins, image ID, reviewed daemon policy, helper, and inert container cleanup.
Synthetic observations and caller-supplied archives alone cannot supply this
receipt. The operator's dispatch authority remains a separate prerequisite.

The job checks the image OS and CPU and starts only the pinned Node and
helper. The helper checks the running OS, CPU, glibc, nonroot identity,
Node/npm executable hashes and selected npm internals. The first supported
runtime is Node 22.23.3, npm 10.9.9, cacache 19.0.1 and pacote 19.0.2.
Both string `"2"` and numeric `2` represent cacache content format 2.
Versions and image declarations are distinct from checked bytes and cache
behavior. For each unique applicable tarball, the helper measures SHA-256
and SRI, seeds the disposable cacache through its stream API, then reads the
object back by SRI and rechecks its byte count and SHA-256.

The runner uses two fixed npm operations.

```text
npm ci --offline --ignore-scripts --no-audit --no-fund
npm --ignore-scripts run build
```

The source is copied from the verified image into a disposable workspace.
The helper refuses project npm config, dependency trees and environment
files in the input. A tracked `dist` is excluded so it cannot satisfy build
evidence. npm receives isolated home, temp and cache paths, with empty npm
config files and lifecycle hooks suppressed. The helper verifies the exact
applicable installed package inventory before building and verifies the
package and lock hashes again after the build. If npm silently omits an
applicable optional package or its resulting layout differs, the job fails.
The selected build script is executable committed input and needs review.

The private build intentionally permits execution from its disposable
`/work` tmpfs so npm can invoke installed tools. It requests `exec` with
`nosuid` and `nodev` and checks the effective mount before installation.
The report retains the actual mount ID, type, per-mount and super options,
plus the resolved `tsc` and `vite` paths, link targets, file modes and
execute access when those tools are present. The root filesystem stays
read-only and the job runs as a nonroot user without host mounts or
published ports. Executable workspace access is part of the reviewed
build and supplies no general hostile-code isolation claim.

The runner collects fresh `dist` bytes within the reviewed artifact policy.
Ordinary ASCII names with internal spaces, such as font files, are accepted.
Traversal, links, special files, duplicate paths, out-of-bound output and
missing required files refuse. The Linux runner anchors artifact reads to
open directories. Since the job uses a tmpfs workspace, it emits a bounded
report over its configured stdout log stream. The controller binds the
terminal report to the effect and source, verifies each artifact's decoded
size and digest, and keeps the complete bounded report in its private
journal before exact container cleanup. A malformed complete report retains
its raw bytes and pin as unverified evidence. Missing or incomplete output
cannot supply a pass. The job retains failed operation exits and bounded
stdout/stderr when reported. These are trusted-controller observations of a
reviewed image and source, with no claim of attestation against a hostile
runtime. They establish neither a release nor product acceptance.

### Loopback contract

The current preparation requests loopback **disabled** and treats that
control as unqualified on the real Docker path. Docker's `network=none`
isolates external network access and still permits communication over
container-internal `127.0.0.1`. A browser test that starts a server on
`127.0.0.1` therefore needs an explicit loopback decision. Host port
publication is a separate control. The private build requests no published
ports. The Mac/VM DNS listener on TCP/UDP 53 is a host/VM lifecycle observation,
separate from the container's loopback and external egress policy.

The smallest next reviewed policy choice is a versioned capability for
container-internal loopback only. Keep the default disabled. Require an
explicit recipe/runtime approval for the literal internal bind and port
before enabling the capability for web tests. Verify on the actual backend
that the approved internal client can reach the server, external egress
remains unavailable, the host cannot reach the test listener, no ports are
published, and the environment returns to its initial listener state. A
denial, unexpected listener, or unmeasured control must still block
qualification. This change introduces no such approval and keeps public
execution blocked.

## Run one reviewed build as a local operator

The installed controller can run one reviewed, preloaded npm/Vite image on a
trusted local Docker daemon. Start with the schema v2 `plan-recipe` output
and its separate planning review, approval and container profile. The
installed preparation command can produce these inputs and the local image
from reviewed inputs. See the preparation workflow. [Onboarding](aim-ci-onboarding.md#prepare-a-reviewed-npmvite-build) The
preloaded image must already contain the complete committed source, the
installed helper and a reviewed platform dependency store. Preparing or
fetching an image and its store is a separate operator task.

Keep the configuration, plan, container profile and Docker policy outside
the checkout, controller installation and state directory. Use canonical
absolute paths and files owned by the invoking OS user that other users
cannot write. The version 1 configuration has this shape.

```json
{
  "version": 1,
  "manifestSha256": "<installed manifest SHA-256>",
  "plan": {"path": "/absolute/reviewed/plan.json", "sha256": "<file SHA-256>"},
  "containerProfile": {"path": "/absolute/reviewed/profile.json", "sha256": "<file SHA-256>"},
  "dockerPolicy": {"path": "/absolute/reviewed/policy.json", "sha256": "<canonical file SHA-256>"},
  "platform": {"os": "linux", "cpu": "arm64", "libc": "glibc"},
  "stateDir": "/absolute/private/new-build"
}
```

The Docker policy is compact sorted JSON without a trailing newline. It binds
the reviewed Unix endpoint, image, adapter, runtime and installed manifest.
The first approved invocation creates the new private state directory under a
protected parent owned by the invoking OS user.

Preview the effect before consenting. Keep the returned `intentSha256`.

```sh
python3 -I "$controller/aim-ci-control.py" run-reviewed-build --preview \
  --manifest-sha256 "$manifest_sha256" --config "$config" --config-sha256 "$config_sha256"

python3 -I "$controller/aim-ci-control.py" run-reviewed-build \
  --manifest-sha256 "$manifest_sha256" --config "$config" --config-sha256 "$config_sha256" \
  --authorize-intent "$intent_sha256"
```

The preview creates no state and never contacts Docker. The explicit
invocation records the local OS principal's consent to the exact intent,
including image readback, the fixed offline install and build, the
executable disposable workspace, and exact-owned cleanup. The command
rechecks inputs before creating or starting a container. A pin identifies
bytes. The consent record establishes local process control and a choice of
effect. Processes with the same OS credentials share that authority.
It establishes neither per-person authentication nor repository ownership,
hosted authority, or general hostile-code isolation. The generic
`check-recipe-execution` remains read-only and blocked before dispatch.

Use status and resume on the original effect. Status reads only retained
state and uses the original intent even if the planning files have changed.
It displays the historical result. Resume rechecks the evidence.

```sh
python3 -I "$controller/aim-ci-control.py" status-reviewed-build \
  --manifest-sha256 "$manifest_sha256" --state "$state" --intent-sha256 "$intent_sha256"

python3 -I "$controller/aim-ci-control.py" run-reviewed-build --resume \
  --manifest-sha256 "$manifest_sha256" --config "$config" --config-sha256 "$config_sha256"
```

Resume uses the original journal and deadline. A pending or unknown dispatch
is observed using the matching identity. The command reports new, retained
and reconciled journal operations separately. An outcome that cannot be
reconciled remains unknown. A passing build in either mode requires an
observed container finish time before that original deadline, even when
the container exits successfully after an interruption. This check
leaves the evidence journal identities and report versions intact.
Resume refuses a previously recorded pass if its retained evidence no
longer verifies. It preserves the old receipt for inspection and does
not start another container. Keep the private state. An interrupted
controller has no independent watchdog, so promptly inspect and resume
or clean up the original effect.

### Run a reviewed named offline test

Add `--with-offline-test` to the build preview, exact authorization
and any resume for the same configuration. Use the same flag for
configuration-based status. State-based status and cleanup use the
original intent SHA-256 and need no flag. The build configuration
keeps its version 1 format. The flag adds the exact test operation
and its prerequisites to the preview, so a build-only intent cannot
authorize the test. A new effect needs an unused state directory. The
script comes from the reviewed plan's committed `package.json` selection.

Review the test's code, effects and prerequisites before consenting.
It must work with the pinned Node/npm and installed dependencies in the
same disposable workspace as the build. It must leave build inputs
intact and the artifact directory absent. Browser binaries, loopback
listeners, other services, external network and extra runtime setup
are outside this mode. The declaration records the operator's review
of these prerequisites. The backend still permits internal loopback
under Docker network mode `none`. The runner uses the same runtime
controls and one journaled container, with no new capability granted.

After verifying offline installation, the helper invokes the literal
script via `npm --ignore-scripts run <script>`. It records the exit,
signal or error and bounded stdout and stderr. Failure stops before
build. On success it rechecks the package and lock pins and the absence
of `dist` before building. The controller verifies the version 2 report
against the exact job, script, source and terminal outcome, including the
original deadline. It retains the report and exposes the named test result
and log hashes under `platformBuild`. Exit 0 requires
`build-and-test-passed` and confirmed cleanup. Missing or inconsistent
evidence produces an unverified result. Resume reconciles the same
journal and never starts a second job for an unknown outcome. Build-only
keeps the version 1 report and `build-passed` status.

The helper, report validation and journal path have offline test coverage
with simulated process and transport responses. This covers the contract
and control flow. Real backend qualification for this mode and support for
browser tests or the static-heading oracle are separate. Test exit 0
reports the script outcome. Product acceptance is a separate owner decision.

### Plan an exact Playwright selection

A reviewed npm/Vite schema v2 recipe can add a `testSelector` to the
existing `testScript`. The optional field is also accepted in the
preparation recipe and in the separately reviewed candidate validation
profile. For example:

```json
{
  "kind": "playwright-names-v1",
  "file": "tests/receipt.spec.ts",
  "project": "chromium",
  "suite": "receipt component",
  "names": ["opens receipt", "closes receipt"]
}
```

This version supports a single spec directly under `tests/` and a
single named suite with up to 16 unique, literal alphanumeric titles
with spaces. It requires the committed `playwright.config.ts`, the
exact script body `playwright test`, and npm v3 lock entries for
Playwright 1.59.1. The spec and configuration are outside the recipe's
editable paths. The trusted image readback verifies their bytes against
the pinned commit and records their digests. A change to the selector
or either command requires a new intent.

The preview records the fixed discovery and test arguments. Discovery
loads the project's configuration and spec as code. A mismatching
discovery report stops the helper before test execution. The test command
disables snapshot updates, retries and dependent projects. A version 3
report retains both commands' process outcomes and bounded logs. A passing
report requires exactly the selected project, file, suite and tests,
with one passing execution for each. The controller checks the raw
reports again when verifying retained evidence for technical review.

#### Bind a browser inventory to the selected recipe

A container profile can add `browserRuntime`. When preparing a derived
image, declare the same object under `base.browserRuntime`. Preparation
retains the declaration in the derived image's profile and pins it in
the plan and Docker policy. A plan with this inventory requires a
reviewed Playwright selector. A change to the inventory or network
request changes the runtime identity and the operator intent. Profiles
without this object keep their original identities.

The `playwright-chromium-inventory-v1` object requires these fields:

- `platform` specifies Linux, glibc and either arm64 or x64. The daemon
  readback checks the image OS and architecture. The platform also has
  to agree with the measured npm store.
- `revision` is `"1217"` for the supported Playwright release.
  `executable` pins the managed Chromium headless shell by `path`,
  `sha256`, `size` in bytes and numeric `mode`. The path is
  `/opt/aim-ci-browser/chromium_headless_shell-1217/chrome-linux/headless_shell`
  on arm64 or
  `/opt/aim-ci-browser/chromium_headless_shell-1217/chrome-headless-shell-linux64/chrome-headless-shell`
  on x64.
- `libraries` maps one to eight literal, regular shared-library paths
  under `/usr/lib` to their `sha256`, `size` and `mode`. Use canonical
  paths in the image. The archive reader refuses links and unexpected
  members. The helper also requires each runtime path to resolve to itself.
- `network` is the exact request
  `{"mode":"none","loopback":"container-only","externalEgress":"deny","publishedPorts":[],"hostMounts":[],"hostOrVmDns":"not-requested"}`.
  This describes the requested boundary. The daemon's configuration
  and absence of explicit DNS overrides are not observed connectivity.

The controller reads the listed members from the exact never-started
image container. It checks size, mode, digest and the ELF64 byte order,
type and CPU header. A local archive can demonstrate matching bytes.
Only the maintained trusted-local-daemon acquisition, including its
original create, measure and cleanup receipts, supplies image provenance
to the operator path. The byte evidence covers the listed files.
Dependency closure, ABI compatibility, actual browser choice and
runtime behavior remain unqualified.

The selected operator preview shows the expected pins and requested
network policy separately from observed evidence. The helper contract
remeasures the listed files before project commands. For an inventory-
bound selected run it records those measurements in report version 4,
and the controller verifies them again for technical review. Build-only
continues to use its original runner arguments and report version.

Selected test authorization still stops before contacting Docker. Before
enabling it, qualify the same image and controls under the job's non-root
identity. An independent probe must exercise the intended Chromium
executable and a listener on container-local loopback, and establish the
absence of external egress, published ports and host mounts. Retain those
observations separately from image readback. Product acceptance remains
an owner decision.

### Clean up the original build effect

Withdraw dispatch authority and request cleanup of the recorded resources
with the original state and intent. Cleanup uses the original policy and
identities and cannot create or start containers. It still works when the
original planning files have changed. An active controller sees the
withdrawal before its next advance. While it holds the lock, cleanup
reports that withdrawal was recorded and cleanup is pending. Repeat the
same cleanup command after the active controller exits. An unknown cleanup
remains explicit.

```sh
python3 -I "$controller/aim-ci-control.py" cleanup-reviewed-build \
  --manifest-sha256 "$manifest_sha256" --state "$state" --intent-sha256 "$intent_sha256"
```

This command adds a local interface to the reviewed build. Adopter migration,
operator and repository policy for real adoption, loopback and browser
qualification, and hosted effects remain separate decisions.
