# Sol API proposal delivery

`aim-flow.py deliver-api` is included in the beta.10 controller as a separate, proposal-only route for `gpt-6.1-sol` with `xhigh` reasoning. It does not apply edits, run project code, accept a product, deploy, or retry. `run-local-intake` retains its existing pinned mini model and historical budget rules. The local Codex adapter remains separate.

## Contract

- Pin the UTF-8 configuration by SHA-256. Pin the exact Responses request JSON, source files, task, AIM Dev instructions and any other required inputs in that configuration. Preview also binds the adapter, entrypoint, credential transport, budget validator and filesystem helpers by SHA-256.
- Requests require `model: gpt-6.1-sol`, `reasoning: {effort: xhigh}`, `service_tier: default`, `store: false`, `stream: true`, string input/instructions, and a positive `max_output_tokens`. Tools, alternate endpoints/models, priority/flex tiers and unrecognized fields are refused.
- `text.format` must be a strict JSON schema with a string `summary` and an `edits` array of objects containing only string `path`, `old`, `new`. See `PATCH_SCHEMA` in `lib/flow_api_delivery.py`. Each old string must be nonempty and occur exactly once at its sequential replacement point. Only pinned source paths are permitted. The model should produce the smallest useful replacements. Validation happens in memory; no source file is written.
- Preview is local and read-only. An exact `--authorize-intent` is required before token counting or generation. The key is supplied only with `--key-file` for this invocation, must be private and contain `OPENAI_API_KEY=...`, and is read by the existing credential transport. Neither key nor credential path is retained. Use the existing approved credential; never put it in configuration or prompts.
- Token counting uses `responses/input_tokens`. Provider bounds are 922,000 input tokens, 128,000 output tokens and 1,050,000 combined tokens. A reservation adds 1,024 input tokens of conservative headroom. Up to 272,000 reserved input tokens it charges $2.50/M input and $10/M output; beyond that threshold it charges $5/M input and $15/M output. These maximum input rates include potential cache writes. Reservations are never refunded, even when measured usage is lower or a request fails.
- The shared campaign ceiling is $20 including the full pinned historical liability: `legacyReservedNanoUsd` plus every historical `reservedNanoUsd`. `Budget.read_existing` verifies the established history. Pin `budget.json`, `budget-established.json`, all `budget-before-*.json` files and the original legacy ledger. Any drift refuses new generation. Existing historical and legacy writer locks are held read-only through dispatch; old files are not modified.
- Use the same dedicated `campaignDirectory` for all operations under this approval. On first authorized use, a private, exclusive, durable `sol-api-authority-<hash>.json` sibling of the historical directory binds its canonical history paths and exact pins to that campaign. Another campaign for the same history is refused. Never delete/reset the authority, campaign, or attempt directories. This is local trusted-operator continuity, not protection against an operator deleting or rewriting every record or copying the entire history to a new identity.
- The full reservation and attempt receipt are durably flushed before generation. Pins are checked again immediately before dispatch. Each state directory is single-use; an identical parsed request is refused across different operation directories. An interrupted, unknown, refused, incomplete or failed outcome never authorizes retry. A reviewed correction requires a different explicitly previewed request, a new state directory and the same campaign.
- The private terminal response is retained before model/status/usage/proposal validation, including refusals and incomplete/failed terminal responses. Transport loss without a terminal response is unknown. All reservations remain held. `--status` verifies retained evidence; it does not retry or prove current source freshness.
- `timeoutSeconds` is 1–3600 (900 is useful for Sol). Only this invocation's wall deadline changes. The legacy 240-second deadline, 180-second socket timeout and legacy prices/model limits remain unchanged.

## Configuration

The exact keys are:

```json
{
  "schemaVersion": 1,
  "method": "responses-api",
  "model": "gpt-6.1-sol",
  "reasoning": "xhigh",
  "candidateDirectory": "/private/candidate",
  "sourceFiles": {
    "src/example.js": {"path": "/private/candidate/src/example.js", "sha256": "FILE_SHA256"}
  },
  "inputFiles": [
    {"path": "/private/inputs/task.json", "sha256": "FILE_SHA256"},
    {"path": "/private/inputs/aim-dev.md", "sha256": "FILE_SHA256"}
  ],
  "request": {"path": "/private/inputs/request.json", "sha256": "FILE_SHA256"},
  "campaignDirectory": "/private/sol-campaign",
  "historicalBudget": {
    "directory": "/private/existing-aggregate-budget",
    "legacy": {"path": "/private/existing-legacy/budget.json", "sha256": "FILE_SHA256"},
    "files": {
      "budget.json": "FILE_SHA256",
      "budget-established.json": "FILE_SHA256"
    },
    "liabilityNanoUsd": 123456789
  },
  "timeoutSeconds": 900
}
```

Add every existing `budget-before-*.json` to `historicalBudget.files`. The liability example is illustrative only; calculate it from the verified established ledger. Source and input pins are operator-selected disclosure scope, not automated evidence that a prompt is safe to transmit. Use one canonical historical budget identity. Campaign and state directories must be disjoint, outside the candidate, controller checkout and historical roots, with existing parents.

```sh
python3 -B aim-flow.py deliver-api --config /private/config.json \
  --config-sha256 CONFIG_SHA256 --state /private/operation-1 --preview
python3 -B aim-flow.py deliver-api --config /private/config.json \
  --config-sha256 CONFIG_SHA256 --state /private/operation-1 \
  --authorize-intent PREVIEW_INTENT_SHA256 --key-file /private/approved-credential.env
python3 -B aim-flow.py deliver-api --state /private/operation-1 --status
```

Use Python 3.11+. Review `proposal.json`, retained response and accounting before separately preparing or applying a candidate. This route's offline tests do not attest live model behavior, model availability, UI correctness, AIM gates or independent acceptance.

## Install and upgrade

Verify and install the beta.12 controller with its matching bootstrap and independent pins. Its schema-3 archive contains exactly 100 files. Beta.9 has 61 and beta.8 has 53; each needs its original matching bootstrap. Install to a new directory and preserve old instances, grants, budgets and attempts. Equal schema numbers do not imply compatible inventories.

Run `/absolute/path/to/new-private-controller/aim-flow.py deliver-api` with the arguments above. The installed command uses only its packaged modules; no source checkout, Git or Codex CLI is required. Preview requires an existing established budget history, selected source and request files, but no API key or network. This advanced route does not initialize a fresh campaign budget or replace the simpler mini intake.

Keep the original campaign and authority files when upgrading. A new installation changes implementation pins, so preview the next operation again and authorize its new exact intent. Never edit an old receipt or reset an attempt to make it match a new version. Use the original installation to inspect an older retained operation.

An explicitly authorized budget increase uses a separate continuation campaign.
Its pinned schema-1 declaration has format `aim-flow-campaign-continuation`, new
`campaignDirectory`, combined `limitNanoUsd`, `priorCampaign` containing the exact
old campaign directory and historical budget, and a `priorBudgetSnapshot` file
pin. The old journal, identities and verifiers remain unchanged. A single durable
authority claim prevents creating multiple top-ups of the same old campaign.
The new journal reserves the old campaign's entire original ceiling, then admits
new requests only within the difference. Thus an original USD20 allocation plus
a new total USD30 ceiling allows at most USD10 in the continuation even if an
old controller later spends its remaining allowance. Previous reservations are
never refunded, and duplicate old request hashes are refused in the new journal.

A further explicit increase uses a schema-2 declaration. Its `priorCampaign`
also pins the previous `campaignContinuation`, and `maximumPaidGenerations`
(an explicitly authorized positive integer) bounds new reservations, including
failed or uncertain attempts. The
combined ceiling must strictly increase; all earlier full allocations stay
reserved. Continuations must be disjoint and acyclic; there is no fixed ancestry
depth cap. Every ancestor's
request identities and hashes are protected against replay. For USD20 -> USD30
-> USD40, the last journal can reserve at most USD10; the preceding journals
remain valid and unchanged. Set its generation bound to the unused authorized
allowance after conservatively accounting for any still-active earlier grant.

The initial workflow and `run` / combined intake start path also accept the
optional pinned `campaignContinuation:{path,sha256}`. The exact pin is retained
in outer/workflow/onboarding grants and forwarded to implementation and validation.
Displayed campaign limits use the verified continuation ceiling. No field means
the historical schema-1 grant shape and default ceiling remain unchanged.

All new workflow, onboarding, outer-run and combined-intake state must be disjoint
from every ancestor campaign directory. The intake bridge refuses such overlap
before deriving intake or writing any retained inputs. Continued funding does not
reset old costs, delete journals, permit duplicate requests or authorize new paid
steps by itself. The added allowance is the difference between the new combined
ceiling and the prior fully allocated ceiling; prior measured usage is not a
refund. A project may present that new allowance separately without discarding
its original cumulative accounting.

`resume-candidate` accepts this declaration as `campaignContinuation:{path,sha256}`
in a fresh exact grant. It first verifies the original producer against its
original accounting, then forwards the new funding through source review and
executed-evidence review. This does not replay an old attempt or regenerate its
completed candidate. Report recorded usage separately from reserved liability;
neither is an independently retrieved billing invoice.

`deliver-api` produces `proposal.json` in its own old/new edit format. It is not a retained `run-local-intake` proposal and cannot be passed directly to `prepare-local-candidate`. Applying and testing its reviewed edits remains a separate operation. Local Codex and Codex Cloud delivery are not included in this download.

The development source now has a separate local `deliver-codex` proposal adapter
and explicit backend/agent-host routing; see [DELIVERY-BACKENDS.md](DELIVERY-BACKENDS.md).
This does not retrofit the published beta.12 download or change the Responses API
workflow into a Codex workflow. Cloud dispatch remains unimplemented and refuses
before usage. API campaign accounting and Codex account usage stay distinct.
