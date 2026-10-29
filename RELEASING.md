# Creating a release

The [Release workflow](.github/workflows/release.yml) builds a wheel and source
archive, checks the installed CLI and bundled resources, runs the full test
suite on Linux, generates `SHA256SUMS` and uploads the files to a GitHub Release.
Firmware files are downloaded by the CLI from the official catalog; they are
not included in the release. The tested BR25 RAM loader and its license are
included in the Python package.

## Build-only check

Open **Actions → Release → Run workflow**, select the branch to test, leave
`tag` empty and `publish` disabled. The workflow builds and tests the selected
ref and saves the distributions as an Actions artifact for 14 days. It does not
create a tag or publish a release.

From GitHub CLI:

```sh
gh workflow run release.yml --ref main
```

## Publish a version

1. Update `version` in `pyproject.toml` and `__version__` in
   `hlyflash/__init__.py` to the same version. Refresh the lockfile with `uv lock`.
2. Commit and push the version change. Wait for the Tests workflow on that
   commit to pass on Linux and macOS.
3. Create an annotated tag matching the package version and push it. For the
   currently configured version:

   ```sh
   git tag -a v0.1.0 -m 'hlyflash v0.1.0'
   git push origin v0.1.0
   ```

Pushing `v*` tags starts the Release workflow. A tag/version mismatch stops
before publishing. The tag must already exist; the workflow never invents a tag
from the latest branch state. Prerelease/dev versions are marked as prereleases.
The GitHub token needs `contents: write`, granted only to the release job.
No PyPI credentials are required, and the workflow does not publish to PyPI.

An existing tag can also be released manually using **Run workflow**, setting
`tag` and enabling `publish`:

```sh
gh workflow run release.yml --ref main -f tag=v0.1.0 -f publish=true
```

Use a new version/tag for a new published release. If that GitHub Release
already exists, publication fails rather than replacing its assets. A failed
upload may leave a draft release; review and remove that draft before retrying.
Build-only runs can always be repeated without publishing.

## What users receive

Each release contains:

- `hlyflash-VERSION-py3-none-any.whl`: installable CLI, loader, license and Linux rules.
- `hlyflash-VERSION.tar.gz`: source distribution, documentation and tests.
- `SHA256SUMS`: SHA-256 hashes of both distributions.

Users can install the downloaded wheel with `uv tool install ./hlyflash-VERSION-py3-none-any.whl`
or `pipx install ./hlyflash-VERSION-py3-none-any.whl`, then run `hlyflash doctor`.
On Linux, run `hlyflash setup` once and reconnect the device before updating.
For a checksum check, use `sha256sum -c SHA256SUMS` on Linux or
`shasum -a 256 -c SHA256SUMS` on macOS, with both distributions in the same directory.
