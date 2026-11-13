# Executed acceptance review (separate grant)

When the verified source phase selects `findingDispositionPolicy:
severity-and-impact-v1`, executed findings are typed and carry severity, source
and execution references, impact and rationale. Only genuine requirement,
evidence-integrity or material delivery failures block as findings. Medium/low
follow-ups remain reported; critical/high findings require a blocking impact.
Source follow-ups are retained separately from unresolved required obligations,
so they do not silently reappear as mandatory repair work. Both source and
executed follow-ups survive in the acceptance receipt and delivery proof/body.
All original criterion, required-artifact, actual-stage-result, unknown and prior
blocking-obligation checks remain enforced. The policy cannot turn failed tests
or unverified feature behavior into acceptance. Legacy packets retain v1 behavior.

`aim-flow.py review-executed-work` is a standalone bounded post-execution review.
It does not amend source-review v1 or validation receipts and cannot execute project
commands, publish, deploy, spend outside the existing campaign, or claim human review.

Requires a unified installed controller that includes the read-only
`flow_workflow_validation.verify(config_path, config_sha, state)` interface and the
optional validated container `artifactCapture` feature. Original work, continuation,
and acceptance must use that same immutable controller. Old grants are not upgraded.

Configuration (all keys required):

```json
{
  "schemaVersion": 1,
  "manifestSha256": "<installed controller manifest SHA-256>",
  "validationConfig": {"path": "<absolute continuation config>", "sha256": "<SHA-256>"},
  "validationState": "<absolute completed continuation state>",
  "maxOutputTokens": 8192,
  "maxOperationNanoUsd": 1000000000
}
```

Preview with `--config PATH --config-sha256 SHA --state NEW-DIRECTORY --preview`.
Inspect the exact `request` (including all image pixels and JSON contents) and
`intent`; preview does not access credentials or call the provider. Submit once
with `--authorize-intent INTENT-SHA --key-file PRIVATE-FILE` only after separately
authorizing this exact disclosure and generation. The adapter inherits the upstream
campaign, historical spend, deadline and fixed Sol model. Token counting includes
images using the actual Responses request. There is one generation attempt, no new
budget, no refund, and no retry after uncertain/incomplete outcomes.

Use the same pinned configuration with `--verify` for a read-only reconstruction.
Verification recomputes upstream provenance, candidate source, source review,
container receipt, packet and request, then reparses the retained actual response.
Changing candidate/config/controller/receipt/artifacts refuses; arbitrary uploaded
JSON or a manual implementation report cannot replace execution state.

The dispatched packet carries every original criterion and exact candidate source,
review, measured runtime/stages and retained JSON artifacts. PNGs are separate
Responses `input_image` parts using base64 data URLs and `detail: high`, not a textual
claim that an image exists. API contract reference:
https://developers.openai.com/api/docs/guides/images-vision

Each criterion receives satisfied/missing/defect/uncertain/pending-delivery, concrete explanation,
source lines enriched with exact hash/quote, and exact evidence IDs. The parser
requires source plus retained executed artifact references before satisfied can
stand; visual requires PNG, behavior requires JSON. Missing artifacts become
criterion blockers. Every prior source finding, unknown, blocker, workspace unknown,
unsupported assessment step and assessment unknown receives a stable input ID and
exactly one explicit disposition. Omission/duplication is refused. Resolved needs
exact source plus newly executed artifact references; exit zero alone cannot resolve
an earlier browser/runtime gap. Unresolved entries block local acceptance.

Pure later hosted-CI/release/deployment obligations can be pending-delivery only
with exact source citations and an existing typed CI/deployment assessment step.
Every typed CI/deployment assessment step is also retained as a canonical pending
delivery obligation independently of model coverage/disposition aliases. No local
PNG/JSON can resolve such a step, and the parser refuses that claim. An unsupported
test step cannot borrow a delivery step; concrete source defects and
workspace-policy unknowns cannot be deferred. Mixed feature/delivery criteria must
not hide unverified local behavior by deferring the whole criterion. Classification
and semantic relevance remain bounded model judgments, not schema-proven truth.

`localFeatureAccepted` expresses the local criterion/disposition review outcome.
`pendingDeliveryObligations` preserves each deferred obligation and its citations.
`productAccepted` remains false while any delivery obligations are pending, even
when local features pass. `deliveryReady` is always false: this phase never validates
or performs hosted CI, publication, release or deployment. A later gate must retain
these obligations and establish the corresponding evidence/authority.

The generic dispatcher still labels successful transport `source-review-ready` for
compatibility. That label is NOT acceptance. Only this adapter's verified review
`localFeatureAccepted` and `productAccepted` fields express their respective bounded model assessments. The immutable
upstream validation continues to report `productAccepted: false`.

Limits: at most 32 artifacts, 4 MiB per artifact, up to the declared capture and report-headroom budget and 2 MiB text packet;
no truncation. Backend verifies generated output identity and byte retention.
PNG structural checks are not proof of visual validity. Same-UID project/helper
execution is not an adversarial trust boundary. JSON assertions require source/test
scrutiny by the reviewer; semantics cannot be proven by schema checking. A model
can misjudge evidence. Home paths in generated runtime/stage/JSON metadata and common credential patterns
everywhere are refused, not silently redacted. Generic home-like literals in exact
approved original requirements, committed source/diffs and hash/line-bound citation
quotes remain intact; these are source evidence, not controller-location metadata.
Exact private input/controller/state paths are still refused across the full request; inspect all authorized source/JSON/image disclosure, since
pattern checks cannot guarantee absence of arbitrary secrets, especially in pixels.
No browser/platform/deployment capability is inferred beyond supplied evidence.
