# PSPDFKit-SP-Mirror

This is a version of https://github.com/PSPDFKit/PSPDFKit-SP backed by CDN instead of downloading straight from S3.

## Updating for new releases

This is automated. The [`mirror-upstream`](.github/workflows/mirror-upstream.yml) workflow runs daily (and can be triggered manually) and, for every upstream [`PSPDFKit-SP`](https://github.com/PSPDFKit/PSPDFKit-SP) version tag newer than the latest one already mirrored here, it:

1. reads the upstream `Package.swift` for the framework download URLs and checksums,
2. downloads the two framework zips and verifies them against the upstream checksums,
3. opens a `feature/{{ version }}` branch with an updated `Package.swift`,
4. creates a `pre-{{ version }}` prerelease (not marked latest) with the zips attached, and
5. opens a pull request.

The `pre-` tag prefix keeps SPM from resolving that tag as a package version, and the prerelease flag keeps it from ever being treated as latest.

When you merge the pull request, the [`publish-release`](.github/workflows/publish-release.yml) workflow reads the version from `Package.swift` and publishes the final `{{ version }}` release marked as latest.

To mirror a specific version on demand, run the `mirror-upstream` workflow via **Actions → mirror-upstream → Run workflow** and enter the version.

### Making the auto-opened PR run CI

Pull requests opened by the default `GITHUB_TOKEN` do not trigger other workflows, so the [`ci`](.github/workflows/ci.yml) checks won't start automatically on the mirror PR. To get CI running on it, add a repository secret named `MIRROR_PAT` containing a personal access token with `contents` and `pull_requests` write access; the workflow will use it instead. Without the secret, close-and-reopen the PR (or push an empty commit) to kick off CI.

### Doing it manually

If you ever need to do this by hand: create a `feature/{{ version }}` branch, update the URLs and hashes for each framework, create a `pre-{{ version }}` release on that branch with the framework zips attached (not marked latest), and open a PR. Once the PR is merged, create a `{{ version }}` release marked as latest.
