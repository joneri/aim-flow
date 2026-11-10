# Project-local validation handoffs

This unreleased route consumes an independently reviewed, pinned onboarding instance and a separate project-local declaration. It produces actual named npm command handoffs without translating model prose into shell commands, changing shared recipes, invoking a provider or executing the project.

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
