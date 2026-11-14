# Delivery backend and agent host

Beta.13 includes `delivery-route`, the local `run-codex` workflow and the
proposal-only `deliver-codex` adapter. Earlier downloads predate these commands.
Install a fresh private controller; never modify a retained installation in place.

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
| `codex-cli` | `local` | `run-codex` workflow or `deliver-codex` proposal | Saved ChatGPT/Codex account usage |
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

## Native implementation-to-delivery workflow

`run-codex` differs from the proposal-only `deliver-codex`: it
orchestrates implementation, a fresh independent source review, the selected
project-owned local tests, an executed-evidence review, and bounded repair rounds.
Its model stages have native Codex JSONL receipts, distinct thread identities,
usage and replay fences. They are not represented as historical API receipts.
The shared source-citation and finding-materiality validators remain in force.

The model backend remains independent of the agent host. This adapter currently
runs locally on a POSIX host with an existing eligible Codex login. Test recipes
run with ordinary host/network privileges in a private committed copy; this is
not a hostile-code sandbox. The original checkout and API cost history are not
execution workspaces. Codex model phases stay read-only.

Use configuration format `aim-flow-codex-local-run`, schema 1. Start with the
source/model/login/manifest fields from the proposal configuration above, then add:

```json
{
  "format": "aim-flow-codex-local-run",
  "python": {"path": "/absolute/python3.11-or-newer", "sha256": "SHA256"},
  "maximumRounds": 2,
  "maximumCodexTurns": 6,
  "testTimeoutSeconds": null,
  "testCatalog": {"path": "/private/input/project-tests.json", "sha256": "SHA256"},
  "tools": {"node": {"path": "/absolute/resolved/node", "sha256": "SHA256"}},
  "environment": {"LC_ALL": "C"},
  "outputBytes": 32000000,
  "artifactBytes": 8000000,
  "runtimeReview": "Explicit review of this trusted project's exact recipes, lifecycle hooks, host effects and prerequisites."
}
```

The examples' limits are operator choices, not hidden product defaults. Both
model and local test timeouts can be null. The optional `reviewMaxSourceBytes`
uses the existing source-review bound validator; no context is silently truncated.
Source discovery includes committed catalog definition files, and source review
uses compact unchanged context. A separate native criterion document retains the
original requirement text; the model cannot invent a new mandatory test method.

The test catalog is source-bound configuration, selected before claiming a queue
item. It is not arbitrary commands supplied by a model:

```json
{
  "schemaVersion": 1,
  "minimumTests": 1,
  "maximumTests": 2,
  "requiredTests": ["focused-feature"],
  "stages": [{
    "id": "focused-feature",
    "kind": "test",
    "root": ".",
    "tool": "node",
    "arguments": ["scripts/focused-feature-check.mjs"],
    "needs": [],
    "definitionSources": ["package.json", "scripts/focused-feature-check.mjs"],
    "artifacts": [{"path": "output/feature-check.json", "mediaType": "application/json"}]
  }]
}
```

`install`, `test` and `build` are the only stage kinds. A recipe names an already
pinned tool and exact literal arguments. For npm, include the pinned npm CLI as
an explicit Node argument and all source-supported lifecycle definitions in the
reviewed catalog. Declare any required install/build predecessors through `needs`;
dependencies must precede their consumers. The source reviewer chooses the smallest
sufficient subset within the reviewed policy, including mandatory tests. Each
selected stage and dependency executes once per candidate. No general full suite
is added automatically. A changed candidate can require a new focused test run;
prior tests are not silently accepted against changed source.

Recipes and all changes are disclosed to the independent source reviewer before
host execution. Test stdout/stderr, exact argv, tracked input identities and
selected PNG/JSON artifacts are retained. An executed-evidence Codex turn receives
actual JSON bytes and PNG attachments, with explicit evidence identities. It
cannot satisfy a criterion by citing an unexecuted test. The evidence review
reports limitations and preserves source-review follow-ups. Genuine blockers
cause a new candidate within the already granted rounds/turns; unknown or
interrupted outcomes never authorize an automatic replay.

```sh
python3 /private/controller/aim-flow.py run-codex \
  --config /private/input/native-run.json --config-sha256 SHA256 \
  --state /private/codex-run
# Consume the exact preview once:
python3 /private/controller/aim-flow.py run-codex \
  --config /private/input/native-run.json --config-sha256 SHA256 \
  --state /private/codex-run --authorize-intent INTENT_SHA256
# Completed-run verification is read-only and invokes no model/project commands:
python3 /private/controller/aim-flow.py run-codex \
  --config /private/input/native-run.json --config-sha256 SHA256 \
  --state /private/codex-run --verify
```

`--continue` may advance an existing granted run only through its original scope;
it does not replay a consumed stage or enlarge an allowance. A structured blocked
response includes a reason and the last retained phase. `progress.json` reports
local phase progress without pretending to know monetary cost or available plan
quota. Terminal results and original stage receipts remain immutable.

A completed native local run is accepted by the existing `deliver` command using
the normal `localRun` config/state/grant/result pins. The candidate producer kind
is `codex-workflow`, independently verified by the fixed installed
`run-codex --verify` command. The existing authenticated flat verification closure
can be reused only while its actual input bytes, directory observations and Git
queries remain unchanged. No historical recursive model/test execution is run.

Remote publication is a separate exact grant: the existing create-only branch
and draft PR adapter, exact-head checks/merge, exact main-push CI/artifact checks,
and project-owned release/production commands keep their original contracts.
`continue-release` accepts the resulting normal delivery handoff. A local
Codex success never claims those remote or production steps have happened.
A queue integration must explicitly opt into this new configuration, bind its
own test catalog and normal release policy, and preserve existing paused items
and cost journals. The legacy API `run` command never silently switches backend.

## Authentication, eligibility and actual cost evidence

This implementation invokes the user's installed Codex CLI with its existing
ChatGPT login. Flow does not export, parse, retain or refresh OAuth access/refresh
tokens itself; credential renewal stays with Codex. Missing, revoked or expired
login/account access stops the stage without an API-key fallback. Installation
or a model catalog does not prove entitlement; only a successful real inference
turn demonstrates that request's access. Local synthetic CLI tests prove protocol
and refusal behavior, not access to a user's account or an allocation of quota.

This CLI route is separate from registering AIM Flow as its own Sign in with
ChatGPT application. The documented app integration requires an explicit user
permission for plan usage and a distinct registration/sign-in lifecycle. It is
available to eligible open-source/local personal tools, with restrictions on
remotely hosted/commercial uses. AIM Flow's public MIT/local design appears to fit
the documented category, but availability and the user's account remain separate
checks. No first-class Flow OAuth connection or quota dashboard is claimed here.

The documented app-server integration is a supported future option for such a
registered connection; Flow currently uses `codex exec`, not a manually copied
OAuth token or undocumented endpoint. We do not label the CLI's token counters
as an invoice, remaining quota, guaranteed subscription inclusion or free usage.
API campaign accounting stays a separate cost source, and switching to it still
requires its existing explicit funding/credential authority. No automatic credit
purchase, reset or paid API failover is implemented.

Sources: [Sign in with ChatGPT cookbook](https://developers.openai.com/cookbook/articles/sign-in-with-chatgpt),
[Codex app-server integration](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server),
and the CLI/authentication references above. Documentation establishes supported
integration patterns; it is not evidence that this particular account completed
a live turn or that a specific usage bucket was debited.


## Phase gates and continuation without repeating model work

The independent source reviewer has a separate `executionGate`. Demonstrated
source defects, missing source requirements and unsafe/unapproved runtime
prerequisites block before tests. A test not having run yet is pending execution
verification; it cannot be a prerequisite for collecting that very evidence.
The original source review and its raw blockers remain intact. Every pending
source uncertainty/coverage item receives an exact executed disposition; resolved
items need actual evidence IDs. Unresolved essential items still prevent delivery.

A verified legacy pre-execution stop can continue through a new exact grant with
optional `resumeCandidate`. It pins the original installed controller, local run,
grant/result, every explicitly deferred item ID and an explanation. The fixed
original verifier must reconstruct its original blocked result; no raw review or
candidate is edited. Concrete source defects and explicit blocked safety gates
cannot use this route. Source, model, tools, test policy, runtime scope, account
ledger and original requirement must remain identical. The new workflow is
limited to one continuation round and the original unused Codex-turn allowance.
It reuses implementation/source-review receipts, runs only the never-started
reviewed tests and performs the remaining fresh executed review. Combined usage
and all prior model-stage references stay in the result. This is not a way to
replay failed or unknown model/process outcomes, expand permissions or reset cost.

An isolated live qualification completed three genuine Codex CLI turns, one
focused Python check and retained JSON evidence. Its two earlier model receipts
were reused under the verified continuation; final read-only verification left
all three dispatch records unchanged. This demonstrates local CLI/account access
and the narrow fixture's native pipeline. It does not qualify arbitrary projects,
Node/browser/PNG execution, cloud, hosted delivery, production or a billing bucket.
Remote adapter contracts were exercised separately with simulated GitHub effects.

Local native Git preparation also defaults to no Flow deadline. Optional `localGitTimeoutSeconds` sets a positive explicit deadline without changing historical API grants. Runtime and permission prerequisites that source edits cannot resolve stop before another implementation turn.


## Complete-input preflight (beta.14)

Implementation context maps are disjoint: `editableSources` contains every exact
selected writable file; `contextSources` contains the remaining exact selected
context. Their union is unchanged. Neither requirements nor source text are
truncated to fit. Both `run-codex` and `deliver-codex` use this representation.

The installed CLI's `turn/start` transport rejects text inputs over 1,048,576
characters with `input_too_large`. Flow checks the complete serialized UTF-8 JSON,
including escaping, instructions, metadata, diagnostics and evidence, before
invoking the account CLI or writing that phase's dispatch marker. This is a
transport character limit, separate from source byte allowances or token quotas.
The source-review and executed-evidence phases each receive the same check when
their actual input exists; a startup preview cannot predict future model answers
or project output.

The workflow preview exposes `intent.firstModelInput`; proposal previews expose
`intent.modelInput`. These contain exact serialized characters, UTF-8 bytes, the
CLI maximum and SHA-256. Stage intents retain the corresponding `modelInput`.
An oversized first request fails before creating an attempt or consuming a turn.
A later oversized phase reports its exact size before that phase's dispatch;
earlier model work and tests remain preserved. Review the project context scope
or separate the task explicitly rather than dropping evidence automatically.

Install a fresh beta.14 controller for this change. Never rewrite an older
attempt, its controller, request or dispatch journal, or treat an earlier failed
CLI start as an unused/refunded turn. Queue owners retain their cumulative
authority when explicitly creating a new attempt; this fix restarts nothing.


## Phase-focused context and read-only expansion (beta.15)

Selected provenance is no longer synonymous with text that must be sent inline.
The Codex routes retain every selected exact source in private per-turn `context`
files. `contextIndex` binds each logical path to SHA-256, original line count,
editable authority, display reason and an exact `fullSource` path/hash. These
files are made before model dispatch and verified unchanged with the attempt.
Codex may inspect these supplied files read-only when initial context is
insufficient; this grants no project execution or additional editable paths.

The inline view uses the original tree-wide intake ranking for primary code,
not a new ranking of the smaller already-selected subset. Leading related code
is displayed whole. Literal callers/imports and selectors used by primary
components receive surrounding line-addressed excerpts. Generic entrypoint
names such as `index` do not connect unrelated files. Relevant exact test recipe
definitions and project instructions remain explicit. Original requirements and
actual runtime evidence are not shortened by this policy.

Locks and unrelated process code are normally hash-bound inventory entries,
not full implementation prose. A dependency/lockfile requirement displays lock
content, and every changed source is displayed whole during independent review.
This is role/relevance selection, not a file-size ban or proof of semantic
completeness. The complete selected files remain available even when omitted
from the initial view. Existing committed-source discovery bounds still apply.

Excerpts declare original inclusive line ranges. Omitted lines are never claimed
to be adjacent; citations are checked against the retained exact full review
sources. Patch replacements are validated for unique old text against complete
committed editable files, including a source inspected on demand. A directory
grant is not expanded by a display choice.

Startup previews expose `firstModelContext` (or proposal `modelContext`) with
whole/excerpt/on-demand counts and full/displayed source character counts. The
complete serialized initial request still receives the beta.14 transport check.
Model raw output and tool activity remain native evidence, not project tests.
Missing or changed expansion files invalidate verification, including cached
proof. A fresh controller and new exact grant are required; older attempts are
not rewritten or automatically retried.

Focused tests exercise private expansion files using a synthetic local CLI and
real Git/project commands. Read-only previews use retained real inputs. No new
live inference, production, cloud or billing qualification is claimed for this
context change.
