# Project-local validation handoffs

This beta.9 planning route consumes an independently reviewed, pinned onboarding instance and a separate project-local declaration. It produces actual named npm command handoffs without translating model prose into shell commands, changing shared recipes, invoking a provider or executing the project.

`plan-project-validation` and `verify-project-validation` require:

- Current `--manifest-sha256`.
- Exact `--instance` and `--instance-sha256`.
- Trusted original `--instance-controller` and `--instance-controller-manifest-sha256`.
- Explicit canonical `--python` and externally verified `--python-sha256` for the old verifier.
- Separate reviewed `--declaration` and `--declaration-sha256`.
- A new `--output` for planning, or `--handoff` and `--handoff-sha256` for verification.

Keep outputs private and outside both controller installations and the source checkout. Never use an arbitrary supplied hash as a substitute for reviewing the controller and declaration.

## Preserve the old assessment

An older controller has a different package inventory. The new controller does not weaken its normal package verifier or rewrite an old assessment to make it fit. Instead, it verifies every file in the explicitly pinned predecessor installation, rejects all unmanifested files/directories and links (including stale bytecode), and invokes only its fixed `verify-project-onboarding` command under isolated pinned Python and a clean environment. The old manifest pin must match the instance's original packet. The complete returned verification contract and before/after pins must agree. The predecessor's code is not imported into the current interpreter.

This bridge executes trusted controller inspection code and pinned local Git, never npm or project scripts. It does not provide an adversarial filesystem sandbox. A malicious controller is not made trustworthy by supplying its hash. If an installation contains unmanifested bytecode or other additions, reinstall its independently verified original archive in a new private directory. Preserve the original evidence rather than ignoring additions or silently deleting them.

## Declaration

The exact top-level fields are `format` (`aim-flow-project-validation-declaration`), integer `schemaVersion: 1`, `instanceSha256`, unchanged `target`, `review`, `sourcePaths`, `packageRoots`, `runtimes`, `jobs` and `excludedCoverage`.

- `review` has `reference`, `qualifications` and `openItems`. These are planning review records, not execution grants. Keep corrections separate from the unchanged model answer. Every original model unknown is also retained automatically.
- `sourcePaths` selects distinct committed regular UTF-8 files. The planner uses pinned-commit `git ls-tree` and object reads, never the index or dirty working copy.
- Each `packageRoots` item has `directory` (`.` or repository-relative) and `lockfile`. Select that root's `package.json` and its actual `package-lock.json` or `npm-shrinkwrap.json`. Root and nested locks remain distinct. Full script bodies, engine declarations and lock identities are retained, without claiming dependency resolution or runtime compatibility.
- Each runtime has `id`, nonempty `requirements` and `sources`. Requirements are explicit review facts, not executable tool pins or compatibility proof.
- Each job has `id`, `mode`, `runtime`, `needs`, `stages`, `coverage`, nonempty `effects` and `prerequisites`, and `sources`. Modes are `local-sequence`, `component-only` or `hosted-reference`. Dependencies name earlier jobs; jobs without dependencies are not implicitly serialized. Stages inside a job are ordered.
- A local stage has `root`, `operation` and `script`. Operation `ci` requires `script: null` and yields `npm ci`. Operation `run` names a committed script and yields `npm run NAME`. No extra flags, shell interpolation or assessment command strings are accepted. The planner lists declared root install hooks in npm lifecycle order and normal pre/script/post definitions; dependency and implicit/default hook behavior remains explicitly unexpanded. It never injects `--ignore-scripts`. Script bodies, including nested chains, remain source data rather than a guessed executable graph.
- Hosted-reference jobs have no local stages. Their quoted workflow remains evidence of actions, OS/runtime, conditions and job behavior, not a local substitute for GitHub Actions.
- A source reference has exactly `path`, `startLine` and `endLine`. Flow fills its verified hash and exact quote from the selected committed file.
- `excludedCoverage` states what the handoff does not cover. It must not be empty.

## AIWebSite adaptation boundary

The project-local declaration can select root `npm ci` followed by root `npm run ci:release-readiness` under a reviewed Node 24 local runtime. It keeps the actual aggregate body: frontend build/package/Windows-contract/release-process checks, followed by the nested API install and test runner. The build's `prebuild` ZIP generation stays visible. This is not the same environment or graph as the independent frontend Node 22.12.0, API Node 24 and Windows Actions jobs. Main-push-only artifact assembly/upload and production deployment remain separate.

A `component-only` job can select the real `test:release-process` script without claiming that installation, Astro build, backend, Windows or hosted CI passed. Its imported fixtures, Git/which availability, transport stubs, native fetch reachability, filesystem/network containment and runtime differences still need separate review. The validation handoff does not invoke any runner. Existing npm/Vite and source-check executor guarantees remain unchanged.

All handoffs remain `ready: false` and `executionAuthorized: false`. Each stage is `not-run`; hosted jobs are `reference-only`. Per-job scope and prerequisites remain visible even when a separate operator has observed one component. A passing component must not upgrade other jobs or erase unresolved model questions.

## Reviewed candidate commits (development after beta.9)

Declaration schema 2 adds `candidateReview` and lets `target` name a separate,
canonical checkout at a reviewed descendant commit. `candidateReview` has exactly
`reference`, nonempty `qualifications` and the complete distinct `changedPaths`
list. Keep the original instance, its controller and original checkout at their
old commit. The original verifier still checks those unchanged bindings. No
model call, proposal application or Git mutation happens in this bridge.

The planner verifies the candidate's exact commit/tree and ancestry, compares
the complete recursive base/candidate trees, and binds each changed path's
before/after object, content hash, mode and byte count. Additions, deletions and
executable-mode changes count. Touched links and submodules are refused. Every
original onboarding source and every validation-definition source must retain
its original object and mode. If those definitions change, prepare a new reviewed
assessment instead of quietly transferring its conclusions. Schema 1 remains
unchanged and cannot retarget its original instance.

The candidate review must inspect the whole diff and any new configuration,
imports, lifecycle effects or dependencies. Unchanged selected evidence is not
proof that all behavior is unchanged. The original model did not assess this
candidate. Review qualifications and unresolved questions remain visible; a
review-reference string is not proof of independent review or user permission.

Both existing execution adapters use this derived candidate target and include
the candidate binding in the exact preview/intent. Their source selection must
include every changed file still present in the candidate. This prevents a
baseline-only or partial-source run from claiming to validate the whole diff.
Deleted files are bound as absent. Dirty and untracked files remain excluded.
State/output must stay outside both checkouts, and the original instance is never
rewritten. Existing runtime qualification, containment, exact authorization,
single-attempt journals and receipt verification still apply.

This connects a separately reviewed candidate to project-local named npm jobs.
It does not apply an API proposal, start hosted CI, merge a PR, retain generated
container artifacts, run an undeclared browser check or authorize deployment.

## Explicit offline execution

`run-project-validation` now has a separate execution boundary. Supply the same pinned instance/controller/declaration/Python arguments as the planner, plus `--execution-spec`, `--execution-spec-sha256` and a new canonical private `--state` outside the controllers and checkout. `--preview` returns the complete intent and digest without executing project code. Only after independently reviewing and authorizing that concrete operation, use `--authorize-intent DIGEST` with unchanged inputs. A digest is an exact-change check, not evidence that a person granted permission.

The executor rederives the handoff and selects one explicitly named local job with no unresolved job dependencies. Its ordered npm argv comes only from committed named scripts in that handoff. It does not execute model command text, infer authorization from an assessment, or change the original draft's readiness. It preserves ordinary npm pre/post and install hooks. All networking is denied, npm uses offline mode, and no credentials or operator environment are inherited.

The execution specification has exactly:

- `format: "aim-flow-project-execution-spec"`, `schemaVersion: 1`, and `job`.
- `sourcePaths`: explicit committed regular files at the same pinned commit. Binary assets are supported. Sources need not be limited to the earlier analysis excerpts, but their expanded closure and effects require separate review. Dirty files, links and submodules are not copied. Required stage manifests and install locks must be selected.
- `tools`: canonical path and SHA-256 for `node`, direct `git`, npm CLI (`npm`) and `/usr/bin/sandbox-exec` (`sandbox`). `npmTree` pins the entire npm installation directory and its content/mode/link inventory, not just its entry point. `utilities` optionally pins additional named executables such as zip/unzip on the private PATH.
- `npmCache`: null or a canonical path/hash for an independently reviewed regular-file npm cache seed. Its directory contents are copied into the private npm cache. No installation happens during preview. Cache availability alone does not establish successful installation.
- Nonempty `runtimeReview` and `sourceReview` lists. Record actual runtime differences, configuration, transitive inputs, lifecycle effects and exclusions. The adapter refuses committed `.npmrc` at declared package roots/ancestors rather than silently omitting it. Selected `.npmrc` files are also refused pending separate configuration-adapter review.
- `writablePaths`: exact new project-relative output directory subtrees. `writableFiles`: exact generated file paths. Neither may overlap selected immutable sources or another output scope. Generated archives may be omitted from inputs only when the reviewed project lifecycle recreates them before use. Do not broaden a parent directory to evade a denied write.
- `sourceWriteExceptions`: exact selected source files with separately reviewed temporary writes to that exact file in the private copy. The exception permits temporary data, metadata and create/delete/replace operations at that exact copied path; it does not promise atomic immutability. Final regular single-link bytes and executable mode must match the original source after every stage. This is broader than timestamp-only access: the narrower operation profile did not qualify macOS utimes. The preview discloses the exception; never claim these files are data-write-protected. No original-checkout permission is granted.
- `applicationEnvironment`: explicitly reviewed application settings, such as disabling a framework's telemetry. Runtime/configuration overrides including PATH, HOME, Node/npm/Git settings and loader injection are refused. Do not put credentials in this specification.
- Positive `timeoutSeconds` and `outputBytes`: per-stage wall-clock and combined stdout/stderr limits. Qualification is limited to the smaller of 30 seconds and the stage limit. Total stage budget is the sum across the selected job; the adapter does not advertise a separate whole-job deadline.

### Qualified process boundary

The current adapter is macOS-only. It generates a process-local sandbox profile with no network access, exact `/dev/null` data I/O, private scratch/cache writes and the declared output/exact-source scopes. It changes no persistent OS setting. Qualification checks both parent and descendant network denial and protected-write denial, exact Git selection, null I/O and scratch Git init/config/commit. Exact source exceptions are exercised on the private copy and restored during qualification; their original-checkout counterparts and unapproved sibling paths are also probed for denial. Qualification failure prevents every project stage, and never triggers a weaker automatic retry.

Selected source remains data-write-protected except the explicit restored-file exceptions. The profile, evidence, runtime shims, original checkout and controller remain protected from project writes. Source/tool/npm-tree identities are checked around stages. The environment has private HOME, TMPDIR, npm cache/config and Git config/hooks/templates. No inherited NODE_OPTIONS, proxy, credentials or user Git/npm config is used. Dynamic OS libraries and ambient system utilities are not a fully pinned hermetic OS. Read access is not comprehensively sandboxed. Apple's sandbox-exec is deprecated. This is qualified local containment for reviewed code, not a portable hostile-code execution guarantee.

### Evidence and failure handling

The attempt directory is created exclusively. An effect journal records intent before qualification or project commands, and each stage has a started record before launch. Any existing attempt refuses redispatch; interrupted/unknown attempts are retained and never silently retried. Exceptions retain a diagnostic without establishing success or permission to retry.

A completed `result.json` records process outcomes, logical and resolved argv, per-stage logs and hashes, qualification, protected-control hashes, source integrity and before/after output inventories. Inventories do not follow symlinks, and include declared output bytes plus private work residue. Failure, timeout, output overflow or integrity drift prevents later stages. Process groups are killed after every stage, even if the leader exited first. A failed job returns CLI status 3 and preserves its receipt.

`verify-project-validation-run` uses the same inputs, state and externally retained `--receipt-sha256`. It verifies journal/intent/log/control bindings and rechecks retained source bytes/modes and compares currently retained output bytes with the recorded inventory, without rerunning the project. Missing or changed state is a verification failure, not an instruction to reset it.

A successful process status does not independently establish test assertions, product acceptance, hosted CI, Windows coverage or release readiness. Inspect the raw output and review its meaning separately. The original handoff remains unapproved. Production deployment and external communication are outside this adapter; a successful component does not authorize them or upgrade other jobs.

## Linux container backend jobs

`run-project-container-validation --preview` derives one exact npm job from the
same source-bound instance and declaration. An explicit `--authorize-intent`
grants one fresh attempt. `verify-project-container-validation` verifies the
retained receipt without dispatching another job. It never starts Colima or
pulls an image: those are separate operator actions.

The separate `aim-flow-project-container-spec` schema pins an already-local
Linux Node image ID/repository digest, observed Node/npm versions, the local
Docker executable/Unix socket, selected committed sources, content-only npm
cache and explicit source/runtime reviews. Job commands still come from the
validated committed manifests, never model prose. The normal npm lifecycle is
preserved with offline/audit/fund/update settings and a clean environment.

The controller creates a never-started staging container, uploads a retained
regular-file tar archive and commits one local derived image. A fixed helper
runs as nonroot PID 1 with inspector signal activation disabled, read-only
rootfs, network:none, no host mounts/published ports/devices, no capabilities,
no-new-privileges, no restart/healthcheck and explicit memory/process/tmpfs/time/
output limits. The helper checks its exact baseline inputs and parent/child
containment before invoking any project stage. Unknown effects retain evidence
and refuse replay. Derived images remain local for review; completed job
containers are removed only by exact owned identity.

The network namespace has private loopback for HTTP fixtures and no external
route. This is not the earlier macOS loopback policy. In schema v1, `/work` is noexec: explicit
image Node invocations work, but native binaries and shebang executables in
node_modules/.bin do not. This route is therefore not general frontend npm
coverage. Existing macOS execution remains separate and unchanged.

Original checkout and image baseline are read-only to the job. The private
working copy is mutable during stages; source bytes/modes must be restored
before/after stages. This is not atomic working-source immutability. The helper
sweeps remaining same-UID processes in its private PID namespace before final
inventories. Selected project code is reviewed, not treated as hostile:
same-UID helper/project execution is not adversarial evidence trust separation.

The bounded JSON report captures runtime, stage stdout/stderr/exit, source
checks and output inventories before tmpfs disappears. It is reconciled with
Docker's observed terminal status and retained control/effect hashes. Files
created in tmpfs are not themselves preserved. A pass remains ready:false,
acceptance:not-established and deployment:not-authorized. Hosted CI, Windows,
browser acceptance and release publication are separate work.

### Explicit aggregate runtime extension (schema v2)

Schema v1 keeps its noexec contract. V2 adds an explicit `workspaceExecution`
choice (`noexec` or `exec`), an `inventoryBytes` limit no larger than the private
workspace quota, and an optional `preparedImage`. Selecting exec changes only
the newly created private tmpfs. Network:none, no host mounts or published ports,
nonroot PID1, read-only image root, dropped capabilities and no-new-privileges
remain required. Parent and child directly execute a copied, hash-checked ELF
and generated shebang nonce stub. Exec must succeed; noexec must fail with EACCES.

A prepared image is a local derivative, not a registry digest. Its specification
pins the official Node base separately, the exact derivative ID, a reviewed
preparation record and utility paths/hashes. The controller verifies recorded
base-layer ancestry and inert image defaults; the helper checks actual Node/npm
and utility bytes before project dispatch and after each stage. The preparation
record must retain identical complete npm tree and Node fingerprints before/after
preparation; the helper repeats the complete runtime fingerprint comparison. Layer ancestry alone is
not proof that an overlay is safe. Utility package acquisition, repository
signature checks and image preparation are separate authorized work, never an
automatic fallback in this runner.

V2 streams constructed PAX staging archives and checks actual size up to 512 MiB.
The helper's explicit inventory bound can cover a full source/cache/install/build
workspace up to the existing 2 GiB tmpfs quota. Its entry count and JSON report
remain bounded. V1 keeps its 256 MiB staging and 512 MiB inventory ceilings.
Normal npm lifecycle settings stay intact; v2 uses informational npm logs to
retain lifecycle dispatch/exit evidence without changing script authorization.
A successful aggregate is still local validation, not hosted CI, acceptance,
artifact publication or deployment authority.


## Platform and upgrade boundaries

The macOS adapter requires the platform's sandbox-exec support and separately
pinned Node/npm/Git/utilities. The Linux container adapter requires a separately
available Docker Unix socket, an already-local official Node image, and any
separately reviewed prepared image/cache. Installing Flow does not install or
start Docker/Colima, pull images, download packages or configure security policy.
The live aggregate qualification used Linux ARM64 in Colima on macOS; other
hosts, Linux amd64, Windows, and hosted Actions remain separate qualifications.
The Node Windows-contract fixture is not native Windows/PowerShell execution.

Use this release's matching bootstrap and all independent pins for its exact
61-file schema-3 archive. Beta.8's 53-file schema-3 archive needs the beta.8
bootstrap. Preserve older installations and retained instances; the separate
pinned predecessor-controller verifier validates their own onboarding protocol.
An upgrade never turns an earlier preview into execution authorization.

## Derive a plan from onboarding (development)

`derive-project-validation` removes the need to author every named npm stage by
hand. It takes the same pinned instance, predecessor controller and Python
arguments as planning, plus `--resource-catalog`, `--resource-catalog-sha256` and
a new private `--output`. No declaration argument is required. It verifies the
old assessment, rereads its actual committed evidence and emits a derived
schema-1 declaration, resource needs, unchanged model unknowns and unsupported
steps. The original assessment and checkout are not modified.

Only complete literal `npm ci` / `npm run NAME` commands, optionally joined by
exact spaced ` && `, in installation/tests areas are accepted. Every named
script must exist in the working directory's committed package.json and a real
npm lock must exist. Normal lifecycle effects remain visible to the existing
handoff planner. Known `node/npm/git --version`, `zip -v` and literal `command -v TOOL ...`
probes become explicit resource needs, never shell dispatch. Other flags, shell
fragments or non-npm commands are not silently dropped. An unsupported predecessor remains a concrete unresolved need,
not an implicitly satisfied dependency. Flow neither invents installation stages
nor substitutes an aggregate for independent assessment steps. Unsupported
production/hosted/browser commands require separate adapters and authorization.

The catalog is `aim-flow-resource-catalog`, integer schemaVersion 1, with a
`resources` array. Entries have `id`, `kind` (`node-runtime`, `npm-cache` or
`browser-runtime`), `identity`, `requirements` (strings) and `qualification`.
Optional `qualificationState` is `unknown`, `prepared` or `qualified`. A matching
prepared resource is reported as `catalog-candidate-qualification-required`,
not missing or already qualified. Even qualified claims still require review
and current execution-time checks. An optional `executionSpec` is null or `{backend: "container" | "local", path,
sha256}` with a canonical absolute path and an exact digest. These references
are retained as proposed resource bindings; derivation does not read/execute the
spec, provision anything, or claim the resource is currently available.

Optional typed `capabilities` carries `nodeVersion`, `npmVersion`, `platform`,
`architecture`, `lockSha256`, `gitVersion`, `utilities` (tool/identity mapping),
and/or `browserPackages` (package/version mapping).
Node candidates require measured three-part versions, platform and architecture.
Matching uses a fail-closed engine-range subset: numeric versions, comparisons,
space-separated conjunctions, caret and tilde. Unsupported unions, prereleases
and other syntax remain unresolved. Thus 24.22.2 satisfies `>=24 <25` without
catalog prose edits. Platform compatibility still requires separate qualification.
Cache matching requires the exact lock SHA-256. Browser package matching does not
prove browser binaries/revisions or sandbox behavior are qualified. Selected nested manifests
also expose their engine constraints, and declared Playwright/Puppeteer
packages expose browser needs. These conservative project-wide observations do
not prove transitive reachability. Script bodies, implicit hooks, utility needs,
network, writable scopes and nested behavior still require effect review.
Missing/unsupported resources stop automatic progression; catalog matches are
candidates for independently qualified binding, not proof of readiness.

The returned `declaration` can be persisted and passed to the existing planner
for exact quoted sources and full declared lifecycle handoffs. Its generated
review marker is explicitly unreviewed. An independently authorized Flow review
stage or operator can supply review evidence; no human-only policy is imposed
by this derivation. Neither model text nor a citation supplies execution consent.
The existing execution adapter still requires its own pinned specification,
source/effect/runtime review and exact intent authorization. A catalog spec's
job selection must be bound to the derived job ID; changed candidate sources
must use the existing reviewed-descendant bridge and complete changed-file
coverage. Never substitute a new commit into old source or authorization pins.

This increment derives a usable bounded command plan; it does not resolve every
assessment command, infer a complete shell execution graph or automatically
provision runtime resources. Missing qualification is deliberately not success.

### Optional retained test outputs

Schema 1 and 2 container specs optionally accept `artifactCapture`. Its exact
value is pinned by the execution-spec hash and intent authorization, then copied
into the helper job. Omission preserves previous intent/job/report shapes.
Higher-level planning must choose outputs from a source-cited test/generator
plan; the backend does not guess paths or promote model text to execution consent.

```
"artifactCapture": {
  "schemaVersion": 1,
  "files": [{"path": "test-results/check.json", "mediaType": "application/json"}],
  "maxFiles": 8,
  "maxFileBytes": 4194304,
  "maxTotalBytes": 8388608
}
```

All exact project-relative files are required. No directories, globs, workspace
export or source overlap is supported. Limits are 32 selected files, 4 MiB per
file and 8 MiB total; each declaration may lower them. Base64 worst-case size
plus 16 KiB metadata allowance must fit half of `outputBytes`, leaving explicit
headroom for logs and inventory. A larger inventory can still exceed the final
report limit: that produces `report-output-limit` failure, never a passing
report with quietly omitted artifacts. For the example maximum, use an output
budget of at least 22402392 bytes (within the existing 32000000 cap).

After process cleanup, source restoration and output inventory, the helper
captures regular single-link files with bounded reads before tmpfs disappears.
It refuses symlink paths, hardlinks, missing outputs and mismatched inventory.
Each ordered `report.artifacts` row contains `path`, `mediaType`, `bytes`,
`sha256`, and canonical `base64`. The bytes remain inside the private local
report and receipt, not exported to the source checkout, published, or sent to
any API. Failed commands can retain outputs if post-stage checks complete;
missing files include their exact path in the failure diagnostic.

The installed verifier rechecks count, selection, bounds, base64, content hash,
media type and output-inventory agreement, including present artifacts on failed
runs. Supported media types are strict UTF-8 JSON (duplicate keys and non-JSON
constants rejected) and PNG (signature, IHDR prefix and IEND trailer checked).
PNG checks establish format markers, not decoding, visual correctness or
acceptance. Independent acceptance review must inspect the actual retained
bytes and the original requirement separately. `ready:false`, acceptance and
deployment boundaries remain unchanged. Old attempts retain their old verifier
and unknown execution outcomes remain non-retryable.

Offline verification for this increment: 17 Python container tests passed,
including an actual Node-to-Python retained-bytes round trip; 26 Node helper
tests passed. The four artifact verifier tests were repeated after tightening
null-artifact rejection. Provenance and whitespace checks passed. Fixtures do
not start Docker, execute a customer project, call a provider or qualify an
actual browser runtime; no full suite or live acceptance is claimed here.
