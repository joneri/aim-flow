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
