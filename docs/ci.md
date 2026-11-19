# CI, release and deploy

## Stages

```text
lint  ->  test  ->  build  ->  release (manual)  ->  deploy (manual)
```

- **lint:** `ruff check .`
- **test:** pytest with coverage; headless GTK entry-point check; real Deluge
  2.0.3 / 2.1.1 / 2.2.0 compatibility matrix.
- **build:** `python -m build` plus `scripts/package.sh` (egg + SHA256);
  artifacts live in `dist/`.
- **release:** manual on the default branch. `scripts/release.sh` bumps
  `diskguard/version.py`, moves the `Unreleased` notes in `CHANGELOG.md` into a
  dated section, builds the egg, commits, tags `vX.Y.Z` and publishes a GitLab
  Release with the egg and checksum attached.
- **deploy:** manual on the default branch, after a successful release. Uses
  the release artifact, copies the egg to the Deluge host, installs it into
  `$DELUGE_CONFIG_DIR/plugins`, restarts `$DELUGE_SERVICE`, and checks that
  the service is active.

`RELEASE_TYPE` is `patch` by default; set `minor` or `major` for a different
release.

## Deploy variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `DEPLOY_HOST` | required | SSH target, e.g. `root@deluge-host` |
| `DEPLOY_SSH_KEY` | optional | Private key written to `~/.ssh/id_ed25519` |
| `DELUGE_CONFIG_DIR` | `/var/lib/deluged/config` | Deluge config directory |
| `DELUGE_SERVICE` | `deluged` | systemd service to restart |

The deploy stage installs the **core** plugin on the daemon host. The GTK client
is a separate machine; install the same egg there and restart `deluge-gtk`.

A release commit pushed by the CI job token does not create a new pipeline.
Create a pipeline for the new ref (or rerun the release/deploy jobs manually) if
the release must be followed by another CI run.

## GitHub mirror

The GitHub repository is a one-way mirror of the private GitLab repository.

- GitLab push-mirror authentication needs a GitHub fine-grained PAT for the
  target repository with **Contents: Read and write**, **Workflows: Read and
  write** (the repository contains `.github/workflows/release.yml`) and the
  mandatory **Metadata: Read** permission.
- GitHub Actions uses the repository's built-in `GITHUB_TOKEN` with
  `contents: write`; no extra PAT is needed there.
- On a `v*` tag, the GitHub workflow checks that the tag matches
  `diskguard/version.py`, builds wheel/sdist/egg + SHA256 and creates or
  updates the GitHub Release from the matching CHANGELOG section.
