# Project-specific agent onboarding (development)

Flow can now retain an agent's actual interpretation of an unfamiliar repository, rather than equating detected filenames with a working integration. The shared engine supplies evidence collection, binding and review rules. The project instance stores the selected commands, order, prerequisites, CI interpretation and deployment questions. No repository names or project-specific commands are added to Flow.

This is an **offline review handoff**, not an autonomous agent runner or a new execution adapter. It makes no model call, installs nothing and executes no project code. An independently authorized coding agent (or a person) reads the packet and writes the assessment. Recording an assessment verifies its shape and exact source citations, not whether its reasoning is correct. Independent review, qualified execution and exact approval remain separate.

## Workflow

Use a freshly built and installed development controller containing these commands, Python 3.11+, Git 2.54+, and independently retained controller/Git pins. Keep all outputs in a private directory outside the controller and project checkout.

1. Run `prepare-project-onboarding` with `--manifest-sha256`, `--checkout`, `--commit`, `--git`, `--git-sha256` and a new `--output` path. Add `--project-doc README.md` and repeat `--review-source` for relevant scripts, nested manifests, deployment definitions and other committed text. Existing discovery contributes root manifests and detected CI/container files automatically. The output receipt gives the packet SHA-256.
2. Give the packet to an authorized agent for analysis. Its `agentTask` explains the assignment and trust boundaries. Do not automatically send private source to an external service: model use and source transmission need their own authorization. Missing referenced files require another explicitly selected packet, not a guessed conclusion.
3. Write an assessment using the schema below. Hash the exact assessment file. Run `record-project-onboarding` with the same explicit controller, Git and target options, plus `--packet`, `--packet-sha256`, `--assessment`, `--assessment-sha256` and a new `--output`. It regenerates the evidence and refuses drift before retaining a project instance.
4. Run `verify-project-onboarding` with the same explicit target options plus `--instance` and `--instance-sha256` before using the draft. A new commit requires a new packet and review. Uncommitted working-tree files are neither read nor overwritten.
5. Independently review the proposed plan, resolve prerequisites and select a separately qualified execution contract. No current execution command consumes this instance as authority. `install-project` receipts and their original schema are unchanged.

Artifacts are written exclusively with private file permissions, reject links and never overwrite existing files. Retain the output pins separately. The packet contains complete selected source text, which may be private. This is not a secret detector or a hostile-code sandbox. Do not publish private project instances.

## Assessment schema

The top-level object has exactly these fields:

- `format`: `aim-flow-project-onboarding-assessment`
- `schemaVersion`: integer `1`
- `packetSha256`: SHA-256 of the exact packet file
- `target`: the packet's unchanged target object
- `summary`: nonempty project-specific explanation
- `areas`: exactly `installation`, `tests`, `ci` and `deployment`
- `steps`: ordered proposed command records, possibly empty when investigation is needed
- `decisions`: at least one reasoned, source-cited project-specific decision
- `unknowns`: a list of unresolved questions, or an empty list

Each area has exactly `status`, `analysis`, `sources` and `questions`. Status is `proposed`, `needs-investigation` or `not-applicable`. Both `proposed` and `not-applicable` require citations. `needs-investigation` requires a concrete question and can have no citations when the evidence is absent. Do not present an absence in selected evidence as proof that no configuration exists elsewhere.

Each source citation has exactly `path`, `sha256`, `startLine`, `endLine` and `quote`. Use a source in the packet, its SHA-256 and a nonempty exact span of complete lines, including original line endings. Line numbers start at one. The verifier rejects missing files, changed hashes, invalid spans and invented quotes. A valid quote does not prove that a claim follows from it.

Each step has exactly:

- `id`: unique lowercase identifier, starting with a letter and containing letters, numbers or hyphens
- `area`: one of the four area names
- `purpose`: what the proposed step would establish
- `command`: inert text describing the invocation, including shell semantics when needed
- `workingDirectory`: `.` or an exact repository-relative directory
- `dependsOn`: identifiers of earlier steps, without duplicates
- `effects`: nonempty list describing effects and risks, including uncertainty
- `prerequisites`: nonempty list describing toolchain, access, approval and environment requirements
- `independentChecks`: nonempty list of checks that would independently establish the intended result
- `sources`: exact citations supporting the proposal

Each decision has exactly `decision`, `reason` and `sources`. Explain why this repository needs that choice rather than a generic default. For example, a test using a running backend cannot be called a dependency-free source check; a workflow with a branch condition cannot be treated as an unconditional deployment route.

Step commands, effects and reasoning are unverified agent proposals. The engine checks ordering and citations, not command safety or semantic completeness. All instances remain `awaiting-independent-plan-review`, `ready: false` and `executionAuthorized: false`. Source-bound verification means only that selected committed evidence and the controller still match. It does not mean tests passed, deployments happened, or a reviewer approved the plan.

## Verification boundaries

The offline tests use committed Python and nested JavaScript fixtures, hostile instruction text and marker commands that must never run. They cover installed CLI round trips, literal citations, target/controller/Git drift, source selection, schema escalation, cycles, links, output overlap and overwrite refusal. They are not live model, build, browser, CI or deployment qualification.
