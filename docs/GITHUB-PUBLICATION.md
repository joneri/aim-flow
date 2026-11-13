# Exact candidate publication (offline development)

The installed `publish-github` adapter creates a new branch and a new **draft**
PR from an exact local committed candidate. It never edits an existing PR, changes
its description, reuses a pre-existing branch, merges, closes a PR, deletes a
branch, reruns CI, or deploys. A higher Flow workflow supplies exact pinned PR
text from the user's requirement and actual verified results; the bridge neither
invents verification claims nor requires a human to author routine PR text.

A typed declaration binds the installed controller, repository ID/name, pinned
Git/gh executables, candidate checkout/base/head/tree/full binary diff, unique
branch/base names, title/body bytes, aggregate/browser/review/candidate evidence,
and the existing gh HOME/config authentication transport. The complete candidate
must be one direct child commit of the exact baseline, or one verified Flow
repair of that child. The repair path independently checks both single-parent
links and preserves the full original-baseline diff. An explicitly granted
second repair retains and verifies all three single-parent links; recursive
repairs beyond that bound are refused. Dirty working-tree files
are never published. Source blobs, local Git configuration, hooks and model prose
are never executed to publish.

`--preview` performs only local verification and exposes exact text and effects.
`--authorize-intent HASH` creates exclusive durable state and performs at most one
branch push and one draft creation. Requests are durably reserved before effects,
observations/errors retained, and unknown effects never retried automatically.
`--resume` performs only reads and writes private observation receipts.

## New-branch race protection

The push uses a full SHA and one fully validated target ref, with the **empty
expected previous ref** `--force-with-lease=refs/heads/BRANCH:`. Despite its name,
this form cannot overwrite or rewrite any existing branch. It atomically creates
only while that ref is absent. There is no nonempty expected ref, leading `+`,
plain-force option, fallback push, or retry. Both preflight absence and the Git
`[new branch]` porcelain result are checked; an existing identical ref can produce
an up-to-date no-op, which is refused rather than claimed as Flow creation.

The controller constructs a fresh private bare Git view over the verified local
object store. Only its own minimal configuration is used, so source-repository
URL rewrites, hooks, credential helpers and push settings cannot redirect the
push. Global/system Git config and ambient Git/token/debug overrides are omitted.
TLS verification stays enabled and HTTP redirects are disabled. The fixed pinned
`gh auth git-credential` helper supplies existing authentication directly to Git;
credential values are not read or retained by this controller. Git's helper
protocol invokes a fixed, shell-quoted executable, not a declaration-supplied
command. No login, credential setup/save, security setting change or source
checkout write is performed.

## Draft creation and recovery

Official GitHub reads verify repository identity, exact current base and exact
published branch. Complete paginated PR lookup must find no existing PR before
creation. The only mutation is POST to that repository's pulls endpoint with the
exact title/body/head/base, `draft:true`, and `maintainer_can_modify:false`.
The resulting PR is read back and checked for exact source, base, title, body,
draft/open state and canonical URL. GitHub does not offer an atomic expected-base
guard for PR creation: base drift can leave a created draft but blocks handoff.
There is no automatic rollback, branch deletion, PR closure or description edit.

If a push is uncertain, read-only resume can establish that the exact branch is
now present. It cannot prove who created it. If no draft request was ever reserved,
resume exposes an exact `draftContinuationSha256`. Passing it with
`--authorize-draft-intent` authorizes only that still-unattempted draft creation,
after all guards are reread. It can never push or repeat an earlier draft request.
If draft creation was uncertain, resume only reconciles exact remote PR state.
Observing absence once never proves an earlier request cannot finish later.

Versioned `result-NNNNNN.json` has format `aim-flow-github-publication-result`.
Success is `draft-pr-verified`; `deliveryPull` is the exact pull declaration for
`deliver-github` (including explicit `markDraftReady:true` for its separate merge
authorization). The handoff must pin this receipt along with real validation and
browser evidence. `pushResponseConfirmed` and `draftResponseConfirmed` distinguish
retained success responses from unknown-effect reconciliation. Local hashes and
JSON claims do not authenticate causal provenance against the same local user.

## Verification boundary

All development tests use generated local Git fixtures, mocked GitHub responses,
actual durable state files and source-free installed CLI paths. A local bare
fixture can qualify create-only Git compare-and-swap behavior without GitHub.
These checks are not live branch protection, authenticated GitHub publication,
CI or full-pilot qualification. No existing project PR is used or edited.

## Declaration and producer verification

The declaration has exactly the following shape (all file pins are canonical
absolute paths with lowercase SHA-256; commits/trees use full 40-character SHAs):

```json
{
  "format": "aim-flow-github-publication", "schemaVersion": 1,
  "manifestSha256": "<publication installation manifest>",
  "repository": {"name": "owner/project", "id": 123},
  "source": {
    "checkout": "/private/flow/candidate", "baseSha": "<baseline>",
    "headSha": "<candidate>", "treeSha": "<candidate tree>",
    "diffSha256": "<complete Git binary/full-index/no-renames diff>",
    "git": {"path": "/resolved/git", "sha256": "<Git executable>"}
  },
  "branch": "flow/unique-candidate", "baseRef": "main",
  "pull": {"title": "Exact routine PR title", "body": {"path": "/private/draft.md", "sha256": "<body>"}},
  "evidence": {
    "candidate": {"path": "/private/producer-state/candidate-receipt.json", "sha256": "<receipt>"},
    "aggregate": {"path": "/private/aggregate.json", "sha256": "<receipt>"},
    "browser": {"path": "/private/browser.json", "sha256": "<receipt>"},
    "independentReview": {"path": "/private/review.json", "sha256": "<receipt>"}
  },
  "candidateVerification": {
    "controller": {"path": "/private/producer-install", "manifestSha256": "<producer manifest>"},
    "python": {"path": "/resolved/python", "sha256": "<Python executable>"},
    "config": {"path": "/private/producer-config.json", "sha256": "<producer declaration>"},
    "state": "/private/producer-state"
  },
  "transport": {
    "gh": {"path": "/resolved/gh", "sha256": "<gh executable>"},
    "home": "/declared/home", "configDirectory": "/declared/gh/config", "timeoutSeconds": 180
  }
}
```

The producer may be an earlier installation. Its complete inventory, executable
and config are pinned, with no unmanifested files or import directories. The
only producer command is its fixed installed `aim-flow.py work --config ...
--config-sha256 ... --state ... --verify` under the declared Python with `-IBS`.
No arbitrary verifier command is accepted. The command must report the exact
`candidate-prepared` receipt, checkout, source commit/tree/diff and private
publication-authority flag. The producer inventory and inputs are checked again
after verification. Its report has a 120-second, 8 MiB metadata bound. The Git
source check independently hashes the complete diff and raw commit/tree objects.
The Git/Python executable pins allow read-only OS hard links with exact bytes and
file identity checks; state, receipt and control files retain single-link rules.

For a repaired producer, `candidateVerification` additionally requires
`producerKind: repair-candidate` and the original `repairConfig` file pin.
`config` must pin that repair state's exact `derived-workflow-config.json`.
The fixed installed `repair-candidate --verify` bridge rechecks the original
producer, repair grant, paid response, edit scope, ancestry, assessment reuse and
all retained source bindings. A partial pair or unknown producer kind is refused.
The source/workspace/acceptance and new publication gates remain separate.

Candidate verification proves the retained workflow protocol and source binding,
not a real provider's authenticity, validation success, acceptance, or permission
to publish. Aggregate, browser and independent-review files are **caller-provided
pinned JSON attachments only** in this bridge: their source/pass semantics are
not verified here. The owning Flow workflow must validate the actual aggregate,
browser and review evidence and apply its authorization before dispatch. The
bridge receipt explicitly leaves validation authentication and acceptance false.

Offline source-free publication tests exercise a synthetic pinned producer
protocol fixture. A separate cross-component integration test uses the real
installed requirement-workflow verifier and actual Git candidate, with injected
provider/GitHub/push responses and explicitly synthetic aggregate/browser/review
attachments. It runs when the separately implemented requirement workflow is
present. Neither test is an end-to-end live pilot.
