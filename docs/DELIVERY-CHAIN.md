# Local feature acceptance to GitHub main-push handoff

Development `aim-flow.py deliver` connects actual completed Flow local acceptance
with the existing typed publication and merge adapters. It does not import an
operator-prepared PR, infer publication permission from a local run, or reuse an
older PR's grant. The owning workflow must obtain a **new exact GitHub phase
intent** for the chosen repository, source/base, new branch, generated draft
text, ready-for-review transition, guarded merge and exact main-push checks.

This stage never invokes a model, container, project command, CI rerun, artifact
download, release or deployment. Final `main-push-handoff-ready` means a retained
exact merged-main workflow/run/attempt/jobs/artifact observation is available for
the project's separately authorized release process. Production is not deployed,
product acceptance is not claimed, and original pending delivery obligations are
preserved without inventing dispositions for them.

## Actual local gates

A declaration pins the existing four-phase outer run's config, grant and result.
The same installed controller reconstructs it using the read-only outer verifier
with exact Python and review limits from its original grant. That verifier checks
both original workflow phases, independent source/workspace policy, source-bound
contained stages, actual retained JSON/PNG outputs, and the final execution-review
request/response. No project execution or provider request is performed during
verification. The driver additionally requires:

- `local-feature-evidence-reviewed` and `localFeatureAccepted:true`.
- No execution-review blockers, blocking findings or unresolved required unknowns.
  Under explicit `severity-and-impact-v1`, medium/low follow-ups remain in the
  verified source/execution receipts, local proof and generated PR body without
  preventing publication. Full descriptions, causal rationales and citations
  remain in the referenced receipts; bounded factual titles appear in the body.
  Legacy receipts without the policy retain their original no-findings gate.
- Every local criterion satisfied or an explicitly typed pending delivery
  obligation; every prior item resolved or typed pending delivery.
- Passing executed validation, complete workspace output evidence and identical
  candidate/source bindings across the outer run, workspace and acceptance.
- Exact retained receipt hashes, original requirement, and unchanged pending
  delivery obligations.

This closes the semantic gate intentionally absent from the low-level publication
adapter's caller-provided attachments. It remains trusted local evidence and a
model assessment, not an adversarial same-user ledger, human review or independent
public attestation.

A completed `resume-candidate` run can retain `producerKind: repair-candidate`
and its original `repairConfig` pin. Flow forwards both to publication, which
uses the fixed repair verifier and checks the two single-parent links back to
the original assessed baseline. The original full diff, accepted local evidence
and separate GitHub grant remain mandatory.

Local receipts are read completely using the existing descriptor-size/change
guard, then reverified against the original source, execution and capture limits.
They are not constrained by the GitHub transport's 8 MiB metadata limit: valid
captured artifacts can exceed that size after base64 encoding. The lower adapter's
aggregate attachment is a compact, deterministic `local-proof.json` containing
the original exact receipt pins. Its bytes are pinned by the phase intent and
checked on every continuation. The proof JSON is a reference inventory, not a
substitute for the complete upstream verification or an authenticity claim.

## Configuration

The schema-1 declaration contains exactly:

- `format: aim-flow-github-delivery-chain`, `schemaVersion:1`,
  `manifestSha256`: this installation.
- `localRun`: `config:{path,sha256}`, `state` (canonical absolute path),
  `grant:{path,sha256}` for that state's `grant.json`, and `result:{path,sha256}`
  for its `result.json`. The four-call schema-2 outer grant is required; old
  three-call grants never acquire acceptance or publication authority.
  A completed resumed-candidate run may additionally declare
  `controller:{path,manifestSha256}` for its original immutable installation.
  The delivery installation then invokes that exact controller's read-only
  `resume-candidate --verify`, requires the exact retained result and rechecks
  controller/input pins. Existing grants and paid responses are not upgraded.
  The existing byte-bound verifier cache can reuse this fixed verification and
  its complete dependency closure; an unchanged result flag alone is never proof.
- `repository:{name,id}`, `baseSha`, `baseRef`, new `branch`, exact `title`.
- `bodyScope: requirement-local-evidence-and-pending-delivery-v1`.
- `requiredPrChecks:[{name,appId}]`, `requiredPrStatuses:[context]`, and `workflow`
  using the existing [exact GitHub delivery declaration](GITHUB-DELIVERY.md).
- `transport:{gh:{path,sha256},home,configDirectory,timeoutSeconds}` using existing
  gh authentication and bounded metadata reads. No token values are supplied.

Flow generates the draft body deterministically from the verified original
requirement, exact source identities, measured local stage/artifact counts,
criterion dispositions and evidence digests. It explicitly describes model
assessment and still-pending hosted CI, merge, release and production work. It
never claims arbitrary unseen tests passed or edits an existing PR body later.
Private paths/common secret forms or an oversized body stop rather than being
silently removed. The exact complete body appears in the phase preview.

## Execution and recovery

- `deliver --config FILE --config-sha256 SHA --state NEW --preview` verifies local
  evidence and returns the new exact `intentSha256`. No network or state writes.
- Use `--authorize-intent HASH` once to create durable phase state and begin the
  specifically disclosed GitHub chain.
- `--continue` advances only still-unattempted phases covered by that retained
  grant. It never repeats an unknown mutation. It can perform the still-unattempted
  draft step after read-only reconciliation of an uncertain push, because the
  same exact draft was disclosed in this new phase grant.
- `--observe` performs external reads only. It cannot create a draft, mark ready,
  merge or otherwise exercise an unattempted mutation.

Exact PR readiness is observed and retained **before** reserving the merge child.
Pending/failing/missing/ambiguous checks leave `awaiting-pr-readiness`; no merge
attempt exists and later continuation may observe again. The merge adapter repeats
all source/check/base gates immediately before its effects. After a child merge
attempt exists, all continuation is its read-only reconciliation/CI observation.
There is no second merge, automatic rollback or protection bypass. A draft-ready
step that succeeds but cannot proceed does not silently get a new merge attempt.

Every child has its original fsynced pre-effect state. The outer stage also retains
exact child intents before dispatch, refuses missing consumed inputs, and stores
immutable numbered result receipts. An unrecorded pre-existing child is never
adopted or given a replacement outer reservation. A lost child dispatch with missing state is
blocked, not replayed. Readiness/HTTP errors are retained and stop that invocation;
there is no automatic polling/retry loop. Unknown causal attribution remains
qualified in child evidence.

A successful outer receipt pins the exact `aim-flow-github-main-push` child
receipt and carries merged SHA/tree, observed main SHA, workflow, run ID, attempt
and artifact metadata. The merged Git tree must exactly equal the verified local
candidate tree; different content stops handoff without retry or rollback. The
receipt also pins the phase grant, original publication receipt, candidate receipt
and completed local run. Push/draft/merge response-confirmation flags preserve
causal uncertainty; remote reconciliation alone is not proof Flow caused an effect. The project's release command must independently recheck these
at point of use. A release workspace and project-command handoff are separate
work; this driver neither executes nor bypasses them.

## Offline qualification

Tests must use actual local state and installed verification, with injected
provider/container/GitHub effects only. Mock replies do not establish live API,
container/browser, hosted CI, publication, merge, production behavior or completion
of a live end-to-end pilot. Existing manually prepared PRs are not adopted here.

`tests/test_delivery_chain.py` requires the integrated four-call outer-run fixture
(`tests/test_flow_run_executed.py`). A standalone older checkout skips those
integration cases explicitly; skipped cases are not qualifying test results.
The installed source-free preview case invokes the real packaged read-only outer
verifier. Recovery cases use real durable adapter state and mock only the external
boundaries, including unknown push/merge outcomes and pending PR/main CI.

## Explicit owner verification-scope acceptance

`accept-local` is a separate, source-bound local decision adapter. It preserves a
completed blocked model review and never rewrites it as model acceptance. A new
exact owner grant binds the candidate, original requirement, raw review, every
uncertain criterion/prior item/top-level uncertainty, the owner's instruction,
rationale and unverified limitations. Existing model-only grants are unchanged.

The adapter refuses concrete blocking findings, criterion defects, failed
execution, missing source/artifact support, unaccounted blockers and unsupported
execution obligations. Hosted CI, publication, release and production acceptance
remain pending. `deliver` can consume its pinned config/grant/result through an
optional `localRun.ownerAcceptance` reference. It verifies the original run and
the separate owner receipt, discloses that the model remains blocked, retains the
raw criterion dispositions and records owner acceptance as a distinct basis.
No API call or project test is performed by `accept-local`.
