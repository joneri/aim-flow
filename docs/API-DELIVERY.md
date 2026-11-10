# Sol API proposal delivery

`aim-flow.py deliver-api` is included in the beta.9 download as a separate, proposal-only route for `gpt-6.1-sol` with `xhigh` reasoning. It does not apply edits, run project code, accept a product, deploy, or retry. `run-local-intake` retains its existing pinned mini model and historical budget rules. The local Codex adapter remains separate.

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

Verify and install the beta.9 controller with its matching bootstrap and independent pins. Its archive uses schema 3 with an exact 61-file payload, including `aim-flow.py`, this guide and the Sol adapter. Older installations and their original matching bootstraps remain intact; install to a new directory rather than overwriting one. The matching bootstrap intentionally refuses older schema 2 and beta.8 schema 3 inventories. Beta.8 has 53 payload files; this release has 61. Schema equality alone does not imply archive compatibility.

Run `/absolute/path/to/new-private-controller/aim-flow.py deliver-api` with the arguments above. The installed command uses only its packaged modules; no source checkout, Git or Codex CLI is required. Preview requires an existing established budget history, selected source and request files, but no API key or network. This advanced route does not initialize a fresh campaign budget or replace the simpler mini intake.

Keep the original campaign and authority files when upgrading. A new installation changes implementation pins, so preview the next operation again and authorize its new exact intent. Never edit an old receipt or reset an attempt to make it match a new version. Use the original installation to inspect an older retained operation.

`deliver-api` produces `proposal.json` in its own old/new edit format. It is not a retained `run-local-intake` proposal and cannot be passed directly to `prepare-local-candidate`. Applying and testing its reviewed edits remains a separate operation. Local Codex and Codex Cloud delivery are not included in this download.
