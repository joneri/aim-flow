# Flow-owned committed requirement intake

`lib/flow_requirement_intake.py` is a network-free, read-only entry point before
requirement workflow onboarding. The caller supplies the original requirement
file and SHA-256, exact checked-out Git commit, pinned Git 2.54+ executable,
and broad allowed edit directories. It supplies budget/resource/installation
bindings as usual, but **does not supply a per-file source map**.

Python interface:

```python
report = flow_requirement_intake.derive(controller_root, source,
    requirement_pin, ['src/', 'scripts/', 'docs/features/'])
config = flow_requirement_intake.assemble_config(base_config, report)
```

The outer orchestrator calls these functions locally, persists the report and
returned standard workflow config, then pins that config for the existing work
entry point. It must retain the report alongside its config provenance. Call
`derive` again rather than trusting an edited report. Existing workflow source,
resource, budget and authorization validation still applies. Intake itself grants
no execution or provider authority and does not verify an installation manifest.

Standalone CLI (paths must be canonical; output must not already exist):

```sh
python3 lib/flow_requirement_intake.py --base-config /private/input/base.json \
  --base-config-sha256 SHA256 --allow-directory src/ \
  --allow-directory scripts/ --allow-directory docs/features/ \
  --output /private/output/intake.json
```

Output contains `intake` and `workflowConfig`. Incomplete coverage produces a null
config, never a silently reduced successful scope. No target command, dependency
installation, hook, provider, network request, candidate modification or GitHub
operation is performed. Only pinned Git object plumbing is invoked.

## Retrieval rules and limits

The complete committed tree gets a per-path record. Only regular UTF-8 text in the
allowed code/docs domain and recognized process definitions is considered. Dirty
and untracked content is never read. Symlinks, submodules, binary/unknown formats,
known secret paths, credential configuration directories and suspected literal
secrets are excluded with reasons. JSON data files outside process definitions
are explicitly outside this code-retrieval policy. This is conservative filtering,
not a guarantee that arbitrary source contains no confidential information.

Requirement terms are case-folded and camel-case split, stop words removed, then
weighted by requirement repetition and inverse document frequency. Path matches
get an eight-fold weight. Scores are divided by sqrt(1 + UTF-8 bytes / 8000)
to prevent long shared assets from dominating by incidental term overlap; scores at least 45% of the maximum (minimum eight) are
selected. Test/spec paths use 75% of that threshold to retain test evidence. Exact weights, threshold, matched terms and score are recorded. Literal
relative imports add dependency evidence to a fixed point. One reverse-reference
pass adds code files mentioning selected code filenames; this is a lexical
relation, not proof of a runtime import. It deliberately excludes prose references
from that expansion. This may overselect unrelated code. Missing dynamically
resolved imports and excluded files may still contain relevant evidence.

Process manifests/locks, CI, recognized build/deploy/release/generator definitions,
process documents and literal package script paths outside edit authority are
immutable onboarding evidence. Mutable feature code, tests and feature documents
remain separate review/edit sources. A broad edit directory never authorizes
changing protected process definitions. Paths to missing/unread process script
references remain incomplete. Normal feature edits therefore do not invalidate
an assessment by accidentally being included in its onboarding packet.

Default whole-file ceilings: 262,144 bytes/file; 24,000,000 scanned bytes;
1,000,000 total context bytes; 80 context files; 440,000 mutable review bytes.
No material file is prefix-truncated. Read/context bound failures are explicit
incomplete paths and prevent config assembly. These limits are a retrieval policy,
not a claim of semantic completeness. The mutable limit leaves headroom under the
candidate review's 1 MiB baseline+candidate bound, but callers must also preflight
actual changed-path/declaration-source unions and candidate growth before spending.
Per-file growth can likewise exceed the downstream 262,144-byte ceiling.

`assemble_config` copies all original source, requirement, budget, resource and
operation-limit fields unchanged and replaces only the four derived selection
arrays. It refuses mismatched source/requirement bindings and incomplete reports.
No project-specific names or paths are embedded in the engine.

Process classification is a heuristic, not a promise that every executable
script is immutable. A package-script target inside broad edit authority may
remain mutable feature/test context when its role is not recognized as process
infrastructure. This is intentional for maintained feature tests. The later
source review must assess changed tests and guards; a directory grant or intake
classification is not evidence of their safety or semantic correctness. Every
recognized process definition that cannot be read is reported incomplete,
including excluded configuration paths and unsupported formats.
