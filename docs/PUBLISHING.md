# Publish the prepared Beta

Target: **public `joneri/aim-flow`**, MIT. Website: GitHub Pages project site under `/aim-flow/`. These are intended destinations until live publication and readback are verified.

1. Create the public repository under `joneri`, without generated starter files if pushing this prepared Git history.
2. Publish the reviewed local `main` branch. Do not include private operator directories or credentials. The local package does not contain them.
3. In repository **Settings → Pages → Build and deployment**, choose **GitHub Actions**.
4. Run the prepared **Beta packaging checks** workflow on `main` (or push `main` after Pages is configured). Its portable checks run first, then the Pages job publishes `site/` to the `github-pages` environment.
5. Verify the remote commit and workflow result. Open the real HTTPS page and test the guide, scope, PDF, ZIP, checksum and artwork-notice links.
6. Only then describe the website as live. Optionally create a pre-release tagged `v0.1.0-beta.5`, attaching the reviewed `site/downloads/aim-flow-0.1.0-beta.5.zip` and `SHA256SUMS`.

If the first Pages job runs before Pages is enabled, configure Pages and rerun the failed deployment job. Do not change repository access, add provider credentials or migrate the old application automation to work around publication problems.

## Current reviewed download

ZIP SHA-256: `40169aa3648383fadcdfd4619e1853a18d63eed19368a9821ed8927c29cf3add`

The source checkout's `scripts/check_bundle.py` binds the bootstrap to the preserved trusted source before testing it. End users must independently trust their expected download/bootstrap hashes as described in the quickstart.
