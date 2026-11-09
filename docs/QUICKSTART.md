# Start with inspection

AIM Flow Beta is a technical preview. Begin without model calls, project-script execution or deployment.

## Requirements

- Python 3.11 or newer on a POSIX filesystem.
- Git 2.54 or newer for project inspection. Basic package verification needs no Git.
- Trusted controller files and independently supplied SHA-256 pins.
- A new private installation directory. Keep installations, project receipts and runtime state separate from the target checkout.

The reference qualification used macOS with Python 3.14.8 and Apple Git 2.54.0. It is not evidence that every operating system or Python version has been qualified.

## Build the beta package locally

From this repository, select your Python executable and a new output directory:

```sh
python3 scripts/package_beta.py --output /absolute/path/to/new-beta-bundle
```

The output contains a branded ZIP and checksum. The ZIP includes the controller archive and bootstrap under `controller/`, current instructions and the MIT notice. Some command files still use the earlier `aim-ci` name, including the installed `aim-ci-control.py` manager. They are included parts of AIM Flow, not a separate product you need to install. Building a package does not run project code, call a model or authorize execution.

## Verify and install

For a downloaded release, obtain the expected bootstrap and archive hashes from a separate trusted source. A hash delivered only beside an untrusted file does not authenticate its publisher. For a locally built bundle, inspect the printed result and retain its hashes.

First check the bootstrap bytes with your trusted Python, before executing them. Do not continue if this check fails:

```sh
python3 -IBS -c 'import hashlib,sys; actual=hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest(); sys.exit(0 if actual == sys.argv[2] else "Bootstrap SHA-256 mismatch")' \
  /path/to/aim-ci-bootstrap.pyz EXPECTED_BOOTSTRAP_SHA256
```

Then verify the controller archive and install:

```sh
python3 -IBS /path/to/aim-ci-bootstrap.pyz verify \
  --archive /path/to/aim-ci-control-0.1.0-beta.2.tar.gz \
  --sha256 EXPECTED_ARCHIVE_SHA256

python3 -IBS /path/to/aim-ci-bootstrap.pyz install \
  --archive /path/to/aim-ci-control-0.1.0-beta.2.tar.gz \
  --sha256 EXPECTED_ARCHIVE_SHA256 \
  --destination /absolute/path/to/new-private-controller

python3 -IBS /absolute/path/to/new-private-controller/aim-ci-control.py readiness \
  --manifest-sha256 EXPECTED_MANIFEST_SHA256
```

Default readiness should report `packageVerified: true`, `ready: false` and exit **3**. This is the expected unconfigured state, not a failed installation. Exit **1** is a verification/configuration failure. Inspection performs no credential read, network request or project execution.

## Review a project

Use [project-aware installation](aim-ci-install.md#install-for-a-projects-own-build-process) and [discovery](aim-ci-discover-project.md) to bind the exact committed source and independently pinned Git executable. A successful draft does not grant runtime permission.

For this beta, a pilot should select one meaningful existing source-only npm check. It must fit the [supported contract](BETA-SCOPE.md), including explicit input selection and no dependency installation. Review its script and all selected code before preparing any runtime. Each preparation and execution needs the matching exact-intent consent and its own applicable limits.

Never paste credentials into issues or commit them. Model intake is optional, separate and budget-bound. Start with the documented inspection commands before enabling any execution.
