# Requirement workflow (development)

`aim-flow.py work --config /absolute/work.json --config-sha256 SHA256 --state /absolute/private-run`
previews a single bounded workflow grant. Repeat with `--authorize-intent SHA256`
and the approved `--key-file /absolute/key` to let Flow advance the granted phases.
After initialization, the same command resumes the retained grant without another
phase authorization. The key is never retained. An initialized workflow without
an available provider transport stops at `awaiting-provider-credential`.

The config is schemaVersion 1 with these exact fields:

- `manifestSha256`: installed controller manifest pin.
- `requirement`, `resourceCatalog`: `{path, sha256}` pinned UTF-8 requirement and
  JSON object describing available resources. Only typed public capability
  versions/platforms are disclosed to the implementation model; resource paths,
  opaque identities, qualifications and execution configuration stay local. Catalog content is evidence, not
  authority to execute or provision resources.
- `source`: `{checkout, commit, git, gitSha256}` canonical source worktree, exact
  committed baseline and pinned Git 2.54+ executable.
- `projectDocuments`, `onboardingSources`: repository-relative technical onboarding
  evidence selections. `reviewSources` separately selects implementation context.
  Discovery adds recognized project definitions.
- `editablePaths`: distinct explicit subset of reviewSources; context does not
  grant edit permission.
- `campaignDirectory`, `historicalBudget`: the existing onboard-api/deliver-api
  campaign and historical accounting configuration, unchanged. No new ledger,
  refunds, budget reset or extra USD20 ceiling is created.
- `timeoutSeconds`, `onboardingMaxOutputTokens`, `implementationMaxOutputTokens`,
  `onboardingMaxOperationNanoUsd`: bounded existing API operation settings.

Flow generates the discovery packet, onboarding configuration, implementation
prompt, exact edit request and private candidate. The operator does not author
an assessment, technical plan, patch or per-phase API payload. The agent's
implementation summary is the proposed technical plan, not reviewed approval.
Both onboarding and implementation receive the exact original requirement
(nonempty UTF-8, at most 131072 bytes). Onboarding is therefore task-aware: it
must identify source-backed feature acceptance checks, or explicit evidence
gaps, rather than equating general CI with completion of the requirement.
Onboarding also receives the same typed public runtime/browser capabilities as
implementation, with an explicit warning that registration is not qualification.
The local adapter contract is disclosed so planning can reuse compatible prepared
resources without inventing provisioning commands. Runtime paths, credentials,
opaque identities and operator prose are not sent. Unsupported process needs
remain visible rather than being removed to fit the local executor.
The grant binds full selected source disclosure, requirement/resource bytes,
editable paths, two generation attempts/output limits and the combined existing
historical-and-campaign USD20 envelope. It authorizes private local candidate
Git plumbing only, never model-proposed shell commands, project execution,
network Git operations, publication, resource provisioning or deployment.

Each API call uses the existing transport, durable reservation, exact-intent
checks, terminal receipt and shared campaign. A consumed attempt with unknown,
failed, refused or invalid outcome blocks the workflow. Missing/inconsistent
state is not repaired. Interrupted candidate preparation is retained and blocks
rather than replacing partial bytes or silently rerunning effects. A completed
phase is verified and reused without another model call. Explicit scopes or
controller/source/config changes require a new reviewed plan, never mutation
of the retained run.

The candidate is a private single-parent Git descendant. Flow copies local Git
objects without hardlinks or checkout, writes complete committed regular blobs
and exact old/new edits, and creates its index/tree/commit through typed Git
plumbing without filters, hooks, templates or signing. Links and submodules are
unsupported and refused. Dirty/untracked source checkout content is excluded.
Verification checks the full raw source inventory, exact modes, commit parent,
complete changed path set and clean candidate worktree/index. No project code
runs. Use the same command with `--verify` to verify a completed run without advancing
  incomplete phases or reading a credential. The candidate receipt includes
  intent, intentSha256, source {checkout, baseSha, headSha, treeSha, diffSha256} and the exact
`candidate: {checkout, commit, tree}` target; the intent binds source target,
proposal, projectInstance, full files inventory and before/after change hashes.

When edited files overlap protected onboarding evidence, the next stage is
`fresh-candidate-assessment-required`. Otherwise evidence can be considered by
the existing descendant validation adapter, still subject to its checks and
independent review. The two-call grant never silently authorizes another model
assessment or rewrites the original instance.

A successful result is `candidate-prepared`, `ready:false`, with validation,
independent review and acceptance still pending. It is not a full pilot, passing
CI, product acceptance or a deployment. Offline injected-provider fixtures test
orchestration and refusal contracts only; they are not live project evidence.
