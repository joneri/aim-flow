# One reviewed project command

`run-project-command` adds a bounded host-side driver for an existing project's
CI/release entry point. It does not implement merge, hosted CI selection, artifact
validation, transport or production verification. Those remain in the project's
reviewed tools. This route is separate from offline sandboxed validation: an
authorized command can use the network and operator privileges. The adapter
never authorizes itself from onboarding/model text, a hash or a prior test pass.

Use a separately reviewed project-local declaration, outside the installation
and preferably outside the checkout. The exact fields are:

- `format: "aim-flow-project-command-declaration"`, `schemaVersion: 1`.
- `target`: canonical `checkout`, full `commit` and `tree`; `git`: canonical
  executable `path` and `sha256`. All tracked regular-file bytes/modes must match
  the commit, and Git's tracked/untracked status must be clean. V1 refuses tracked
  links/submodules rather than claiming their targets are pinned. Ignored outputs
  are allowed; they are not approved sources or artifacts.
- `tools`: mapping of PATH executable names to canonical `path`/`sha256` pins.
  The selected executable and every tool needed on PATH must be included. There
  is no inherited PATH or ambient fallback. `trees` is a list of `path`/`sha256`
  runtime-tree pins using the existing Flow content/mode/link inventory. Include
  the complete npm installation for an npm entry point. `inputs` is a list of
  separate nonsecret file `path`/`sha256` pins for configuration not in Git.
- `command`: named `tool` and literal `arguments` array. No shell is added by
  Flow. For a normal npm release, select pinned Node with the pinned npm CLI path,
  `run`, the actual project script name, `--`, and its exact reviewed selections.
  npm still runs normal project hooks/scripts. Review those committed bodies and
  imports. Do not substitute a lower-level transport or skip a required gate.
- `environment`: explicit string settings. PATH is generated from tools; nothing
  is inherited. Never place credentials in this declaration, arguments, review,
  assertions or settings. Common secret variable names are refused, not secret
  content detection. Existing credential-store/config location references can be
  provided after separate authorization. Preview does not read those references.
  Runtime-tree and input pins DO read their files, so never select credential
  stores, private keys or credential-bearing config as those inputs.
- `environmentFile`: null or `{path, sha256, keys}` referencing one existing
  operator-private dotenv file. An authorized operator supplies its hash and exact
  selected field names separately. Preview does not read it; after the durable
  started marker, dispatch verifies bytes in memory and imports only those keys
  into the child environment. It never copies the config or dereferences key-file
  values. Values cannot override the explicit environment/PATH or common runtime
  bootstrap settings. The supported subset is one optional `export NAME=value`
  assignment per line, with POSIX literal quoting/escaping and comments, parsed
  without execution, variable expansion or command substitution. Selected fields
  must exist exactly once and be nonempty. Unsupported/missing/changed inputs
  refuse without launching the project. The full file hash is rechecked afterward.
  Optional `confidentialKeys` is a distinct subset of `keys`; omission treats
  every selected field as confidential. Classify exclusions explicitly in the
  review: public destinations and credential-file location references are not
  private-key bytes. Imported values never appear in the preview/intent.
  stdout/stderr redact exact confidential UTF-8 values in memory before writing, including across read boundaries. A
  completion record containing a confidential value (raw or JSON-decoded) is refused
  rather than copied. This is not universal secret detection: transformed values,
  unrelated secrets or project-created files are outside this narrow protection.
  Nonconfidential imported destination metadata may appear in normal command
  logs and the project record. Do not run code that prints credentials or include credential values in argv.
- `evidence`: nonempty list of `{path, sha256, assertions}`. These pin prerequisite
  records. Assertions use nonempty lists of object keys/array indices in `path`
  and exact JSON `equals` values. Type matters, including true versus 1. Their
  provenance and meaning require independent review; generic assertions do not
  independently authenticate hosted CI or replace a Flow receipt verifier.
- `scope`: `destinations` list and `selections` object of named string arrays.
  Explicitly disclose service/repository/production destinations, exact requested
  surfaces/files and exclusions. This is reviewed scope, not a sandbox. Any
  project-controlled dependency expansion and deletion semantics must be reviewed.
- `review`: nonempty `reference`, `effects`, `qualifications`, `prerequisites`.
  The latter three are nonempty string lists. State which merge/CI/evidence steps
  happened separately and which controls the selected project command invokes.
  These are review records, not proof of permission or completed prerequisites.
- `receipt`: repository-relative `directory`, single-directory `filenamePattern`,
  nonempty equality `assertions`, and human-readable `meaning`. Bind the exact
  source SHA, CI run AND attempt, selections, final status, upload/verification
  outcomes as appropriate to the project's own schema. Exactly one newly named
  matching record must appear. A pre-existing record, merely edited record,
  copied/renamed old bytes, empty record, stdout marker or exit 0 alone cannot count as completion.
- Positive per-command `timeoutSeconds` and combined-log `outputBytes` limits.

## Preview, execute, retain

Run the installed `aim-flow.py run-project-command` with `--manifest-sha256`,
`--declaration`, `--declaration-sha256`, a fresh canonical private `--state`
outside checkout/controller/inputs, and `--preview`. The complete intent includes
argv, nonsecret environment and private-field references, source/tool/config/evidence pins, scope, prior receipt
inventory, qualifications and prerequisites. Preview runs only pinned local Git
inspection and file reads, never the declared project command.

After obtaining appropriate permission for these concrete effects, use the same
arguments and `--authorize-intent DIGEST` instead of preview. Permission must
come from the operator/user workflow; knowing a digest does not grant it. An
exclusive attempt, fsynced parent directory and intent/started marker precede process launch.
Every existing attempt refuses redispatch. Do not delete state or use a new
state directory to bypass an uncertain outcome. The adapter has no retry mode.

Flow captures stdout/stderr, bounds execution and kills the process group on
completion, timeout or log overflow. It rechecks source/tools/config/evidence,
retains an exact copy/hash of the newly generated project receipt, and applies
the declared assertions. Successful exit plus intact inputs plus the matching
receipt yields only `project-receipt-matched`. Read that together with
`projectReceiptMeaning`, `operationsPerformedByFlow` and
`prerequisitesPerformedSeparately`. It is not an independent product-acceptance
claim. Any missing/mismatched receipt, interruption, nonzero exit, timeout,
output limit or post-effect drift remains `outcome-unknown`, exit 3 when a result
can be retained. An exception retains failure/started state. Partial effects may
already have occurred, even if the process exits nonzero. Reconcile those effects
with the project's operational record and destination before deciding recovery.

`verify-project-command` takes the same manifest/declaration/state pins plus an
externally retained `--receipt-sha256`. It checks the historical intent, started
marker, effect journal, logs and copied completion record without running the
command, reading credentials or requiring HEAD/outputs to remain unchanged.
Missing/corrupt/incomplete state fails verification; it never causes replay.
This v1 verifier cannot finalize an interrupted attempt from new remote evidence.

## Trust and coverage

This executes reviewed code on the host, not hostile code in a sandbox. The
command can read existing credential stores and mutate source, state or unrelated
files with the operator's permissions. Source/tool hashes and receipts are
local evidence, not attestation against a malicious same-user process. Ambient
OS libraries, absolute tool invocations, ignored dependencies/config, remote
services and credentials are not completely pinned by this driver. Review and
pin relevant nonsecret runtime/configuration closures; the project's guards must
verify actual release inputs at point of use. No local build output becomes an
approved production artifact merely because Flow launched a release command.

For a main-push artifact release, first complete and review the exact merge and
main-push CI identity separately. Bind the resulting MAIN SHA/run/attempt and
browser/aggregate evidence in a new declaration. Invoke the project's normal
release entry point with narrow selections. That tool must still repeat its
mandatory local aggregate, verify the immutable exact-run artifact, use guarded
transport and verify production HTTP. Flow does not provide a shortcut for any
of these steps, and a post-release browser acceptance check remains separate.

Offline tests execute only a local synthetic project command with JSON fixtures.
They do not qualify a production release or prove hosted CI/artifact semantics.

## Local runtime preparation without a project JSON receipt

Schema **2** uses the same exact-command, source, tools, prerequisite evidence,
private-environment, effects journal and no-replay contracts as schema 1, but
replaces `receipt` with:

```json
{
  "mode": "runtime-preparation",
  "outputTrees": ["node_modules"],
  "meaning": "Prepare the locked local dependencies; no release or deployment."
}
```

This is for reviewed local preparation such as `npm ci`, not a receipt-free
release mode. Select the real pinned npm command and reviewed lifecycle hooks;
ordinary network/installation permissions still apply. This driver remains a
host command, not a sandbox: the mode does not prevent a malicious command from
performing unrelated effects. Review must constrain the actual command to the
intended preparation.

Output trees are distinct, nonoverlapping, canonical repository-relative
directories, absent before the attempt. They must not overlap tracked source,
Git metadata, the controller, attempt state or pinned inputs/tools/runtime trees.
Declare ignored runtime directories so the unchanged-source check remains valid.
Existing installations are not adopted, deleted or replaced by this mode.

A successful process, unchanged tracked source (including lockfiles), unchanged
inputs/tools and nonempty measured output trees produces
`runtime-preparation-completed`. `runtime-preparation.json` retains the exact
process observation, source/lock/tool identities and complete output file/mode/
internal-relative-link inventories. The returned `preparedRuntimeTrees` contains
`path`/`sha256` pins ready for a subsequent command's `trees` declaration.

There is no `project-receipt-matched`, release, deployment or product-acceptance
claim. Nonzero exit, timeout, missing/empty outputs, unsafe output links or source
and tool drift leave `outcome-unknown`, preserving partial local outputs and
refusing replay. Reconcile those files before deciding a new authorized action.

`verify-project-command` verifies the historical preparation receipt, journal and
inventories without rerunning preparation or requiring outputs to remain present.
This is historical evidence only. A consumer must recheck the emitted tree pins
at point of use; the ordinary `trees` contract does that. A changed dependency
tree cannot be legitimized by a successful historical preparation receipt.

## Completed runtime inventory reconciliation

Dependency runtime files may have internal hard-link aliases (for example
esbuild's two binary paths after npm installation). Runtime tree identity pins
all bytes/modes and alias topology, rejects aliases outside the declared tree,
and keeps private control files subject to the original single-link rule.

`reconcile-runtime` is a separately granted, read-only recovery for a retained
schema-2 runtime process that exited successfully but whose original inventory
failed on that ownership rule. It invokes the pinned original controller's
historical receipt verifier, rechecks unchanged source/tools/input evidence,
measures the current dependency tree and writes a new reconciliation receipt.
The old result/journal remain unchanged. It cannot relaunch installation, retry
an unknown process, accept a nonzero exit, or claim deployment/product acceptance.
Current measurements are explicitly distinct from an unavailable earlier output
snapshot. Consumers must continue to verify their actual runtime at use.

## Symlink-sensitive Git helper lookup

Private PATH tool aliases change argv0 for some executables. On macOS Git can
then derive a nonexistent libexec directory beneath the command state. For new
controllers, project-command preparation derives GIT_EXEC_PATH by querying the
same canonical pinned Git used for source discovery, records the helper prefix
and pins the relevant available protocol/pack helper binaries. An explicitly
configured prefix is retained. This prevents the isolated PATH from breaking
Git HTTPS before the project release starts; it does not open the ambient PATH.
Existing installed controllers/grants remain unchanged. Private GitHub reads
still use the existing official gh credential protocol under declared authority;
no token is copied into declarations or logs.
