# Flow-owned release continuation

`aim-flow.py continue-release prepare-checkout` previews a fresh-main preparation.
Its pinned config contains `handoff` (the immutable delivery-chain result pin),
`git` (executable pin), `authentication` (null or the scoped transport below),
and canonical new `checkout` and `state` paths. Supply
`--config`, `--config-sha256`, and the installed `--manifest-sha256`. Execution
requires the returned `--authorize-intent` hash. This grant does not deploy.

Flow reconstructs the upstream local acceptance and delivery grant, compares
retained publication and merge identities, and requires the official merged tree
to equal the entire reviewed candidate tree. The pinned Git fetches the exact
merged SHA into a new main repository. Flow reads hash-verified Git blobs directly,
not checkout filters. Symlinks/submodules are unsupported by the host-command
adapter. Existing paths and unknown attempts are never adopted or replayed.

The HTTPS Git transport has no ambient configuration. `authentication: null` uses
anonymous HTTPS. For a private repository declare `gh` (executable pin), `home`,
`configDirectory`, and `timeoutSeconds` in `authentication`. The preview pins and
discloses these references. Git uses the fixed official `gh auth git-credential`
protocol, as the publication adapter does, restricted to the exact GitHub origin;
no caller-supplied helper command, token copying, or credential file reads. TLS
verification remains enabled and redirects are disabled.
This command does not install dependencies or copy local dist. Runtime preparation
must be handled explicitly by the approved project workflow.

`aim-flow.py continue-release release` accepts a pinned config containing
`handoff`, `checkoutReceipt`, `contract`, `declaration` (project-command declaration
pin), and a new command `state`. Preview returns the existing project command's
exact intent hash. Execution requires that distinct hash. The command declaration
pins the handoff, checkout receipt and release contract in `inputs`, so the command
adapter rechecks their bytes at dispatch and completion.

## Reviewed project contract

The contract uses format `aim-flow-project-release-contract`, schema version 1:

- `handoff`: original readiness result pin.
- `command`, `scope`: exact objects from the project command declaration.
- `selectionPolicy`: `handoff-exact` or `project-refresh-same-commit`.
- `preEffectChecks`: ordered list `commit`, `runId`, `runAttempt`,
  `artifactIdentity`, `artifactBytes`.
- `sourceCitations`: committed project path and SHA-256 pairs documenting and
  implementing those checks. Flow verifies bytes, not the truth of arbitrary prose.
- `protectionObligations`: explicit reviewed project obligations. Empty is valid
  only when the reviewed project contract actually has none.
- `unresolvedProtectionObligations`: must be empty to dispatch.
- `protectionEvidence`: entries with `obligations` and `verification` arguments
  for the existing project's `verify-project-command` verifier. Each verified
  result must be `project-receipt-matched`, and its result pin must also appear in
  release command inputs. Coverage must match the declared obligations. Operator
  notes saying a backup exists do not substitute for verified command records.
- `receiptPaths`: project-specific JSON paths for `commit`, `runId`, `runAttempt`.

The exact policy requires matching assertions for the handoff's commit/run/attempt.
The refresh policy permits the trusted project release command to freshly select a
successful run for the same merged SHA, pin that selection before any upload, and
verify the selected artifact and manifest at use. The old handoff remains a readiness
snapshot and is never presented as evidence of the artifact actually uploaded.
The actual run/attempt is returned from the new operational receipt. Both policies
require a commit assertion. A matching operational receipt does not prove feature
acceptance or atomic rollback.

The contract is an explicit review/authorization input, not a semantic proof of
project code. Its provenance assertions and backup evidence mean only what the
reviewed project commands actually checked. Unknown production baseline/backups
remain blocked obligations. Flow can run separately granted source-cited project
commands to establish them when the project supports that work; manual deployment
is not a substitute pilot.

## Boundaries

No project-specific command, repository, destination, frontend selection, secret,
or rollback procedure is embedded in core. The normal project release entry point
retains responsibility for mandatory aggregate checks, exact source/current-main
checks, fresh CI/artifact/manifest verification, scope/dependency expansion,
transport and post-upload HTTP checks. Unsupported authentication or missing
project protection commands are honest blockers, not permission to bypass policy.

The bridge does not perform production feature acceptance, automatically retry an
unknown effect, promise atomic deployment, or invent a production baseline.
Retained local evidence is not independent authentication. Current implementation
expects the upstream chain and release bridge in the same immutable installation.

Offline tests use synthetic repositories and fake network boundaries. Git's runtime
version observation is explicitly faked in these unit tests; they do not qualify an
older Git for actual production operation. No production project command is run.
