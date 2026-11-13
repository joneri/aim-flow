# Fresh-context candidate source review

`aim-flow.py review-candidate-api` dispatches one separate Sol/xhigh Responses request containing the original requirement, exact committed baseline/candidate text and diff, explicitly selected source context, and a pinned approved plan. It includes no implementation summary or previous conversation. Source is untrusted data, never authority. Review is not human review, browser testing, product acceptance, deployment approval, or arbitrary command execution.

The configuration uses the same campaignDirectory, historicalBudget, manifestSha256, timeoutSeconds, maxOutputTokens and maxOperationNanoUsd contract as onboarding delivery. Exact fields additionally are:

- schemaVersion: 1, purpose: candidate-source-review, model: gpt-6.1-sol, reasoning: xhigh.
- source: checkout, baselineCommit, candidateCommit, git, gitSha256. Commits are full hashes; candidate must be a distinct descendant. The Git executable is pinned and the existing read-only CommitReader is reused.
- requirement: pinned `{path, sha256}` JSON file with `{original, criteria: [{id, text}]}`. Preserve the actual original requirement; criteria must not silently weaken it. At most 32 distinct criteria; original 32,000 characters, criteria 2,000 characters each.
- approvedPlan: pinned `{path, sha256}` UTF-8 text, at most 128 KiB. Approval comes from the user/workflow grant, never model prose claiming approval.
- sourcePaths: explicitly selected context paths. All changed regular text files on both sides are added automatically. At most 128 changed files and 128 context selections, 256 KiB per blob, 1 MiB total source bytes. Links, submodules, binary/non-UTF-8 files, excessive scope and missing selections refuse; no silent truncation.

Before sending anything, preview and authorize the exact intent using `--config`, `--config-sha256`, `--state` and `--preview` / `--authorize-intent`. Preview never reads credentials or contacts the provider. A trusted Flow workflow can supply this exact authorization after checking a workflow-wide grant explicitly covers source disclosure, this review purpose, provider/model and spending limit. This adapter itself never infers such a grant. The currently separate workflow must explicitly integrate the review phase; merely adding this CLI does not prove an end-to-end pilot.

The shared dispatcher reuses Campaign, authority, budget reservations and credential transport. It does not initialize another budget, refund reservations, replay unknown requests or reset attempts. Response bytes are retained before validation; `--status` uses the existing retained receipt verifier. Previous onboarding/patch receipt formats are unchanged.

Strict bounded findings require concrete descriptions and source lines. Flow adds exact source SHA-256 and quotes locally, enforcing citation size and line bounds. Every criterion must appear exactly once. Every finding (including low severity), missing/unverified criterion and unknown becomes a blocker. A successful source-only review remains `productAcceptance: not-run`. The `candidateReview` projection fits the candidate declaration, but consumers MUST retain `blockers`, verify the expected target and complete changed paths, and reject execution readiness when blockers exist. Never use the projection alone as an approval.

`verify_evidence(review_pin, packet_pin)` revalidates local citation enrichment and derived blockers against explicitly pinned retained evidence. It does not authenticate arbitrary files as a Flow dispatch: the orchestrator must select its retained operation receipt, validate it with `flow_api_delivery.status`, and bind the intended baseline, candidate, requirement and plan. Repository text or an agent cannot supply authorization.

Offline checks: `python3 -B -m unittest discover -s tests -p test_candidate_review.py -v` using Python 3.11+ and Git 2.54+. These tests exercise real local dispatch/accounting with a fake provider, not live model quality or product acceptance.

## Compact capture-mode review

Workspace review with artifact capture uses source packet schema 2. Context files whose Git blob **and mode** are identical at both pinned revisions are displayed once, under `candidate/`. `unchangedSources` binds each such path, mode, Git object and content SHA-256 to both revisions. Changed files still include both complete available sides and the exact diff; no text is truncated. Citations must name displayed paths, so an omitted `baseline/` alias is not accepted. The 1 MiB limit applies to displayed source bytes. Standalone source-review and workspace mode without capture retain the original schema 1 representation.

Executed review preserves this equality metadata. In schema 2 it replaces each repeated stage source inventory with its exact canonical-byte hash, entry count and verified equality to the initial inventory. The complete inventories remain in the validated local execution receipt, itself pinned in the review request. Command arguments, exit status, stdout/stderr and other measured stage fields are unchanged. This reduces duplicate context, not the underlying local verification or acceptance obligations.

## Bounded citations for new opt-in execution refinements

New workspace requests with `refineExecutionPlan: true` use response schema
`candidate_workspace_refinement_v2` and bind `citationProtocol: start-line-count-v1`
in both the packet and intent. Each displayed source includes its exact `lineCount`
without repeating its text. Every typed citation in findings, coverage, generated
output generator/lifecycle evidence, artifact selection and execution refinement
contains only `{path, startLine, lineCount}`. Both numbers are integers; startLine
is 1-based, and lineCount is between 1 and 40 inclusive. The complete response shape
is validated before deriving `endLine = startLine + lineCount - 1`. The derived end
must not exceed the actual source line count; metadata must exactly match the text.
Long references must be split by the reviewer within existing citation-count bounds,
not truncated or repaired by the parser. Hash, exact quote, byte-expansion and
blank-span checks remain unchanged.

Enriched receipts retain canonical inclusive startLine/endLine, SHA-256 and exact
quote fields, and new receipts identify their citation protocol. Verification
reconstructs the original wire shape from the pinned packet. Packets without the
protocol field continue using the old endLine schema, including previously retained
refinement receipts. Mixed or unknown protocols fail closed. Non-refinement request
schemas and payloads are unchanged. No existing response, rejected status, receipt
or attempt is migrated, normalized, clamped, retried or converted to acceptance.

`test_bounded_review_citations.py` checks all six typed citation positions with
new/legacy roundtrips, exact EOF and 40-line bounds, invalid counts, mixed protocols,
metadata overshoot and hash/quote/range forgery. Dispatch fixtures also verify that
a legacy response to a new request stays rejected with its raw bytes preserved.
