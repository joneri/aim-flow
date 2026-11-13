# CI selection

PRs and pushes to `main` execute **17 existing cases**: 14 Python contracts and
three Node contracts, approximately 1.7% of the complete 997-case collection.
The exact auditable selection is [smoke-contracts.json](../tests/smoke-contracts.json).
It covers authorization, shared budget/replay fencing, source-bound cache reuse
and tampering, repair iteration, owner scope limits, finding materiality,
hardlink boundaries, historical intents, strict JSON and artifact/environment
integrity. Long fixture lineage integrations are outside the automatic subset.

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
opt-in runs the complete 950-case Python discovery and both complete Node suites
(47 cases), instead of smoke. Neither PR nor push events can select full mode.
Manual test dispatch does not publish Pages; a main push publishes the site after
the selected CI checks succeed.

The full-regression deadline remains configurable through
`offline_timeout_minutes`. The smoke selection qualifies only these critical
contracts; it is not equivalent to a complete regression or product acceptance.
