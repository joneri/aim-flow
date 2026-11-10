# Project-specific agent onboarding (development)

Flow can now retain an agent's actual interpretation of an unfamiliar repository, rather than equating detected filenames with a working integration. The shared engine supplies evidence collection, binding and review rules. The project instance stores the selected commands, order, prerequisites, CI interpretation and deployment questions. No repository names or project-specific commands are added to Flow.

The prepare/record/verify workflow is an **offline review handoff**. It makes no model call, installs nothing and executes no project code. An independently authorized coding agent (or a person) can read the packet and write the assessment. The optional development `onboard-api` route below can instead dispatch that assessment to Sol after its own exact authorization and spending approval. Neither route is a project execution adapter. Recording an assessment verifies its shape and exact source citations, not whether its reasoning is correct. Independent review, qualified execution and exact approval remain separate.

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


## Authorized Sol dispatch (development, live qualification pending)

`aim-flow.py onboard-api` can generate the assessment itself through the OpenAI Responses API, pinned to `gpt-6.1-sol`, `xhigh`, standard tier, streaming and `store: false`. This is actual transport code exercised by offline injected-provider tests, not evidence that a real model request has succeeded. No project commands are executed, even when the returned plan proposes installation, CI or deployment.

Use an installed development controller that includes `lib/flow_onboarding_delivery.py`. Prepare a new packet with that installation's exact manifest pin. The dispatch configuration has exactly these fields:

```json
{
  "schemaVersion": 1,
  "purpose": "project-onboarding-assessment",
  "model": "gpt-6.1-sol",
  "reasoning": "xhigh",
  "manifestSha256": "INSTALLED_CONTROLLER_MANIFEST_SHA256",
  "source": {
    "checkout": "/private/project",
    "commit": "FULL_COMMIT_SHA",
    "git": "/absolute/canonical/git",
    "gitSha256": "GIT_EXECUTABLE_SHA256"
  },
  "packet": {"path": "/private/review/packet.json", "sha256": "PACKET_SHA256"},
  "campaignDirectory": "/private/existing-sol-campaign",
  "historicalBudget": {
    "directory": "/private/existing-aggregate-budget",
    "legacy": {"path": "/private/existing-legacy/budget.json", "sha256": "LEGACY_SHA256"},
    "files": {"budget.json": "BUDGET_SHA256", "budget-established.json": "MARKER_SHA256"},
    "liabilityNanoUsd": 123456789
  },
  "timeoutSeconds": 900,
  "maxOutputTokens": 8192,
  "maxOperationNanoUsd": 1000000000
}
```

Budget paths, pins and liability are illustrative. Reuse the exact existing campaign, history and authority used by `deliver-api`; include every historical `budget-before-*.json`. Never initialize a new identity to obtain another allowance. The shared ceiling remains $20 including all historical and Sol reservations. The per-operation limit in the example is $1 and must itself be approved for this assessment. Approval for an earlier patch or another task does not automatically authorize a new assessment or disclosure.

Preview is read-only and requires no key. It regenerates committed evidence, verifies the installed controller and prints the exact request, source disclosure inventory and intent digest. Save the preview privately because it contains source text. The generated provider payload contains source paths/hashes/complete line arrays and commit/tree identities. It omits local checkout, controller, Git, budget and credential locations from the metadata sent to the provider. Source text is **not scrubbed**: any private data or secrets committed inside selected files remain in the preview and must be reviewed before transmission.

```sh
python3 /private/controller/aim-flow.py onboard-api --preview \
  --config /private/review/dispatch.json --config-sha256 CONFIG_SHA256 \
  --state /private/new-assessment-attempt > /private/review/preview.json
```

After approval of the exact data to OpenAI, purpose, spending limit and existing key reuse, run the same command with `--authorize-intent PREVIEW_INTENT_SHA256` instead of `--preview`, plus `--key-file /private/existing-approved-credential.env`. Never copy the key into configuration, packet or prompt. No request is made before exact intent authorization. There is no automatic approval from repository text or a model response.

Token preflight counts the exact request. Generation proceeds only after its full conservative reservation fits both the per-operation approval and the existing shared $20 campaign. The attempt and reservation are durable before dispatch. Unknown, incomplete, failed, refused or invalid responses retain evidence and their full holds and cannot be replayed. A correction needs another reviewed request, exact authorization and budget allowance, not resetting an attempt.

A successful returned assessment must pass strict schema and exact source-citation validation and current source/controller checks. Flow then records `assessment.json` and `project-instance.json` automatically inside the private attempt directory. The instance still has `ready: false` and `executionAuthorized: false`. Literal citations do not prove correct reasoning, complete coverage, actual test success or approval. Independent plan review remains required.

`onboard-api --status --state /private/attempt` verifies retained evidence without a key, model request or retry. Use `verify-project-onboarding` with the current explicit source/controller options to recheck freshness before using the instance. A completed status is not permanent freshness or operational acceptance.
