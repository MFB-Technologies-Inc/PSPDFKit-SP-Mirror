#!/usr/bin/env python3
"""Mirror new releases of the upstream PSPDFKit-SP package into this repository.

For every upstream version tag that is newer than the newest version with a
final release here, this script:
  1. reads the upstream Package.swift at that tag to learn the framework
     download URLs and their SHA-256 checksums,
  2. downloads the two framework zips (PSPDFKit + PSPDFKitUI),
  3. verifies the downloaded bytes against the upstream checksums,
  4. creates a `feature/<version>` branch with an updated Package.swift that
     points at this repo's own release assets and copies upstream's
     swift-tools-version and platforms,
  5. creates a `pre-<version>` prerelease (NOT marked latest) with the two
     zips attached, and
  6. opens a pull request against `main`.

Each step checks for its own output first, so a run that failed partway is
resumed by the next one; a version is done once its PR exists. When several
versions are pending, each branch is stacked on the previous open one so the
PRs don't conflict.

`mirror_upstream.py publish` is run by the publish-release workflow on main. It
publishes the final `<version>` release for each `pre-<version>` that has
reached main, marking the newest one latest.

Configuration comes from the environment:
  UPSTREAM_REPO   upstream owner/repo               (default: PSPDFKit/PSPDFKit-SP)
  MIRROR_REPO     this owner/repo                   (default: derived from `gh`)
  FORCE_VERSION   mirror only this version, ignore  (optional)
                  the "newer than mirror" check
  DRY_RUN         if set to 1, print the plan and make no changes (optional)
  GH_TOKEN        token used by `gh` and `git push`

The logic is split into small functions so it can be unit-tested without
performing any side effects; see test_mirror_upstream.py. Importing this module
defines the functions but does not run anything.

External tools (`git`, `gh`, `curl`) are invoked as subprocesses; the script
itself needs only the Python standard library.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

UPSTREAM_REPO_DEFAULT = "PSPDFKit/PSPDFKit-SP"
MANIFEST_PATH = "Package.swift"
USER_AGENT = "PSPDFKit-SP-Mirror-script"

# Matches a plain semantic version tag, e.g. 26.11.0
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


class MirrorError(Exception):
    """A recoverable-but-fatal condition for a single version (aborts the run)."""


@dataclass
class Config:
    upstream_repo: str
    mirror_repo: str
    force_version: str = ""
    dry_run: bool = False


@dataclass
class BinaryTarget:
    name: str
    url: str
    checksum: str


def log(message: str) -> None:
    print(f"==> {message}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Pure helpers (no side effects, unit-testable)
# ---------------------------------------------------------------------------

def parse_version(version: str) -> tuple:
    """Version as a tuple of ints for comparison, e.g. '26.11.0' -> (26, 11, 0)."""
    return tuple(int(part) for part in version.split("."))


def version_gt(a: str, b: str) -> bool:
    """True iff semver `a` is strictly greater than semver `b`."""
    return parse_version(a) > parse_version(b)


def filter_semver(tags: Iterable[str]) -> List[str]:
    """Keep only plain semver tags, ascending and de-duplicated."""
    kept = {t.strip() for t in tags if SEMVER_RE.match(t.strip())}
    return sorted(kept, key=parse_version)


def compute_candidates(mirror_max: str, versions: Iterable[str]) -> List[str]:
    """Versions strictly newer than `mirror_max`, ascending and de-duplicated."""
    newer = {
        v.strip()
        for v in versions
        if v.strip() and version_gt(v.strip(), mirror_max)
    }
    return sorted(newer, key=parse_version)


_TARGET_RE = re.compile(
    r'\.binaryTarget\(\s*name:\s*"([^"]+)"\s*,\s*'
    r'url:\s*"([^"]+)"\s*,\s*checksum:\s*"([^"]+)"'
)


def parse_manifest(text: str) -> List[BinaryTarget]:
    """Parse a Package.swift, returning one BinaryTarget per `.binaryTarget(...)`."""
    return [BinaryTarget(*m.groups()) for m in _TARGET_RE.finditer(text)]


def manifest_targets_by_name(text: str) -> Dict[str, BinaryTarget]:
    """parse_manifest indexed by target name."""
    return {t.name: t for t in parse_manifest(text)}


def _set_target(text: str, name: str, url: str, checksum: str) -> str:
    """Replace the url + checksum of a single named binaryTarget. Fails unless
    exactly one target matches (guards silent no-ops)."""
    pat = re.compile(
        r'(\.binaryTarget\(\s*name:\s*"' + re.escape(name) + r'"\s*,\s*'
        r'url:\s*")[^"]+("\s*,\s*checksum:\s*")[^"]+(")'
    )
    new_text, n = pat.subn(
        lambda m: m.group(1) + url + m.group(2) + checksum + m.group(3), text
    )
    if n != 1:
        raise MirrorError(f"expected exactly 1 replacement for {name}, made {n}")
    return new_text


def rewrite_manifest(
    text: str,
    repo: str,
    pre_tag: str,
    kit_asset: str,
    kit_sum: str,
    ui_asset: str,
    ui_sum: str,
) -> str:
    """Return `text` with the PSPDFKit and PSPDFKitUI targets pointed at the
    mirror's release assets. Fails unless each target is replaced exactly once."""
    base = f"https://github.com/{repo}/releases/download/{pre_tag}"
    text = _set_target(text, "PSPDFKit", f"{base}/{kit_asset}", kit_sum)
    text = _set_target(text, "PSPDFKitUI", f"{base}/{ui_asset}", ui_sum)
    return text


_TOOLS_VERSION_RE = re.compile(r"^// swift-tools-version:.*$", re.M)
_PLATFORMS_RE = re.compile(r"platforms:\s*\[[^\]]*\]")


def sync_toolchain(text: str, upstream: str) -> str:
    """Return `text` with its swift-tools-version line and `platforms:` block
    copied from `upstream`, so consumers get the same toolchain and OS minimums
    upstream declares. Fails unless each appears exactly once in both."""
    for pattern, label in ((_TOOLS_VERSION_RE, "swift-tools-version"), (_PLATFORMS_RE, "platforms")):
        source = pattern.findall(upstream)
        if len(source) != 1:
            raise MirrorError(f"expected exactly 1 {label} in upstream Package.swift, found {len(source)}")
        text, n = pattern.subn(lambda _m: source[0], text)
        if n != 1:
            raise MirrorError(f"expected exactly 1 {label} in Package.swift, found {n}")
    return text


def kit_asset_name(version: str) -> str:
    return f"Nutrient-iOS-SDK-PSPDFKit.xcframework-{version}.zip"


def ui_asset_name(version: str) -> str:
    return f"Nutrient-iOS-SDK-PSPDFKitUI.xcframework-{version}.zip"


def sha256_file(path) -> str:
    """SHA-256 hex digest of a file, streamed so large archives don't blow memory."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_checksum(path, expected: str) -> bool:
    """True iff the SHA-256 of `path` equals `expected`."""
    return sha256_file(path) == expected


# ---------------------------------------------------------------------------
# Thin external-process / network seams (patched in tests)
# ---------------------------------------------------------------------------

def run(args: Sequence[str]) -> subprocess.CompletedProcess:
    """Run a side-effecting command, streaming its output; raise on failure."""
    return subprocess.run(list(args), check=True, text=True)


def capture(args: Sequence[str]) -> str:
    """Run a command and return its stdout, stripped; raise on failure."""
    return subprocess.run(
        list(args), check=True, text=True, stdout=subprocess.PIPE
    ).stdout.strip()


def fetch_bytes(url: str, headers: Optional[Dict[str, str]] = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def fetch_text(url: str) -> str:
    return fetch_bytes(url).decode("utf-8")


def fetch_json(url: str):
    return json.loads(fetch_bytes(url, {"Accept": "application/vnd.github+json"}))


def download_file(url: str, dest) -> None:
    """Download a (possibly large, ~100 MB) file to `dest`. Uses curl for its
    robust redirect/retry handling on big binary transfers."""
    run(["curl", "-fSL", "--retry", "3", "--retry-all-errors", url, "-o", str(dest)])


# ---------------------------------------------------------------------------
# GitHub reads (prefer `gh`; fall back to unauthenticated HTTP so the discovery
# logic can be exercised locally without a token)
# ---------------------------------------------------------------------------

def gh_available() -> bool:
    return shutil.which("gh") is not None


def list_repo_tags(repo: str) -> List[str]:
    if os.environ.get("GH_TOKEN") and gh_available():
        out = capture(["gh", "api", "--paginate", f"repos/{repo}/tags", "--jq", ".[].name"])
        return [line for line in out.splitlines() if line.strip()]
    return _http_list_tags(repo)


def _http_list_tags(repo: str) -> List[str]:
    names: List[str] = []
    page = 1
    while True:
        batch = fetch_json(
            f"https://api.github.com/repos/{repo}/tags?per_page=100&page={page}"
        )
        if not batch:
            break
        names.extend(tag["name"] for tag in batch)
        page += 1
        if page > 50:
            break
    return names


def upstream_versions(config: Config) -> List[str]:
    return filter_semver(list_repo_tags(config.upstream_repo))


def mirror_versions(config: Config) -> List[str]:
    """Versions with a final release here (tags like `26.11.0`).

    `pre-` tags deliberately don't count: a version is only finished once its
    PR merges and the final release is published. Until then it stays a
    candidate, so a later run can resume it if an earlier one failed partway.
    """
    return filter_semver(list_repo_tags(config.mirror_repo))


def branch_exists(branch: str) -> bool:
    """True iff `branch` exists on origin. Raises on any git failure other than
    "no such branch", so a network or auth error is never read as "absent"."""
    proc = subprocess.run(
        ["git", "ls-remote", "--exit-code", "--heads", "origin", branch],
        stdout=subprocess.DEVNULL,
    )
    if proc.returncode == 0:
        return True
    if proc.returncode == 2:  # --exit-code: no matching refs
        return False
    raise MirrorError(f"git ls-remote failed for {branch} (exit {proc.returncode})")


def find_pr(repo: str, branch: str) -> Optional[dict]:
    """The PR (any state) whose head is `branch`, as {number, state}, or None."""
    out = capture([
        "gh", "pr", "list",
        "--repo", repo,
        "--head", branch,
        "--state", "all",
        "--limit", "1",
        "--json", "number,state",
    ])
    prs = json.loads(out or "[]")
    return prs[0] if prs else None


def find_release(repo: str, tag: str) -> Optional[dict]:
    """The release (including drafts) for `tag`, as {id, draft, assets}, where
    `assets` lists the names of fully uploaded assets. None if there is none."""
    out = capture([
        "gh", "api", "--paginate", f"repos/{repo}/releases",
        "--jq",
        f'.[] | select(.tag_name == "{tag}") | {{id, draft, '
        f'assets: [.assets[] | select(.state == "uploaded") | .name]}}',
    ])
    lines = [line for line in out.splitlines() if line.strip()]
    return json.loads(lines[0]) if lines else None


def release_complete(release: Optional[dict], version: str) -> bool:
    """True iff `release` is published with both framework zips attached."""
    if not release or release["draft"]:
        return False
    assets = set(release["assets"])
    return kit_asset_name(version) in assets and ui_asset_name(version) in assets


# ---------------------------------------------------------------------------
# Side-effecting flow
# ---------------------------------------------------------------------------

def _release_notes(config: Config, version: str) -> str:
    return (
        f"Framework binaries for upstream {config.upstream_repo} {version}, "
        f"re-hosted via GitHub. This prerelease backs the `{version}` release "
        f"and must not be marked as latest."
    )


def _pr_body(config: Config, version: str, pre_tag: str, base: str) -> str:
    stacked = ""
    if base != "origin/main":
        stacked = (
            f"\n\nThis branch is stacked on `{base.removeprefix('origin/')}`, so "
            f"the diff includes the earlier version(s) too. Merge the earlier PR "
            f"first."
        )
    return (
        f"Automated mirror of upstream `{config.upstream_repo}` **{version}**.\n\n"
        f"- `Package.swift` now points at the "
        f"[`{pre_tag}`](https://github.com/{config.mirror_repo}/releases/tag/{pre_tag}) "
        f"release assets.\n"
        f"- `swift-tools-version` and `platforms` copied from upstream's "
        f"`Package.swift`.\n"
        f"- Prerelease `{pre_tag}` created with the framework zips attached "
        f"(not marked latest).\n"
        f"- Downloaded bytes verified against the checksums declared in upstream's "
        f"`Package.swift`.\n\n"
        f"When this PR is merged, the `{version}` release will be published "
        f"automatically, and marked latest if it is the newest version."
        f"{stacked}"
    )


def mirror_version(version: str, config: Config, base: str = "origin/main") -> Optional[str]:
    """Mirror one version, resuming from whatever an earlier failed run left.

    Each step checks for its own output before acting, so the version is only
    "done" once its PR exists. Returns the remote branch the next version should
    stack on (`origin/feature/<version>`), or None if this version's PR is
    already merged or closed and the next version should keep `base`.
    """
    pre_tag = f"pre-{version}"
    branch = f"feature/{version}"

    log(f"[{version}] starting")

    pr = find_pr(config.mirror_repo, branch)
    if pr:
        log(f"[{version}] PR #{pr['number']} already exists ({pr['state'].lower()}), nothing to do")
        return f"origin/{branch}" if pr["state"] == "OPEN" else None

    # 1. Read upstream Package.swift for this tag.
    log(f"[{version}] reading upstream Package.swift")
    upstream_text = fetch_text(
        f"https://raw.githubusercontent.com/{config.upstream_repo}/{version}/Package.swift"
    )
    targets = manifest_targets_by_name(upstream_text)
    kit = targets.get("PSPDFKit")
    ui = targets.get("PSPDFKitUI")
    if not (kit and ui and kit.url and ui.url and kit.checksum and ui.checksum):
        raise MirrorError(
            f"[{version}] failed to parse PSPDFKit/PSPDFKitUI targets from "
            f"upstream Package.swift"
        )

    kit_asset = kit_asset_name(version)
    ui_asset = ui_asset_name(version)
    release = find_release(config.mirror_repo, pre_tag)
    need_release = not release_complete(release, version)

    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        kit_path = tmp / kit_asset
        ui_path = tmp / ui_asset

        if need_release:
            # 2. Download the frameworks under this repo's asset naming convention.
            log(f"[{version}] downloading frameworks")
            download_file(kit.url, kit_path)
            download_file(ui.url, ui_path)

            # 3. Verify before touching git or GitHub. The SPM binaryTarget
            #    checksum is the SHA-256 of the archive, so re-hosting the
            #    identical bytes preserves it. A mismatch means the file changed
            #    in transit (or upstream lied), so abort.
            if not verify_checksum(kit_path, kit.checksum):
                raise MirrorError(
                    f"[{version}] PSPDFKit checksum mismatch against upstream {kit.checksum}"
                )
            if not verify_checksum(ui_path, ui.checksum):
                raise MirrorError(
                    f"[{version}] PSPDFKitUI checksum mismatch against upstream {ui.checksum}"
                )
            log(f"[{version}] checksums verified")

        # 4. The feature branch with an updated Package.swift, stacked on `base`.
        if branch_exists(branch):
            run(["git", "fetch", "origin", branch])
            existing = capture(["git", "show", f"origin/{branch}:{MANIFEST_PATH}"])
            if f"/releases/download/{pre_tag}/" not in existing:
                raise MirrorError(
                    f"[{version}] branch {branch} exists but its Package.swift does not "
                    f"point at {pre_tag}; fix or delete the branch and re-run"
                )
            log(f"[{version}] reusing existing branch {branch}")
        else:
            log(f"[{version}] creating branch {branch} from {base}")
            run(["git", "fetch", "origin", base.removeprefix("origin/")])
            run(["git", "checkout", "-B", branch, base])
            rewritten = rewrite_manifest(
                Path(MANIFEST_PATH).read_text(),
                config.mirror_repo,
                pre_tag,
                kit_asset,
                kit.checksum,
                ui_asset,
                ui.checksum,
            )
            rewritten = sync_toolchain(rewritten, upstream_text)
            Path(MANIFEST_PATH).write_text(rewritten)
            run(["git", "add", MANIFEST_PATH])
            run(["git", "commit", "-m", f"Mirror PSPDFKit-SP {version}"])
            run(["git", "push", "origin", branch])

        # 5. Prerelease with the framework zips attached. Marked as a prerelease
        #    so it is never resolved as "latest"; the `pre-` tag prefix keeps SPM
        #    from resolving it as a package version. A draft or a release missing
        #    an asset is left over from a failed upload, so replace it.
        if need_release:
            if release:
                log(f"[{version}] deleting incomplete release {pre_tag} (id {release['id']})")
                run(["gh", "api", "-X", "DELETE", f"repos/{config.mirror_repo}/releases/{release['id']}"])
            log(f"[{version}] creating prerelease {pre_tag}")
            run([
                "gh", "release", "create", pre_tag,
                str(kit_path), str(ui_path),
                "--repo", config.mirror_repo,
                "--target", branch,
                "--title", pre_tag,
                "--prerelease",
                "--notes", _release_notes(config, version),
            ])
        else:
            log(f"[{version}] reusing existing prerelease {pre_tag}")

    # 6. Open the pull request last: its existence marks the version as done.
    log(f"[{version}] opening pull request")
    run([
        "gh", "pr", "create",
        "--repo", config.mirror_repo,
        "--base", "main",
        "--head", branch,
        "--title", f"Mirror PSPDFKit-SP {version}",
        "--body", _pr_body(config, version, pre_tag, base),
    ])

    log(f"[{version}] done")
    return f"origin/{branch}"


# ---------------------------------------------------------------------------
# Publishing final releases (run by the publish-release workflow on main)
# ---------------------------------------------------------------------------

def manifest_commit(version: str) -> Optional[str]:
    """The newest commit reachable from HEAD whose Package.swift points at
    `pre-<version>`, or None if the version never reached this history."""
    needle = f"/releases/download/pre-{version}/"
    shas = ["HEAD"] + capture(["git", "log", "--full-history", "--format=%H", "HEAD", "--", MANIFEST_PATH]).split()
    for sha in shas:
        if needle in capture(["git", "show", f"{sha}:{MANIFEST_PATH}"]):
            return capture(["git", "rev-parse", sha])
    return None


def latest_release(repo: str) -> str:
    return capture(["gh", "release", "view", "--repo", repo, "--json", "tagName", "--jq", ".tagName"])


def publish_releases(config: Config) -> int:
    """Publish a final `<version>` release for every `pre-<version>` tag that
    has none yet and whose manifest has reached HEAD (main).

    Scanning every pre- tag, rather than only the version HEAD points at, covers
    stacked PRs merged out of order and publish runs that GitHub cancelled. Only
    the newest version is marked latest, and only when it is newer than the
    current latest release, so a backport never moves "latest" backwards.
    """
    tags = list_repo_tags(config.mirror_repo)
    finals = set(filter_semver(tags))
    pre = filter_semver(t[len("pre-"):] for t in tags if t.startswith("pre-"))
    pending = {}
    for version in pre:
        if version in finals:
            continue
        sha = manifest_commit(version)
        if sha:
            pending[version] = sha
        else:
            log(f"pre-{version} has not reached main yet, skipping")

    if not pending:
        log("No releases to publish.")
        return 0

    current = latest_release(config.mirror_repo)
    newest = max(pending, key=parse_version)
    for version in sorted(pending, key=parse_version):
        latest = version == newest and (
            not SEMVER_RE.match(current) or version_gt(version, current)
        )
        log(f"Publishing release {version} at {pending[version]} (latest: {latest})")
        run([
            "gh", "release", "create", version,
            "--repo", config.mirror_repo,
            "--target", pending[version],
            "--title", version,
            f"--latest={'true' if latest else 'false'}",
            "--generate-notes",
        ])
    return 0


def dry_run_plan(versions: Sequence[str], config: Config) -> None:
    """Print, without side effects, what would be mirrored."""
    for version in versions:
        try:
            manifest_text = fetch_text(
                f"https://raw.githubusercontent.com/{config.upstream_repo}/{version}/Package.swift"
            )
        except (urllib.error.URLError, OSError) as exc:
            log(f"[dry-run] {version}: could not fetch upstream Package.swift ({exc})")
            continue
        print(f"[dry-run] {version}:")
        print(f"    branch : feature/{version}")
        print(f"    tag    : pre-{version} (prerelease, not latest)")
        print(f"    assets : {kit_asset_name(version)}, {ui_asset_name(version)}")
        for target in parse_manifest(manifest_text):
            print(f"    source {target.name}: {target.url}")
            print(f"           checksum: {target.checksum}")


def build_config() -> Config:
    upstream = os.environ.get("UPSTREAM_REPO") or UPSTREAM_REPO_DEFAULT
    mirror = os.environ.get("MIRROR_REPO")
    if not mirror:
        mirror = capture(["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"])
    return Config(
        upstream_repo=upstream,
        mirror_repo=mirror,
        force_version=(os.environ.get("FORCE_VERSION") or "").strip(),
        dry_run=os.environ.get("DRY_RUN") == "1",
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(argv or [])
    config = build_config()

    if argv[:1] == ["publish"]:
        return publish_releases(config)

    ups = upstream_versions(config)
    log(f"Upstream {config.upstream_repo} has {len(ups)} version tags")

    if config.force_version:
        if not SEMVER_RE.match(config.force_version) or config.force_version not in ups:
            log(f"FORCE_VERSION {config.force_version!r} is not an upstream version tag.")
            return 1
        log(f"FORCE_VERSION set, mirroring only {config.force_version}")
        candidates = [config.force_version]
    else:
        mirrors = mirror_versions(config)
        if not mirrors:
            log("Could not determine any existing mirror version. Refusing to mirror the")
            log("entire upstream tag history. Run the workflow manually with a 'version'")
            log("input to seed the first release.")
            return 1
        mirror_max = mirrors[-1]  # filter_semver returns ascending order
        log(f"Newest version released here: {mirror_max}")
        candidates = compute_candidates(mirror_max, ups)

    if not candidates:
        log("No new upstream versions to mirror. Nothing to do.")
        return 0
    log(f"Versions to mirror: {' '.join(candidates)}")

    if config.dry_run:
        log("DRY_RUN=1, so no branches, releases, or pull requests will be created.")
        dry_run_plan(candidates, config)
        return 0

    # Process oldest-to-newest, stacking each branch on the previous open one so
    # the PRs don't conflict with each other.
    base = "origin/main"
    for version in candidates:
        base = mirror_version(version, config, base) or base

    log("All done.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
