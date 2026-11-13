# GitHub merge and main-push handoff (development)

`aim-flow.py deliver-github` is an installed, typed adapter. It does not run a
model, interpret a command from project content, edit a PR body, download an
artifact, dispatch/rerun CI, bypass protections, push a branch, or deploy. Project
choices are a separately pinned declaration, never built-in repository names,
workflow names, job names, endpoints or release rules.

## Contract and authorization

Use a freshly built and verified installation. The JSON declaration has exactly
these fields (`sha256` values are lowercase SHA-256; Git commits use 40 lowercase
hex characters):

```json
{
  "format": "aim-flow-github-delivery",
  "schemaVersion": 1,
  "manifestSha256": "<installed controller manifest digest>",
  "repository": {"name": "owner/project", "id": 123},
  "pull": {
    "number": 1,
    "headSha": "<reviewed head commit>", "headRef": "feature/example",
    "baseSha": "<observed base commit>", "baseRef": "main",
    "markDraftReady": true
  },
  "requiredPrChecks": [{"name": "Build", "appId": 12345}],
  "requiredPrStatuses": [],
  "workflow": {
    "path": ".github/workflows/release-readiness.yml",
    "sha256": "<independently reviewed workflow bytes>",
    "requiredJobs": [{"key": "frontend-build", "name": "Frontend build"}],
    "artifact": {"nameTemplate": "frontend-{sha}-{attempt}", "retentionDays": 7}
  },
  "evidence": {
    "aggregate": {"path": "/private/review/aggregate.json", "sha256": "<digest>"},
    "browser": {"path": "/private/review/browser.json", "sha256": "<digest>"},
    "independentReview": {"path": "/private/review/review.json", "sha256": "<digest>"}
  },
  "transport": {
    "gh": {"path": "/absolute/resolved/path/to/gh", "sha256": "<executable digest>"},
    "home": "/declared/existing/home",
    "configDirectory": "/declared/existing/gh/config",
    "timeoutSeconds": 180
  }
}
```

The local files must be canonical, regular, unlinked inputs. The `gh` binary is
independently reviewed and pinned. The adapter invokes only `gh api --hostname
github.com` with constructed REST routes or its one fixed ready-for-review
GraphQL mutation. It uses declared HOME/GH_CONFIG_DIR and existing gh auth. It
does not read token values or pass ambient token/debug/config overrides. The gh
process itself necessarily uses its existing authentication transport. No login,
credential creation, settings change or arbitrary command bridge is included.

Evidence files are required and byte-pinned, but their JSON claims are not proof
of genuine execution, genuine review, or user authorization. An independent
reviewer must validate the aggregate, browser coverage, source bindings and
actual user grant. The exact preview hash authorizes only the disclosed
operation in the named private state directory. It is not an authority token
against an attacker with the same local user privileges.

1. `deliver-github --config DECLARATION --config-sha256 SHA --state NEW_DIR --preview`
   reads local pins only, performs no network call and writes no state.
2. Independently inspect that preview and its evidence. If authorized, pass its
   exact `intentSha256` using `--authorize-intent SHA` instead of `--preview`.
3. If CI is pending or a transport outcome is uncertain, use the same config,
   SHA and state with `--resume`. This can only read GitHub and retain local
   observations. It never marks ready, merges again, rolls back or reruns CI.

Marking a draft ready is an explicit part of the preview when `markDraftReady`
is true, and can notify reviewers. False requires an already non-draft PR.
Only an ordinary merge commit is supported; no squash/rebase/admin/force or
protection bypass option exists. Same-repository heads are required. GitHub
server protection decisions still apply. A successful main-push result is an
input to the project's usual release process, not permission to replace it.

## State, races and failures

The state directory is exclusive. Intent and declaration are fsynced before
any external observation; each effect gets an exclusive, fsynced dispatch
reservation before the request. All complete API pages and external error
messages are retained privately. Metadata is bounded per response (8 MiB),
per call (60 seconds), and per observation invocation (1..300 declared seconds).
There is no file-count or project-size limit. Pagination checks totals and
unique identities, refusing unstable or incomplete results. Invocations hold
an exclusive local lock. There is no automatic transport retry.

Before effects the adapter verifies repository ID/name, PR number, same-repo
head/base refs, head/base commits, draft state, GitHub mergeability, all returned
latest check conclusions and status contexts, configured required contexts,
and remote base. Every current check/status must be successful. The final
non-draft preflight additionally requires `mergeable_state: clean`. Pending,
failing, missing or ambiguous checks stop the attempt. The merge request uses
GitHub's atomic `sha` guard for the exact head, then reconciliation checks the
actual merged SHA, both ordered merge parents, its official Git tree SHA, and exact remote main.
The generic adapter observes the tree; the accepted-local-work chain additionally
requires it to equal the accepted candidate tree before any release handoff.

**GitHub has no atomic expected-base merge guard.** Base is reread immediately
before the merge and verified in the resulting parents. A concurrent base or
check change between reads and the merge is subject to GitHub protections.
A concurrent base change can cause an actual merge but never a successful
handoff here. Post-merge mismatches remain stopped/not-ready; there is no
rollback or second merge. This is not a transaction spanning GitHub and release.

Any failed effect stops immediately with `effect-outcome-unknown`, even if an
HTTP rejection looks conclusive. The official error is retained. Read-only
resume reconciles remote PR/parents/main before proceeding. An open PR yields
`merge-not-observed-no-retry`; absence of a merge at one instant is not proof
that an earlier request will never complete. A merge not reserved by this Flow
attempt cannot pass this handoff. A reserved request followed by matching remote
state also cannot prove who performed the merge: receipts explicitly distinguish
`mergeResponseConfirmed` from read-only reconciliation after an unknown request,
and preserve the attribution uncertainty. A known successful ready step
followed by a failed preflight also stops, without silently merging later.

## Exact CI and artifact handoff

A merged PR or green PR checks are not main-push release evidence. The adapter:

- Re-reads and hashes workflow bytes at the merged commit.
- Resolves the exact active workflow and observes only its `push` runs for the
  exact merged SHA/base branch; zero is pending and multiple are ambiguous.
- Durably pins the first observed run ID and run attempt. A rerun or substitute
  run requires independent review, never silent acceptance by resume.
- Retrieves all pages of attempt-specific jobs, validates SHA/run/attempt,
  and requires exactly one complete successful job for each configured name.
  `declaredKey` comes from the reviewed declaration: GitHub jobs metadata exposes
  display names, not an independently authenticated YAML job key.
- Retains all artifact pages, selects the exact SHA/attempt name, and verifies
  workflow-run/repository identity, nonzero size, nonexpiry and declared
  retention (one-minute timestamp tolerance). The artifact ID, optional API
  digest, expiry and full metadata are retained. Bytes are not downloaded or
  independently hashed; `artifactBytesVerified` remains false.
- Rechecks remote main and the current run attempt before success.

Versioned immutable `result-NNNNNN.json` receipts have format
`aim-flow-github-main-push`. Successful status is `main-push-verified`, with
`declarationSha256`, `intentSha256`, repository, pull/head/base/merged/main SHA,
workflow path/hash/ID, run ID/attempt, required job observations and exact
`artifact`. `evidenceFiles` pins all retained request/response pages and dispatch
records. `awaiting-main-push` is resumable, not release-ready. Every result has
`releaseExecuted: false` and makes the evidence-authentication limit explicit.

The downstream release declaration should pin the successful receipt and its
expected values. The project's release command must recheck current exact main,
push run/attempt/artifact and keep its own upload and HTTP-result guard. A pinned
handoff cannot prevent state changing between observation and consumption.

## Verification boundary

`tests/test_github_delivery.py` uses mocked official API replies and actual local
state files. Installed smoke tests exercise the copied module and CLI without a
source import. These tests establish local validation, durable transitions,
no-retry behavior and generated argument contracts. They are not live GitHub,
branch-protection, mark-ready, merge, hosted-CI, artifact-download or deployment
qualification. No external request or real credential read is needed.

This adapter is a development component only. Passing its offline tests, or
using a previously operator-prepared PR, is not a full Flow pilot. A full pilot
must separately show Flow onboarding, plan creation, requirement-to-code work,
validation and the authorized delivery chain; none is inferred from this module.
