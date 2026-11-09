# Install AIM CI and inspect a project

Use Python 3.11 or newer on a POSIX system. The bootstrap uses only Python's
standard library. Project inspection also needs an existing local Git checkout
and a real Git 2.54.0 or newer executable. The local workflow was tested on
macOS. Git, Node and npm are unnecessary for installation.

## Obtain the Beta bundle

Use the current AIM Flow Beta download and its separately trusted checksum. The outer distribution contains MIT and artwork notices plus current instructions; its `controller/` directory contains the controller archive, bootstrap and detailed guide. Installation proves package integrity, not runtime readiness or product acceptance.

Obtain the expected bootstrap and archive SHA-256 values through a trusted
channel. The bundle's `candidate.json` and `release.json` list hashes for
comparison. A hash supplied only by the download shows internal consistency.
Verify `aim-ci-bootstrap.pyz` against the trusted bootstrap digest before
running it. Use the archive filename in `release.json`.

Set `python`, `bundle`, `archive`, `archive_sha256`, `bootstrap_sha256` and `install` to
your interpreter, bundle directory, archive filename, independently supplied
pins and new installation path. The installation parent must already exist.

```sh
"$python" -IB -c 'from hashlib import sha256; from pathlib import Path; import sys; sys.exit(sha256(Path(sys.argv[1]).read_bytes()).hexdigest() != sys.argv[2])' \
  "$bundle/aim-ci-bootstrap.pyz" "$bootstrap_sha256" && \
"$python" -I "$bundle/aim-ci-bootstrap.pyz" verify \
  --archive "$bundle/$archive" --sha256 "$archive_sha256" && \
"$python" -I "$bundle/aim-ci-bootstrap.pyz" install \
  --archive "$bundle/$archive" --sha256 "$archive_sha256" --destination "$install"
```

Both commands print the manifest SHA-256. Save it for installed commands.
Installation uses a new directory and rejects links and existing destinations.
If a filesystem error leaves a partial installation, preserve it for diagnosis
and select a new path for a later attempt.

Set `checkout` to the Git worktree root, `commit` to its full current HEAD,
`git` to the real Git executable path and `git_sha256` to the reviewed digest
of that executable. On macOS, `xcrun --find git` locates the developer-tool
binary. Set `manifest_sha256` to the value reported by the verified installation.

```sh
manager="$install/aim-ci-control.py"
"$python" -I "$manager" discover-project --manifest-sha256 "$manifest_sha256" \
  --checkout "$checkout" --commit "$commit" --git "$git" --git-sha256 "$git_sha256"
"$python" -I "$manager" propose-adaptation --manifest-sha256 "$manifest_sha256" \
  --checkout "$checkout" --commit "$commit" --git "$git" --git-sha256 "$git_sha256" \
  --test-script "$selected_test_script"
```

Choose `selected_test_script` from the committed script names reported by
discovery. For a project that declares `test:playwright`, set it to
`test:playwright`. You can add `--project-doc` for selected committed Markdown
and pass `--npm-docs` to adaptation to cite already installed npm documentation.
See the installed discovery and adaptation guides in `docs/` for the bounds.

Exit **0** means the requested inspection completed. Read the JSON status
for open questions and a possible recipe proposal. Exit **1** means a check
refused the input or the operation failed. Exit **2** indicates command-line
usage errors. The separate `check-recipe-execution` command returns **3** for a
blocked backend. Discovery and adaptation are read-only. Draft commands and
`plan-recipe` output require separate review, execution authorization and
release authorization.

## Prepare a reviewed npm/Vite build

Use installed project readiness with a pinned configuration, including an
incomplete `configurationDraft` object. Select `preparation`, `acquisition`,
or `build` to assess the inputs for that step.

```sh
"$python" -I "$manager" readiness --workflow preparation \
  --manifest-sha256 "$manifest_sha256" --config "$prep_config" \
  --config-sha256 "$prep_config_sha256"
```

Read `unset`, `source`, `dependencies` and `next`. To compare a preparation
or acquisition configuration with the current commit and local tarballs,
add `--git-sha256 "$git_sha256"` using the independently reviewed digest of
the Git executable named in the configuration. This explicitly permits the
bounded local Git reads, including its version check. Git 2.54.0+ is required
for those reads. The default project check starts no processes. Build
readiness reads pinned planning files and reports their declared source and
image store, without checking the live checkout or the image.

Exit **3** returns guidance, including when a separate preview is available.
Exit **1** reports invalid, stale or unsupported inputs. A verified package
and a complete configuration still leave `ready: false`. Docker, image
contents, isolation, network, installation, build, browser tests and product
acceptance are unchecked. Use the separate preview and exact-intent consent
for any later effect. The plain `readiness` invocation without `--workflow` reports package state; the neutral catalog profile remains an offline testing aid.

Project discovery also reports Python, alternative JavaScript package
managers, container definitions and CI configuration. Use those facts and
the adaptation questions to decide what needs review. A project outside the
current npm/Vite recipe stays in discovery and adaptation until a suitable
preparation and execution adapter is reviewed.

Start with the installed controller and the pinned checkout inputs above.

```sh
"$python" -I "$manager" draft-build-preparation --manifest-sha256 "$manifest_sha256" \
  --checkout "$checkout" --commit "$commit" --git "$git" --git-sha256 "$git_sha256"
```

The command prints JSON to stdout. Review `adaptation`, `checkoutFacts`,
`declaredScripts` and `operatorChoices`. The reported origin and branch are
local Git labels. Script bodies are committed text and their behavior is
unverified. For a recognized npm/Vite candidate, copy `configurationDraft`
to a separate JSON file, keep its `source` binding and fill each `null`
with a reviewed value. Optionally pass `--test-script` to record your choice
of a literal committed script name. A report with no `configurationDraft`
lists the discovery questions to resolve. The report and incomplete
configuration are refused by preparation. Drafting reads Git only and
does not probe Docker or create output, state or approval.

For a supported repository, `prepare-reviewed-build` assembles the exact
committed source, an offline npm platform store, a reviewed recipe and a
preloaded derived image. It uses a pinned local Docker
CLI and the reviewed Unix endpoint. Image preparation uses a container that
never starts and needs an already cached base image. It runs no builder,
project scripts, tests or downloads.

Create a local directory for npm `.tgz` files you already obtained through
your approved process, or start with an empty private directory (`mkdir -m 700`).
Fill `platform` and `tarballsDir` in the extracted configuration
object. The acquisition step can run before you fill the Docker, image,
recipe and oracle choices. Save the file, compute its SHA-256 and choose a
new `acquisition_state` path outside the checkout, tarball directory and
installation. Inspect the exact missing packages and proposed HTTPS requests.

```sh
"$python" -I "$manager" acquire-npm-tarballs --preview \
  --manifest-sha256 "$manifest_sha256" --config "$acquire_config" \
  --config-sha256 "$acquire_config_sha256" --state "$acquisition_state"
"$python" -I "$manager" acquire-npm-tarballs --authorize-intent "$acquire_intent_sha256" \
  --manifest-sha256 "$manifest_sha256" --config "$acquire_config" \
  --config-sha256 "$acquire_config_sha256" --state "$acquisition_state"
```

Preview reads local Git and tarball bytes only. Add the same optional
`--locator node_modules/<package>` to each call to select one package.
The acquisition command accepts canonical locked `https://registry.npmjs.org`
tarball URLs. It uses direct TLS to checked public addresses and refuses
redirects. For other registries or unsupported lock graphs, supply approved
local bytes where the platform contract permits, or choose a supported route.
Each approved batch covers up to 16 distinct tarballs, 16 MiB each and
256 MiB total, with up to one byte per response to detect oversize. Requests
have a 25-second limit within the batch's five-minute dispatch window.
The command publishes verified compressed bytes without replacing existing
files. It never extracts or installs the packages. A batch can leave other
platform entries missing. Preview them with a new state path.

After interruption, use `--resume` with the same configuration digest,
state and locator. The journal and per-request observations distinguish
HTTPS attempts, existing bytes and local reconciliation. A request with
no verifiable retained bytes stays unknown and is not retried by resume.
Keep the original state and inspect the recorded error before planning a
separate acquisition. Only the applicable Linux glibc arm64 or x64 subset
of one root npm v3 lock is covered.

After acquisition, complete and review the preparation configuration and
recompute its SHA-256. The separate `prepare-reviewed-build --preview`
still checks the local SHA-512 tarballs and reports any missing entries.
Acquisition approval supplies no authority to prepare an image, install,
build or accept a product.

Use an absolute configuration path outside the checkout and installation.
The following manual version 1 example shows the fields shared with a
version 2 draft. A completed version 2 configuration also retains `source`,
which preparation checks against the commit, tree and file bytes. Draft
again if the source changes. Use actual SHA-256 pins
for the existing Git and Docker executables and the Node and npm CLI bytes
inside the approved base. The base ID is the full local image ID. The
preparation output must be new and its parent must be owned and protected.

```json
{
  "version": 1,
  "manifestSha256": "<installed manifest SHA-256>",
  "checkout": "/absolute/project",
  "target": {
    "repository": "https://example.org/team/project.git",
    "branch": "main",
    "commit": "<full current Git commit SHA>"
  },
  "git": {"path": "/absolute/git", "sha256": "<Git SHA-256>"},
  "docker": {
    "path": "/absolute/docker",
    "sha256": "<Docker CLI SHA-256>",
    "endpoint": "unix:///absolute/reviewed/docker.sock"
  },
  "base": {
    "imageDigest": "sha256:<full local image ID>",
    "tools": {
      "node": {"path": "/usr/local/bin/node", "sha256": "<in-image Node SHA-256>"},
      "npm": {"path": "/usr/local/lib/node_modules/npm/bin/npm-cli.js", "sha256": "<in-image npm CLI SHA-256>"}
    }
  },
  "platform": {"os": "linux", "cpu": "arm64", "libc": "glibc"},
  "tarballsDir": "/absolute/offline-tarballs",
  "recipe": {
    "id": "reviewed-project",
    "stack": "npm-vite-static",
    "testScript": "test:unit",
    "allowedPaths": ["src/App.tsx"],
    "forbiddenPaths": ["dist"],
    "artifact": {"directory": "dist", "requiredFiles": ["index.html"], "maximumBytes": 2000000},
    "limits": {"network": "disabled", "timeoutSeconds": 90, "memoryMiB": 1024, "maxProcesses": 64}
  },
  "oracle": {"kind": "static-heading-v1", "file": "index.html", "heading": "<reviewed expected heading>"},
  "assignment": {"version": 1, "id": "local-build", "title": "Review local build",
    "acceptance": "Verify the required static artifact", "paths": ["src/App.tsx"]},
  "outputDir": "/absolute/private/prepared-project"
}
```

Preview is local and creates no output. Inspect the exact Git commit and
tree, script facts, lockfile and tarball identities, runtime and base
declarations, and selected recipe, artifact and oracle. The test script
is a planning selection at this point. Preparation does not run it.
Supply the exact preview digest to consent to preparation once.

```sh
"$python" -I "$manager" prepare-reviewed-build --preview \
  --manifest-sha256 "$manifest_sha256" --config "$prep_config" --config-sha256 "$prep_config_sha256"

"$python" -I "$manager" prepare-reviewed-build --authorize-intent "$prep_intent_sha256" \
  --manifest-sha256 "$manifest_sha256" --config "$prep_config" --config-sha256 "$prep_config_sha256"
```

The output includes the source manifest, local platform index, the retained
image ID, recipe, planning records, exact plan and `run-config.json`. The
planning records refer to this preparation consent by the invoking OS
principal. They do not attest to an earlier review or authorize a build.
Use the reported run configuration and its SHA-256 with
`run-reviewed-build --preview`. That CLI presents a separate effect and
requires a new exact approval. Actual image byte readback and npm runtime
verification take place on the build route. The current supported runtime
remains the pinned Node/npm/cacache combination in the recipe documentation.

Build-only is the default. For a named test that needs only the pinned
runtime and installed dependencies, review its command and prerequisites,
then add `--with-offline-test` to the build preview and authorize that
exact intent. Use the same flag on authorization and resume. The test
runs before the build in the same disposable workspace. A failed or
unverified test cannot pass the combined run. The flag requires an
offline test with no browser, network, loopback service or additional
runtime setup. Liam's selected `test:playwright` requires Playwright
browser binaries and its loopback web server and is outside this mode.
[Named test operation and evidence](aim-ci-plan-recipe.md#run-a-reviewed-named-offline-test)

Use `prepare-reviewed-build --resume` with the same configuration to
reconcile known preparation receipts. An unresolved upload remains
unknown. After interruption, `cleanup-build-preparation --output "$output"
--intent-sha256 "$prep_intent_sha256" --manifest-sha256 "$manifest_sha256"`
records withdrawal and removes only the exact owned inert staging container.
If a controller is active, the command reports `cleanup-pending-active-controller`
with exit status 1. Retry it after the active controller exits to confirm cleanup.
It leaves the derived image and evidence, if present, in place. Keep the
locally prepared image available until the separately approved build has finished.

For final image cleanup, finish the build and keep the original configuration,
checkout, tarballs and preparation output available. Use the full derived
image ID reported by preparation. The preview reads retained inputs and the
journal without contacting Docker. The second command explicitly withdraws
preparation consent and removes that image.

```sh
"$python" -I "$manager" cleanup-build-preparation --preview \
  --output "$output" --intent-sha256 "$prep_intent_sha256" \
  --manifest-sha256 "$manifest_sha256" --remove-image "$derived_image_id"

"$python" -I "$manager" cleanup-build-preparation \
  --output "$output" --intent-sha256 "$prep_intent_sha256" \
  --manifest-sha256 "$manifest_sha256" --remove-image "$derived_image_id"
```

Use the already running reviewed daemon with no concurrent writers to these
Docker resources. The command checks the original journal, base metadata,
image labels, sole tag, digest references and container references. It
removes by full ID without force or pruning. After an unknown result, the
same command can confirm absence of the original ID and tag. It never
resends an uncertain removal. The VM lifecycle remains an operator action.
