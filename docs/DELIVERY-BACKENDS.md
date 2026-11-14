# Delivery backend and agent host

This source branch adds `delivery-route` and a local `deliver-codex` proposal
adapter. The public beta.12 download predates these commands. Build a new private
controller before using them; never modify a retained installation in place.

Backend is the model delivery mechanism. Execution host is where that adapter and
agent run. They are separate choices, and neither grants project testing or release
permissions. For example:

```sh
python3 aim-flow.py delivery-route --backend codex-cli --execution-host local
python3 aim-flow.py delivery-route --backend codex-cli --execution-host cloud
```

| Backend | Agent host | Implemented route | Cost source |
| --- | --- | --- | --- |
| `responses-api` | `local` | Existing `deliver-api` proposal adapter | Existing API campaign |
| `codex-cli` | `local` | New `deliver-codex` proposal adapter | Saved ChatGPT/Codex account usage |
| Either | `cloud` | Blocked before dispatch | No fabricated usage or charge |

`available` means the adapter exists, not that authentication, source coverage,
execution, acceptance or deployment has passed. Cloud selection exits 3 with
concrete prerequisites; it never falls back to the local API worker.

## Local Codex proposal

Use a pinned Codex executable and an existing ChatGPT login on the selected host.
The installed CLI must support `exec --json --output-schema --ephemeral` and
`--ignore-user-config`. The model and reasoning setting are explicit, subject to
the account's actual access. Flow does not log in, copy auth caches, buy credits,
or retry with an API key. `codex login status` is checked without retaining its
output. API/provider environment overrides are not passed to the child. The
command forces ChatGPT auth and the OpenAI provider with user config ignored.

Configuration is pinned by SHA-256 and has exactly these fields:

```json
{
  "schemaVersion": 1,
  "manifestSha256": "NEW_PRIVATE_INSTALLATION_MANIFEST_SHA256",
  "delivery": {"backend": "codex-cli", "executionHost": "local"},
  "codex": {"path": "/absolute/resolved/codex", "sha256": "EXECUTABLE_SHA256"},
  "model": "EXPLICIT_ACCOUNT_SUPPORTED_MODEL",
  "reasoning": "high",
  "requirement": {"path": "/private/input/requirement.md", "sha256": "SHA256"},
  "source": {
    "checkout": "/absolute/project", "commit": "EXACT_HEAD_COMMIT",
    "git": "/absolute/resolved/git", "gitSha256": "SHA256"
  },
  "allowedDirectories": ["src/", "scripts/", "docs/features/"],
  "dispatchDirectory": "/private/codex-campaign/dispatch",
  "timeoutSeconds": null
}
```

Optional `intakeLimits` replaces the entire existing positive-integer intake
limits object. `null` timeout means no Flow deadline; a positive integer sets an
explicit operator deadline. State, dispatch ledger, controller and project must
be disjoint. Inputs must be outside mutable state. Always reuse the same dispatch
ledger for the campaign, including after switching backend. Preserve existing
API journals separately; never reset them or claim Codex consumption as a refund.

```sh
python3 /private/controller/aim-flow.py deliver-codex \
  --config /private/input/codex.json --config-sha256 SHA256 \
  --state /private/codex-campaign/attempt-1 --preview
# Review the exact intent; only the authorized call consumes account usage:
python3 /private/controller/aim-flow.py deliver-codex \
  --config /private/input/codex.json --config-sha256 SHA256 \
  --state /private/codex-campaign/attempt-1 --authorize-intent INTENT_SHA256
```

Preview performs committed-source discovery with the original requirement and
broad directory boundary; it never reuses a previous card's per-file map. It
checks the controller and selected source pins without any model call. Incomplete
coverage stops before account usage with the concrete omitted paths. Source
limits are configurable; heuristic discovery is not semantic completeness.

Dispatch durably marks the source-bound request before one new Codex turn. The
same request is refused in another state directory in that ledger. Interrupted,
failed, timed-out or unconfirmed attempts remain unknown and are never retried
automatically. Do not delete an attempt or change the ledger to bypass that fence.

Codex receives the selected committed context in stdin and a read-only sandbox
with a private state directory as its working directory. Flow does not grant
project commands, writable project access, deployment or publication. This is a
trusted local account adapter, not a confidentiality boundary against the host's
own filesystem or Codex tools. Tool activity is retained in JSONL and reported;
it is never promoted to a Flow project-test receipt.

Flow validates strict summary/old/new edits against selected editable sources,
preserves raw JSONL, final answer and stderr, and writes a separate Codex proposal
and receipt. An empty edit list reports `source-context-blocked` with the model's
explanation. Successful proposal generation is not candidate application,
independent review, feature acceptance or delivery. Codex token usage and thread
ID are recorded; USD amount is **unknown (`null`)**, never zero. No API budget
file is opened or changed. Codex account limits/credits still apply.
Use the same command/config/state with `--verify` to recheck a completed receipt,
its raw outputs and current source/controller pins without invoking Codex. A
missing or changed result is refused; verification never restarts a turn.

This command does not replace `run`, `work`, the API review/repair chain or an
existing queue watcher. Those still use their documented API contracts. A queue
owner must explicitly adopt the new route and bind proposal application,
verification and release before claiming autonomous Codex delivery. A generic
error should include the retained Flow status and source-context explanation;
changing a customer's queue is outside this adapter.

## Cloud prerequisites

The CLI exposes `codex cloud exec --env ENV_ID`, but Flow currently has no verified
cloud submit/status/diff adapter or environment binding. A real integration must
establish repository access, exact revision and a permitted environment, plus
single-dispatch recovery and a source-bound returned diff. Local checkout paths,
SSH release credentials, account login and runtime tools do not automatically
exist there. Never invent an environment ID or advertise a local worker as cloud.
No cloud task is submitted by these commands.

Official contracts checked for this implementation:
[non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode),
[authentication](https://learn.chatgpt.com/docs/auth), and
[developer commands](https://learn.chatgpt.com/docs/developer-commands).
