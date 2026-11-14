# CI selection

PRs and pushes to `main` execute **17 existing cases**: 14 Python contracts and
three Node contracts, approximately 1–2% of the complete collection.
The exact auditable selection is [smoke-contracts.json](../tests/smoke-contracts.json).
It covers authorization, shared budget/replay fencing, source-bound cache reuse
and tampering, reviewed test-reuse boundaries, repair iteration, owner scope limits, finding materiality,
hardlink boundaries, historical intents, strict JSON and artifact/environment
integrity, plus one small source-free native Codex workflow with a simulated CLI and real focused local test. Long fixture lineage integrations are outside the automatic subset.

Run the same subset locally:

```sh
python3 -B scripts/run_smoke.py --list
python3 -B scripts/run_smoke.py
```

`--list` does not import or execute tests. Python must pass all 14 selected cases
without skips. Node's exact-name filter executes three cases; other cases are
filtered out, not run. The runner refuses a successful empty match or fewer passes.
The selection is deliberately limited to 10–19 cases, so it cannot silently grow
back into full regression.

The existing provenance, archive/bootstrap/fresh-install, version and static-link
checks remain. They do not repeat project/model operations or the full suites.

## Deliberate full regression

Only when explicitly wanted, open **Actions → Beta packaging checks → Run
workflow** and enable **full_regression**. It defaults to **false**. This manual
opt-in runs complete Python discovery and both complete Node suites, instead of
smoke. Collection size can change as duplicate tests are consolidated. Neither PR nor push events can select full mode.
Manual test dispatch does not publish Pages; a main push publishes the site after
the selected CI checks succeed.

The full-regression deadline remains configurable through
`offline_timeout_minutes`. The smoke selection qualifies only these critical
contracts; it is not equivalent to a complete regression or product acceptance.

## Focused installation and lineage checks

The three installed API/GitHub wrappers check fresh source-free installation,
CLI wiring and ten selected authorization, accounting, durable-effect and replay
contracts (4 API, 3 delivery, 3 publication). They do not repeat all 75 source
contracts. The complete source suites remain available through manual regression.

The real repair-lineage integration advances one fixture through repairs two to
six once. Every original authority, accounting, evidence and publication-binding
assertion remains; later tests no longer recursively call earlier tests. Timing
contracts assert deadline/refusal behavior without a machine-speed pass threshold.
Historical inventory compatibility reads the retained historical archive rather
than inferring its file count by subtracting today's helpers.
