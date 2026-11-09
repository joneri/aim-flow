# Discover a local project's standards

Use the installed AIM CI control package, its reviewed manifest hash, and
a separate local Git checkout. Discovery requires Python 3.11 or newer
and an explicit Git 2.54 or newer executable with its SHA-256. The optional
`--project-doc` selects a committed document to scan for script references.

```sh
"$python" -I "$install/aim-ci-control.py" discover-project \
  --manifest-sha256 "$control_manifest_sha256" \
  --checkout "$checkout" --commit "$commit" \
  --git "$git" --git-sha256 "$git_sha256" \
  --project-doc AGENTS.md --project-doc docs/verification.md
```

Supply the checkout root and the full 40-character hash of its current
`HEAD`. The report describes the committed tree. Uncommitted changes and
other refs are outside this observation. It refuses a checkout whose
`HEAD` differs from the supplied commit. Local Git reads disable network
transports, lazy fetch, replace objects, hooks and filesystem monitoring.

The JSON report identifies each source by committed path and Git object
ID, with a SHA-256 for bytes read. Facts refer to a source path and a JSON
or TOML key, a line number, or the whole file. Interpret `parsed` as a
field read by a qualified parser, `inferred` as a stack clue, `detected`
as a file or literal indicator and `risk` as a review point.

| Input | Current observation |
| --- | --- |
| Root `package.json` | Reads name, package manager, engines, workspaces, build and test definitions, declared script names, dependency names and Vite indicator. Lists lifecycle scripts as review points. |
| npm lock or shrinkwrap | Reads the version and interprets selected v3 package metadata, including locked Vite, direct version comparisons and declared install hooks. Other versions stay open. |
| pnpm or Yarn lock | Detects the file and a recognized header line. Dependency and workspace semantics stay open. |
| Root `pyproject.toml` | Reads selected build backend, Python requirement, dependency, dynamic metadata and tool table fields. Companion requirement and lock files are detected. The intended test command remains open. |
| Dockerfile, Compose and CI definitions | Scans literal line indicators such as `FROM`, `RUN`, `services`, `uses`, `run` and expression-like text. This scan leaves effective commands, includes, permissions and interpolation open. |

Discovery inventories standard root files and direct YAML files under
`.github/workflows/`. It reads complete selected objects using their measured
Git sizes and verifies their object identities. There are no discovery byte,
workflow-count or line-count ceilings. Linked and other nonregular definitions
appear as open items. Workspace paths are clues for later investigation. This
version leaves nested project roots for separate review.

Select distinct committed `README.md`, `AGENTS.md` or Markdown
paths under `docs/`. The scan links a complete literal `npm run NAME` or
`npm run-script NAME` token to the same declared script name. A plain token
ends at whitespace or the end of the line. For a command in backticks,
the name ends at whitespace or the closing backtick. Supported names start
with an ASCII letter or digit and contain at most 100 characters made of
ASCII letters, digits, periods, colons, underscores or hyphens. Quoted
arguments and punctuation attached to a plain token stay outside this
subset. Unsupported names and unmatched references become open items.
The scan treats document text as data and follows no paths or commands
from it. All selected documents are scanned completely, without a document
count or byte ceiling. An absent, linked, unavailable or unreadable document
gives an open item. A script name or reference does
not establish what it tests or whether it passes.

For each root declaration with a corresponding npm v3
`packages["node_modules/…"]` entry, discovery compares the declaration
with the recorded version. The comparison accepts complete stable
`MAJOR.MINOR.PATCH` versions with at most nine digits per component and
one leading caret on the declaration. A caret allows increases below the
next major for major versions above zero, the next minor for `0.MINOR`,
and the next patch for `0.0.PATCH`. The lower bound is inclusive.
Other syntax, including prerelease, build metadata, unions, tilde,
wildcards, aliases and URL dependencies, is outside this subset. Missing or
linked entries stay unresolved. Version-like strings appear in the report.
Other dependency specs are represented by their source locators.

The comparison follows the complete stable caret cases in the locally
available node-semver 7.8.5 README under **Caret Ranges**. The npm 11.19.0
`package-lock-json` and `npm-ci` references describe package entry metadata
and the separate lock consistency check during installation. Discovery does
not run npm, resolve transitive dependencies or verify installability.

Use `openItems` to resolve competing lockfiles, missing scripts,
unfamiliar formats and executable metadata with the operator. Review the
identified keys and lines against project documentation and available
skills. Then propose a recipe for that project's own tools, dependencies,
artifact and independent checks. The report lists this work as pending and
records which documentation or skills discovery consulted.

An operator reviews any proposed recipe and its effect limits separately.
Discovery outputs observations and a proposed review sequence. The command
does not run project scripts, installs, tests, containers or workflows. It
does not initialize journals, budgets, recipes or approval records.

Discovery also preserves script bodies and complete CI/container definitions
as source-linked data for agent review. The compatible `excerpt` field now
contains the entire definition with `complete: true`. Literal indicator scans
include every line. The metadata-only committed-tree footprint counts every
entry and reports all top-level byte totals, plus the ten largest regular
files as a summary. It counts regular files separately from other entries and
does not select execution inputs or change any runner limit.

Exact object size and hash checks, regular-file and UTF-8 requirements, path
validation, and the 20-second timeout on each network-disabled Git command
remain. Failed or incomplete reads are refused or reported as open items;
source is never silently shortened to fit a discovery ceiling.
