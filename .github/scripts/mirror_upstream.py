#!/usr/bin/env python3
"""Mirror new releases of the upstream PSPDFKit-SP package into this repository.

For every upstream version tag that is newer than the newest version already
mirrored here, this script:
  1. reads the upstream Package.swift at that tag to learn the framework
     download URLs and their SHA-256 checksums,
  2. downloads the two framework zips (PSPDFKit + PSPDFKitUI),
  3. verifies the downloaded bytes against the upstream checksums,
  4. creates a `feature/<version>` branch with an updated Package.swift that
     points at this repo's own release assets,
  5. creates a `pre-<version>` prerelease (NOT marked latest) with the two
     zips attached, and
  6. opens a pull request against `main`.

The final `<version>` release (marked latest) is published by the
publish-release workflow once the pull request is merged.

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


def run_ok(args: Sequence[str]) -> bool:
    """Run a command purely for its exit status, suppressing all output."""
    return subprocess.run(
        list(args), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ).returncode == 0


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
    run(["curl", "-fSL", url, "-o", str(dest)])


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
    """Mirror versions come from tags like `26.11.0` or `pre-26.11.0`."""
    stripped = [re.sub(r"^pre-", "", tag) for tag in list_repo_tags(config.mirror_repo)]
    return filter_semver(stripped)


def ref_exists(repo: str, tag: str) -> bool:
    return run_ok(["gh", "api", f"repos/{repo}/git/ref/tags/{tag}"])


def branch_exists(branch: str) -> bool:
    return run_ok(["git", "ls-remote", "--exit-code", "--heads", "origin", branch])


# ---------------------------------------------------------------------------
# Side-effecting flow
# ---------------------------------------------------------------------------

def _release_notes(config: Config, version: str) -> str:
    return (
        f"Framework binaries for upstream {config.upstream_repo} {version}, "
        f"re-hosted via GitHub. This prerelease backs the `{version}` release "
        f"and must not be marked as latest."
    )


def _pr_body(config: Config, version: str, pre_tag: str) -> str:
    return (
        f"Automated mirror of upstream `{config.upstream_repo}` **{version}**.\n\n"
        f"- `Package.swift` now points at the "
        f"[`{pre_tag}`](https://github.com/{config.mirror_repo}/releases/tag/{pre_tag}) "
        f"release assets.\n"
        f"- Prerelease `{pre_tag}` created with the framework zips attached "
        f"(not marked latest).\n"
        f"- Downloaded bytes verified against the checksums declared in upstream's "
        f"`Package.swift`.\n\n"
        f"When this PR is merged, the `{version}` release will be published and "
        f"marked latest automatically."
    )


def mirror_version(version: str, config: Config) -> None:
    pre_tag = f"pre-{version}"
    branch = f"feature/{version}"

    log(f"[{version}] starting")

    # Idempotency: skip anything already in progress or done.
    if ref_exists(config.mirror_repo, pre_tag):
        log(f"[{version}] tag {pre_tag} already exists — skipping")
        return
    if branch_exists(branch):
        log(f"[{version}] branch {branch} already exists — skipping")
        return

    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)

        # 1. Read upstream Package.swift for this tag.
        log(f"[{version}] reading upstream Package.swift")
        manifest_text = fetch_text(
            f"https://raw.githubusercontent.com/{config.upstream_repo}/{version}/Package.swift"
        )
        targets = manifest_targets_by_name(manifest_text)
        kit = targets.get("PSPDFKit")
        ui = targets.get("PSPDFKitUI")
        if not (kit and ui and kit.url and ui.url and kit.checksum and ui.checksum):
            raise MirrorError(
                f"[{version}] failed to parse PSPDFKit/PSPDFKitUI targets from "
                f"upstream Package.swift"
            )

        # 2. Download the frameworks under this repo's asset naming convention.
        kit_asset = kit_asset_name(version)
        ui_asset = ui_asset_name(version)
        kit_path = tmp / kit_asset
        ui_path = tmp / ui_asset
        log(f"[{version}] downloading frameworks")
        download_file(kit.url, kit_path)
        download_file(ui.url, ui_path)

        # 3. Verify. The SPM binaryTarget checksum is the SHA-256 of the archive,
        #    so re-hosting the identical bytes preserves it. A mismatch means the
        #    file changed in transit (or upstream lied) — abort.
        if not verify_checksum(kit_path, kit.checksum):
            raise MirrorError(
                f"[{version}] PSPDFKit checksum mismatch against upstream {kit.checksum}"
            )
        if not verify_checksum(ui_path, ui.checksum):
            raise MirrorError(
                f"[{version}] PSPDFKitUI checksum mismatch against upstream {ui.checksum}"
            )
        log(f"[{version}] checksums verified")

        # 4. Create the feature branch with an updated Package.swift.
        log(f"[{version}] creating branch {branch}")
        run(["git", "checkout", "-B", branch, "origin/main"])
        rewritten = rewrite_manifest(
            Path(MANIFEST_PATH).read_text(),
            config.mirror_repo,
            pre_tag,
            kit_asset,
            kit.checksum,
            ui_asset,
            ui.checksum,
        )
        Path(MANIFEST_PATH).write_text(rewritten)
        run(["git", "add", MANIFEST_PATH])
        run(["git", "commit", "-m", f"Mirror PSPDFKit-SP {version}"])
        run(["git", "push", "origin", branch])

        # 5. Prerelease with the framework zips attached. Marked as a prerelease
        #    so it is never resolved as "latest"; the `pre-` tag prefix keeps SPM
        #    from resolving it as a package version.
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

        # 6. Open the pull request.
        log(f"[{version}] opening pull request")
        run([
            "gh", "pr", "create",
            "--repo", config.mirror_repo,
            "--base", "main",
            "--head", branch,
            "--title", f"Mirror PSPDFKit-SP {version}",
            "--body", _pr_body(config, version, pre_tag),
        ])

        log(f"[{version}] done")


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
        force_version=os.environ.get("FORCE_VERSION") or "",
        dry_run=os.environ.get("DRY_RUN") == "1",
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    config = build_config()

    ups = upstream_versions(config)
    log(f"Upstream {config.upstream_repo} has {len(ups)} version tags")

    if config.force_version:
        log(f"FORCE_VERSION set — mirroring only {config.force_version}")
        candidates = [config.force_version]
    else:
        mirrors = mirror_versions(config)
        if not mirrors:
            log("Could not determine any existing mirror version. Refusing to mirror the")
            log("entire upstream tag history. Run the workflow manually with a 'version'")
            log("input to seed the first release.")
            return 1
        mirror_max = mirrors[-1]  # filter_semver returns ascending order
        log(f"Newest version already mirrored: {mirror_max}")
        candidates = compute_candidates(mirror_max, ups)

    if not candidates:
        log("No new upstream versions to mirror. Nothing to do.")
        return 0
    log(f"Versions to mirror: {' '.join(candidates)}")

    if config.dry_run:
        log("DRY_RUN=1 — no branches, releases, or pull requests will be created.")
        dry_run_plan(candidates, config)
        return 0

    # Process oldest-to-newest so releases land in order.
    for version in candidates:
        mirror_version(version, config)

    log("All done.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
