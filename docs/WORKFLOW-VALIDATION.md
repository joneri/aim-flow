# Candidate review and typed local validation continuation

New grants may select `findingDispositionPolicy: severity-and-impact-v1` in
`validate-work`, `resume-candidate` or `repair-and-validate`, or use
`run --finding-disposition-policy severity-and-impact-v1` (including intake).
The exact intent, fresh source packet and strict response schema bind this explicit policy. Every
finding keeps severity, exact source citations, a short title and a causal impact
rationale. Demonstrated requirement failures, unreliable required evidence and
material delivery risks block. A real failure still blocks at low severity.
Medium/low `follow-up` findings do not block testing or delivery; critical/high
findings cannot be disguised as follow-ups. Review instructions distinguish
preexisting collateral improvements and documentation polish from actual feature
failures and forbid inflating generic tested/reviewed criteria into blockers.

Follow-ups remain in the source receipt and are carried through executed review
and delivery reporting. Missing required evidence, failed stages, workspace
integrity, unresolved acceptance and external delivery gates remain mandatory.
Historical packets without this policy keep their original strict contract; no
completed or rejected review is rewritten or silently reclassified.

`aim-flow.py validate-work --config /absolute/phase.json --config-sha256 SHA --state /absolute/new-state` previews a new explicit phase grant. `--authorize-intent SHA` grants this continuation only. An original `work` v1 grant and its two API receipts never authorize extra review calls or project execution.

The schema-1 config contains `manifestSha256`, `workflowConfig` (path/SHA-256), `workflowState`, `python` (path/SHA-256), `reviewMaxOutputTokens` and `reviewMaxOperationNanoUsd`. It does not contain operator-authored commands, declarations or per-stage specs. By default the completed workflow must come from the same installed controller, preserving existing grant semantics. An explicit `producerController` object (`path`, `manifestSha256`) permits continuation from a different immutable installed Flow producer. Its pinned Python runs that installation's fixed `work --verify` command in an isolated subprocess with bounded output; no predecessor code is imported, no candidate JSON is accepted without completed Flow provenance, and no onboarding or implementation is repeated. The producer inventory, workflow config, original grant and candidate receipt are rechecked around verification. The new phase intent binds the producer, current controller and exact original requirement, source scope, candidate, instance and campaign accounting. It needs a fresh exact authorization; old grants are never upgraded.

For large candidates, optional `producerVerificationTimeoutSeconds` selects a positive metadata-verifier deadline; absent or `null` means no local wall-clock deadline, only with an explicit producer. That deadline is bound into the new phase intent; metadata output remains bounded. The producer installation and old workflow state must remain disjoint from the new continuation state. Current execution review consumes this continuation through its normal read-only verifier; container onboarding verification uses the pinned original producer. This does not rewrite an earlier `run` result or make its delivery envelope refer to the new continuation.

Flow derives literal typed npm stages from the verified onboarding, retains unsupported commands and unresolved prerequisites, and composes the supported local DAG serially in its existing topological order. All runtime constraints, dependency edges, stages, effects, prerequisites and source evidence are retained. One compatible pinned container resource must supply the whole closure. Browser attempt selection currently supports prepared Playwright provenance only: exact shared prepared image/spec identity, preparation package-version/tree facts and matching headless-shell binary pins in the catalog, provenance and runtimeTools. A bare browser catalog label on a plain Node spec is refused. Package version and browser-tree provenance remain preparation claims; the existing runtime adapter actually measures the pinned shell binary but does not independently prove browser launch, package version or full browser-tree identity. Catalog claims never count as execution or qualification success. The real container adapter enforces runtime, tool/cache/provenance and containment checks before project stages, and its existing receipt verifier is reused.

Flow builds one fresh independent source-review and workspace-policy request from the original requirement, assessed plan and exact baseline/candidate source, without the implementation summary. Actual source defects stop validation. Missing/unverified execution evidence may proceed to typed validation but stays unresolved in the result. Test exit zero does not establish browser behavior or product acceptance. Hosted CI, PR, merge, publication and release are not authorized by this grant.

All generated review/configuration/declaration/spec artifacts bind the retained phase grant. Container `sourcePaths` are regenerated from exact committed regular candidate files, never copied from a stale resource template. Definition paths remain protected: changed onboarding or validation evidence requires a fresh assessment. Unknown/interrupted adapter outcomes are not retried. Removed consumed inputs are not repaired.

The template provides measured infrastructure and limits, not project input selection or commands. The combined review uses its own schema and purpose; the existing candidate source review v1 remains supported unchanged. Its workspace policy may classify unchanged ZIP/TAR/gzip archives validated without extraction (64 MiB compressed/uncompressed and 10,000-entry inspection bounds) as retained inputs or generated outputs. Exclusion requires exact candidate generator-code and selected npm lifecycle citations, enriched locally with hashes and quotes. Changed files, protected definitions and executable files cannot be omitted. The initial archive adapter refuses such archive inputs outright, even when retention might otherwise be legitimate; they need a broader supported policy. The existing source-review adapter separately refuses changed binary files. Unclassified or ambiguous archive policy blocks execution; comments or labels alone are not accepted generator evidence. This is a narrow generated-archive adapter, not a general source-write exception language or a universal repository limit.

For each excluded archive, the verified complete container report must inventory the exact newly produced regular path, hash and size. A zero exit without required generated output returns `local-validation-incomplete-generated-output`. Recorded source policy, exclusions, produced files, candidate and container receipt/intent remain bound. Existence/hash/size is still not content correctness or product acceptance. Generator/lifecycle semantics are a source-cited model assessment, not a deterministic proof. Other legitimate source mutations still require a separate supported policy; restoration is never weakened.

Tests are offline synthetic provider/adapter contract tests and real local Git/derivation checks. They are not a full pilot, paid model run, Docker qualification, browser test, CI run or release.

Resource templates may include operator-provided application environment settings and prior preparation decisions. Flow selects and pins those supplied configuration inputs; it does not thereby independently discover or provision them. The core contains no project-specific environment names. Deriving arbitrary application settings is outside this continuation's autonomy proof.

Optional `captureArtifacts: true` in the continuation configuration requests a distinct, intent-bound collection grant. Absence retains the existing request/packet/schema behavior; false or other values are refused. The preview describes local PNG/JSON collection, at most eight files and 4 MiB per file. The combined capture bound is derived from the granted report size, reserving half for other evidence and accounting for base64 overhead, and explicitly does not authorize sending artifact bytes to a provider. A separately granted executed-acceptance review must authorize that transmission.

The fresh combined review uses `candidate_workspace_review_v2` and returns `artifactSelection` entries with exact `path`, `mediaType`, `reason` and candidate implementation `sources`. The model selects actual test/generator outputs from displayed code, not operator-authored template filenames. The controller verifies candidate-only source citations and enriches line ranges with SHA-256 and quotes. The model may derive exact output paths from displayed template literals, path joins and directory definitions, citing the definitions and write/screenshot calls. The controller does not evaluate code or claim to prove path semantics. Unresolved environment-dependent or computed paths must remain explicit missing evidence. Existing committed files, unsafe paths, cache/dependency/credential paths, duplicate selections and non-PNG/JSON types are refused. Legitimate hidden output directories (for example `.aim/analysis`) are allowed, while `.git`, `.ssh`, `.env*`, `.npm`, other sensitive configuration/cache directories and credential filenames are refused. Empty selection is an explicit execution blocker when capture was requested.

Flow deterministically compiles the reviewed selection to backend `artifactCapture` with the exact caps above. Template capture filenames cannot independently authorize collection. Exact review requests, source selection, output choices, caps and generated spec remain receipt-bound; unknown outcomes are not retried. Citation validity and exact path/type/scope restrictions are mechanical checks, while whether code actually generates relevant output remains the model's source assessment. Neither citations nor retained bytes establish screenshot content, browser behavior or product acceptance; executed-artifact review is a separate step.

### Opt-in finite execution refinement

With the opt-in review evidence policy, new artifact citation schemas enumerate
only the displayed protected candidate test/generator code paths and require a
nonempty citation list. Package manifests, CSS, documentation, undisplayed and
unprotected paths cannot be selected as artifact-generation citations. Lifecycle
references belong in execution refinement. Local identity, capture and acceptance
checks remain mandatory; historical rejected responses are never rewritten.

A new validation grant may set `refineExecutionPlan: true`. Existing grants and
retained responses are not rewritten. This requests a fresh candidate workspace
review before strict runtime composition. It classifies every original assessed
step, in order, as finite-local, evidence-only-acceptance, hosted-delivery, or
blocked, with exact candidate source citations. CI-area literal npm commands are
local candidates, not proof of a hosted result. Syntax eligibility never proves
termination or safety.

Finite stages retain their exact original command. A persistent preview/manual
acceptance step may leave the runtime schedule only when the reviewer explicitly
identifies earlier finite evidence producers and justifies the schedule. The
controller preserves all original prerequisites through recursive substitution,
requires the exact resulting runtime dependencies, and retains the complete
original acceptance step as an open obligation. The original assessment and
provider response remain unchanged. An unsupported/uncertain persistent service
blocks before container dispatch; there is no script-name or prose heuristic.

The separately granted executed-evidence review must explicitly resolve every
substituted acceptance obligation using actual artifacts and source. A plan,
finite process exit, or pending hosted release cannot close those obligations.
Insufficient manual/device/accessibility observations remain blockers. Genuine
hosted CI and deployment obligations remain separate delivery gates.

This refinement does not regenerate implementation. A retained Flow candidate
may be continued only through its independently verified producer evidence and
an explicitly granted compatible continuation; no edited or invented provider
response is accepted. Runtime/tool/cache qualification remains mandatory.

## Resume a completed Flow candidate under a new controller

`aim-flow.py resume-candidate --config <pin-file> --config-sha256 <sha256> --state <fresh-directory>` previews a distinct `aim-flow-resumed-candidate-local-run` schema-1 grant. It never upgrades or edits the original work/outer grant and never repeats onboarding or implementation. The input is a closed top-level configuration containing:

- `format`, `schemaVersion`, and the **new** installed `manifestSha256`;
- original `workflowConfig` file pin and `workflowState`;
- `producerController: {path, manifestSha256}` for the immutable original producer;
- pinned `python`, and `producerVerificationTimeoutSeconds` (positive integer, or `null` for no local wall-clock deadline);
- `reviewMaxOutputTokens`, `reviewMaxOperationNanoUsd`, `executionMaxOutputTokens`, and `executionMaxOperationNanoUsd`.

A new exact grant may include a pinned `campaignContinuation` declaration for an
explicit combined budget increase. Original producer verification still uses its
original campaign. Only the new review phases use the separately accounted
continuation, retaining the full old budget allocation and all old reservations.

Flow verifies the original candidate using that producer's fixed `work --verify` command. A new exact `--authorize-intent` permits only two new model generations and one contained execution: fresh source/workspace/execution-plan review, qualified tests, then source-selected retained JSON/PNG evidence acceptance review. Flow generates all child phase inputs internally, with artifact capture and execution-plan refinement mandatory. The original requirement, assessed plan, catalog, historical liabilities, campaign budget and provider credential scope remain bound to the original work configuration.

`--verify` reconstructs both the original producer and new validation/acceptance chain without dispatch or missing-input repair. Durable dispatch markers prevent unknown-effect replay. `deliver` recognizes this exact new format through its fixed verifier and preserves the original producer pins and verification deadline in publication configuration. It does not treat a copied result JSON as evidence or pretend the new controller generated the original candidate. Existing three/four-call `run` formats remain unchanged.

### Opt-in command implementation context

With `refineExecutionPlan: true`, candidate/workspace review now derives extra
review context from the selected npm stages in the candidate commit. It follows
package scripts, pre/post lifecycle scripts, literal nested npm invocations and
prefix roots, direct Node entrypoints, local imports/requires, literal Node/npm
child argv, and existing literal project source references. This is lexical
source retrieval, not shell evaluation or execution. No project commands run.

Whole committed files enter the normal review packet, including unchanged
baseline/candidate blob equality and both sides of changes. Dirty checkout bytes
are never read. `commandSourceContext` records roots, traversal, file identities,
limits and unresolved evidence. It excludes credential/config paths, suspicious
literal credentials, nonregular files and unsupported text. External packages,
dynamic paths, shell forms, runtime behavior and omitted files remain unverified.
The reader has explicit file, byte, command and edge limits; omission metadata
never means coverage is complete. Normal packet limits still fail closed.

These files are mutable candidate review evidence, not immutable onboarding
sources or an execution workspace closure. Historical non-refinement requests
retain their previous packet shape and retrieval behavior.

### Reconcile an interrupted resumed candidate

A fresh `resume-candidate` configuration may additionally declare
`interruptedPredecessor` with exactly these fields:

- `controller: {path, manifestSha256}` for the interrupted controller;
- `config: {path, sha256}` and the canonical interrupted `state`;
- `files`, mapping exactly `run.lock`, `grant.json`, `initial-result.json`,
  `validation-config.json`, and `validation-dispatch.json` to their SHA-256s;
- `campaignBudget: {path, sha256}`, pinning the **existing** campaign's
  `budget.json` at authorization preview.

This is narrowly supported for the audited original schema-1 resumed-candidate
code/configuration inventory (fingerprint
`e2c8c39f4e9da901f9ebc2b4451596d65f72dd423ba3f22581629f8756b5f745`).
Package version and documentation may differ; any executable/configuration
change requires another reviewed contract. The current controller verifies all
predecessor files, then reconstructs the old grant and dispatch with current
read-only preparation. It never imports or executes historical resume code.
The original producer still uses its explicitly pinned fixed `work --verify`.
The successor's original producer, candidate, requirement, source scope,
resources, review ceilings, Python and complete historical/campaign budget
must match the interrupted configuration. Only the current controller and the
explicit recovery declaration change.

An absent child after power loss is **not** evidence that no external call
occurred: the old child-directory creation did not fsync its parent. A token
count request may already have transmitted source to the approved provider.
The new grant preserves this uncertainty explicitly. The narrower conclusion
is based on the audited append-only accounting contract: paid generation is
preceded by an atomically replaced, file- and directory-synced campaign
reservation. No reservation for the predecessor, in a preserved verified
ledger, excludes paid generation under that durability contract. A successful
paid review is required before container dispatch, so project execution is also
excluded. This is not a forensic guarantee against externally deleted/restored
ledgers or storage that violates acknowledged fsync durability. Any predecessor
reservation, even unknown/completed and even with no child, refuses recovery.
No liability is refunded or reset.

Flow holds the existing predecessor lock exclusively and nonblocking throughout
preview, execution or verification. A child directory, extra marker, missing or
changed input, linked file, active lock, unknown code contract or changed budget
refuses recovery. A new exact intent authorization is mandatory. Under the
existing campaign lock, Flow durably consumes a single-successor claim keyed by
the predecessor's canonical state before creating the new run. It preserves the
pinned baseline ledger as `predecessor-budget.json` in the new run; every old byte
stays untouched. An orphaned claim or interrupted new-state creation is not
repaired and cannot authorize another successor. The original dispatch is never
removed or retried. The successor's normal child unknown-outcome rules remain.

All historical reservations remain, including unsettled liabilities. Later
legitimate settlements and unrelated campaign appends remain compatible with
terminal verification. The successor has at most two exact generation attempt
states; their retained reservations and results must still match live ledger
rows, so rolling the campaign back to its pre-recovery snapshot fails closed.
The combined historical/campaign USD20 ceiling remains enforced by the existing
reservation adapter. No new onboarding, implementation, publication, deployment
or human acceptance is authorized by this recovery.

### Pinned execution context before refined review

New refined continuations disclose a bounded, source-bound planning projection
of the exact candidate container template before requesting review. The phase
preview binds candidate commit/tree, literal stage map, resource catalog and
template digests, runner digest, resource limits and safe initial environment
facts. One compatible container candidate is required before paid review; the
post-refinement selection must remain that exact template. This selection is not
runtime qualification. Existing containment, runtime/tool/cache checks and
per-stage source-restoration enforcement remain unchanged.

The runner does not inherit the host environment. The projection includes its
fixed initial variables and conservative non-sensitive application values.
Sensitive, host-specific or unclassified application values stay local; their
names are marked present with withheld values, never absent. Output defaults
can be reasoned about only from displayed source and these initial facts; npm
and project code may derive or change their own variables. No operator-authored
artifact filename is introduced.

The original checkout and image source baseline remain protected. Private tmpfs
copies may change during a stage, including timestamp-only metadata changes;
content, size and mode must match before and after every stage and at completion.
Archive exclusion is a separate policy and cannot waive that restoration. A
finite-local classification describes a source-supported bounded command shape,
not a proof that dependencies terminate or behave correctly. Runtime evidence
stays pending; known source defects and safety/authorization prerequisites still
block. Direct finite predecessors are retained, recursively substituting only
reviewed evidence-only acceptance nodes. Operator-only deployment preparation
remains an external delivery obligation, not a supported local stage.

Standalone reviews without this execution context retain their prior contract;
non-refinement request shapes and all previously retained receipts are unchanged.


### Opt-in substantive sections and opaque literal context

New `resume-candidate` and `validate-work` configurations may declare
`reviewEvidencePolicy: substantive-sections-and-opaque-literals-v1` with captured,
refined review. The exact new intent binds this policy. Existing configurations,
sealed grants, criteria, requests and failed reviews keep their prior contract.

Executable entrypoints and local code imports take retrieval priority over literal
file references and non-executable data, within the unchanged byte/file bounds.
Omissions remain explicit; no execution closure is inferred from this ordering.

In this mode heading-only Markdown sections attach to the following substantive
section (or to the preceding section at EOF). All original requirement bytes remain
unchanged and are preserved in the criteria. An oversized heading prefix or a
headings-only document refuses before provider work. A typed response union requires
at least one source citation for `covered`; unsupported criteria remain unverified.

Quoted single-line credential literal values in unchanged committed context may
be withheld as opaque local identities. The source records the original full-file
SHA-256, exact raw Git object/mode equality, display SHA-256, unchanged line inventory
and explicit redaction locations. Citations and quotes bind the displayed projection;
execution still uses the original committed bytes. Equal opaque identities within
one file preserve only equality, not the contents or safety of a value. Every
equal quoted occurrence in that file is withheld, including comparison literals.
Escaped values, unquoted repetitions and repeated values outside projected sources
refuse rather than transmitting a supposedly withheld value through another field.

Changed credential-bearing files, credential/configuration paths, private keys,
unquoted tokens and unsupported secret forms still refuse or remain omitted. The
projection is not complete byte disclosure, fixture certification, execution
qualification or permission to ignore value-dependent uncertainty. It preserves
reviewable implementation around withheld values and carries these limitations into
executed-evidence review. No failed response is edited, promoted or replayed.

A fresh `resume-candidate` or `validate-work` grant may explicitly carry
`sourceReviewReuse:{controller:{path,manifestSha256},config:{path,sha256},state}`.
Flow reconstructs the retained source review under the pinned original control
manifest, requires its full disclosure packet and wire request to equal the new
review's, and reparses the exact completed provider response against its paid
accounting. Partial, rejected, changed or unsettled reviews cannot be reused.
The new state retains `review-reuse.json`; the old attempt and reservation are
unchanged. This grants no retry of an unknown container operation. A separately
authorized new contained attempt and a fresh executed-evidence review are still
required. A resumed grant using this option permits at most one new model call.

Container staging commits have a bounded 180-second Docker snapshot deadline;
ordinary Docker metadata retains five seconds and project test deadlines remain
unchanged. An unknown snapshot result is never retried automatically.

`sourceReviewReuse.allowDiagnosticRunnerUpgrade:true` permits only the qualified
source-identity diagnostic upgrade. Flow reads both pinned runner files and
requires their entire bytes to differ solely by the exact audited assertion
message patch. It also requires every disclosure/request field other than that
runner digest to match. The original review remains bound to its old runner;
the new grant, reuse receipt and acceptance evidence retain the distinct new
digest and the explicit compatibility qualification. No other runner behavior,
source, requirement, environment, limit, policy or instruction change qualifies.
Without this opt-in, full packet/request byte identity remains mandatory.

`sourceReadModePolicy:"restore-byte-identical-read-bits-v1"` is a separate new
grant option for `resume-candidate`, refined `validate-work` and
`repair-and-validate`. Its exact policy and contract are disclosed to the source
review. After process cleanup, the adapter may restore only a non-executable
0644 input observed as 0600, 0604 or 0640 when its full hash and size are still
identical. It remeasures the result and records the path, stage, original/new
modes, size and digest. Every other content or permission discrepancy still
fails. Original checkout and read-only baseline are never changed. Execution
review receives these actions explicitly attributed to the Flow adapter, not to
project self-restoration. The default remains strict; this behavioral change
requires a fresh source review and cannot use diagnostics-only compatibility.

New explicit grants may set `reviewMaxSourceBytes` to an integer at most 2097152
(2 MiB) for the complete selected source review. The default remains 1048576
(1 MiB). The limit is retained in the resumed/validation grant, derived workspace
policy (`sourceByteLimit`) and source packet. Per-file limits, source identities,
secret checks, complete criteria and no-silent-truncation rules are unchanged.
Subsequent execution review reuses the verified complete packet. Provider token
and cost ceilings must still admit that concrete request before dispatch.

## Reuse a completed validation after a controller correction

A fresh `resume-candidate` config may carry `completedValidationReuse` with the
prior controller path/manifest, validation config pin/state, exact phase-grant
pin and workspace-evidence pin. The prior immutable controller runs only its
read-only completed-phase verifier. Reuse requires passing project stages,
complete captured outputs and the same candidate, producer, funding, runtime
catalog and review policy. It is distinct from source-review reuse and
pre-generation interruption recovery and cannot be combined with either.

The new outer grant authorizes one fresh executed-evidence review and **zero**
project attempts. The new controller owns that review and outer completion;
the original controller and retained receipts still identify the actual source
review and project execution. No old grant, receipt or installation is rewritten.
Unknown, missing, changed or failed validation cannot acquire passing reuse.
Delivery verifies the explicitly retained execution location rather than assuming
all phases were executed under one controller or inside the new state directory.


## Change-focused test scheduling (development after beta.10)

Onboarding now asks for the smallest source-supported checks for the requirement,
with affected scope, purpose and timing made explicit. Existing project filters
may be selected only when their syntax is evidenced. Aggregate scripts and normal
npm hooks must be traced before scheduling their component checks separately.
Early local feedback, exact-commit hosted CI and production observations serve
different purposes; completing one does not manufacture the others.

An independently reviewed execution refinement can use `reuse-local-test` for a
repeated **tests-area** step. It must name exactly one earlier `finite-local`
tests step in `evidenceSteps`, use empty `executionDependsOn`, and retain the exact
same literal supported npm-run command and working directory. No intervening
step is allowed except other aliases of the same producer. Both steps must have
actual derived local command adapters. Installation operations, CI/deployment
steps, different commands/roots and intervening work are refused. There is no
automatic command deduplication: independent repeated runs remain finite steps.

The normal composer then dispatches the producer once. Its ordinary failure
handling still stops validation. The skipped step remains in
`acceptanceObligations`; `reusedLocalTests` records a planned evidence mapping,
not a passing test or product acceptance. Source citations, retained provider
review, exact candidate/runtime inputs, phase authorization and dispatch journals
remain mandatory. Historical grants/reviews are not edited or replayed.

This is reuse **within one validation attempt**, not a cache for tests on another
candidate, environment or production state. Selecting a smaller alternate test
command must happen in the assessed, source-reviewed plan. There is no safe
universal inference that a frontend path makes backend or Windows CI optional;
project-required release guards continue to apply. Cross-candidate per-test input
closures and project-declared impact/filter rules remain future work. The existing
whole-validation reuse still requires its exact source/runtime/producer bindings.
This source increment does not replace the already published beta.10 download or
hot-patch retained installed controllers.
