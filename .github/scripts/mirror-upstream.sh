#!/usr/bin/env bash
#
# Mirror new releases of the upstream PSPDFKit-SP package into this repository.
#
# For every upstream version tag that is newer than the newest version already
# mirrored here, this script:
#   1. reads the upstream Package.swift at that tag to learn the framework
#      download URLs and their SHA-256 checksums,
#   2. downloads the two framework zips (PSPDFKit + PSPDFKitUI),
#   3. verifies the downloaded bytes against the upstream checksums,
#   4. creates a `feature/<version>` branch with an updated Package.swift that
#      points at this repo's own release assets,
#   5. creates a `pre-<version>` prerelease (NOT marked latest) with the two
#      zips attached, and
#   6. opens a pull request against `main`.
#
# The final `<version>` release (marked latest) is published by the
# publish-release workflow once the pull request is merged.
#
# Configuration comes from the environment:
#   UPSTREAM_REPO   upstream owner/repo               (default: PSPDFKit/PSPDFKit-SP)
#   MIRROR_REPO     this owner/repo                   (default: derived from `gh`)
#   FORCE_VERSION   mirror only this version, ignore  (optional)
#                   the "newer than mirror" check
#   DRY_RUN         if set to 1, print the plan and make no changes (optional)
#   GH_TOKEN        token used by `gh` and `git push`
#
# The logic is split into small functions so it can be unit-tested without
# performing any side effects; see test-mirror-upstream.sh. Sourcing this file
# defines the functions but does not run anything.

UPSTREAM_REPO="${UPSTREAM_REPO:-PSPDFKit/PSPDFKit-SP}"
FORCE_VERSION="${FORCE_VERSION:-}"
DRY_RUN="${DRY_RUN:-}"

# Matches a plain semantic version tag, e.g. 26.11.0
SEMVER='^[0-9]+\.[0-9]+\.[0-9]+$'

log() { echo "==> $*" >&2; }

# ---------------------------------------------------------------------------
# Pure helpers (no side effects, unit-testable)
# ---------------------------------------------------------------------------

# Returns 0 if $1 is strictly greater than $2 (semver-ish, via `sort -V`).
version_gt() {
  [[ "$1" != "$2" ]] && [[ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -n1)" == "$1" ]]
}

# Reads lines on stdin, keeps only plain semver tags, sorted and de-duplicated.
filter_semver() {
  grep -E "$SEMVER" | sort -V -u
}

# Given a mirror-max version ($1) and a newline-separated list of upstream
# versions on stdin, prints (ascending) those strictly newer than the mirror.
compute_candidates() {
  local mirror_max="$1" v
  while IFS= read -r v; do
    [[ -z "$v" ]] && continue
    if version_gt "$v" "$mirror_max"; then
      printf '%s\n' "$v"
    fi
  done | sort -V -u
}

# Parses a Package.swift ($1) and prints "<name>\t<url>\t<checksum>" per
# binaryTarget.
parse_manifest() {
  python3 - "$1" <<'PY'
import re, sys
text = open(sys.argv[1]).read()
pat = re.compile(
    r'\.binaryTarget\(\s*name:\s*"([^"]+)"\s*,\s*'
    r'url:\s*"([^"]+)"\s*,\s*checksum:\s*"([^"]+)"'
)
for m in pat.finditer(text):
    print("\t".join(m.groups()))
PY
}

# Rewrites this repo's Package.swift ($1) in place, pointing the PSPDFKit and
# PSPDFKitUI targets at the mirror's release assets. Fails unless each target is
# replaced exactly once.
#   $1 path  $2 repo  $3 pre_tag  $4 kit_asset  $5 kit_sum  $6 ui_asset  $7 ui_sum
rewrite_manifest() {
  python3 - "$@" <<'PY'
import re, sys
path, repo, pre_tag, kit_asset, kit_sum, ui_asset, ui_sum = sys.argv[1:8]
base = f"https://github.com/{repo}/releases/download/{pre_tag}"
text = open(path).read()

def set_target(text, name, url, checksum):
    pat = re.compile(
        r'(\.binaryTarget\(\s*name:\s*"' + re.escape(name) + r'"\s*,\s*'
        r'url:\s*")[^"]+("\s*,\s*checksum:\s*")[^"]+(")'
    )
    text, n = pat.subn(lambda m: m.group(1) + url + m.group(2) + checksum + m.group(3), text)
    if n != 1:
        raise SystemExit(f"expected exactly 1 replacement for {name}, made {n}")
    return text

text = set_target(text, "PSPDFKit",   f"{base}/{kit_asset}", kit_sum)
text = set_target(text, "PSPDFKitUI", f"{base}/{ui_asset}",  ui_sum)
open(path, "w").write(text)
PY
}

# Returns 0 iff the SHA-256 of file $1 equals expected checksum $2.
verify_checksum() {
  local actual
  actual="$(sha256sum "$1" | awk '{print $1}')"
  [[ "$actual" == "$2" ]]
}

# Asset filenames this repo uses for a given version.
kit_asset_name() { printf 'Nutrient-iOS-SDK-PSPDFKit.xcframework-%s.zip' "$1"; }
ui_asset_name()  { printf 'Nutrient-iOS-SDK-PSPDFKitUI.xcframework-%s.zip' "$1"; }

# ---------------------------------------------------------------------------
# GitHub reads (prefer `gh`; fall back to unauthenticated `curl` so the
# discovery logic can be exercised locally without a token)
# ---------------------------------------------------------------------------

list_repo_tags() {
  local repo="$1"
  if [[ -n "${GH_TOKEN:-}" ]] && command -v gh >/dev/null 2>&1; then
    gh api --paginate "repos/${repo}/tags" --jq '.[].name'
  else
    _curl_list_tags "$repo"
  fi
}

_curl_list_tags() {
  local repo="$1" page=1 body
  while :; do
    body="$(curl -fsSL "https://api.github.com/repos/${repo}/tags?per_page=100&page=${page}")" || return 1
    if [[ "$(printf '%s' "$body" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')" == "0" ]]; then
      break
    fi
    printf '%s' "$body" | python3 -c 'import json,sys; [print(t["name"]) for t in json.load(sys.stdin)]'
    page=$((page + 1))
    [[ $page -gt 50 ]] && break
  done
}

upstream_versions() { list_repo_tags "$UPSTREAM_REPO" | filter_semver; }

# Mirror versions come from tags like `26.11.0` or `pre-26.11.0`.
mirror_versions() { list_repo_tags "$MIRROR_REPO" | sed -E 's/^pre-//' | filter_semver; }

# ---------------------------------------------------------------------------
# Side-effecting flow
# ---------------------------------------------------------------------------

mirror_version() {
  local VERSION="$1"
  local PRE_TAG="pre-${VERSION}"
  local BRANCH="feature/${VERSION}"

  log "[${VERSION}] starting"

  # Idempotency: skip anything already in progress or done.
  if gh api "repos/${MIRROR_REPO}/git/ref/tags/${PRE_TAG}" >/dev/null 2>&1; then
    log "[${VERSION}] tag ${PRE_TAG} already exists — skipping"
    return 0
  fi
  if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
    log "[${VERSION}] branch ${BRANCH} already exists — skipping"
    return 0
  fi

  local tmp
  tmp="$(mktemp -d)"
  # shellcheck disable=SC2064
  trap "rm -rf '$tmp'" RETURN

  # 1. Read upstream Package.swift for this tag.
  log "[${VERSION}] reading upstream Package.swift"
  curl -fsSL "https://raw.githubusercontent.com/${UPSTREAM_REPO}/${VERSION}/Package.swift" \
    -o "$tmp/Package.swift"

  local parsed
  parsed="$(parse_manifest "$tmp/Package.swift")"

  local KIT_URL KIT_SUM_UP UI_URL UI_SUM_UP
  KIT_URL="$(awk -F'\t' '$1=="PSPDFKit"{print $2}'   <<<"$parsed")"
  KIT_SUM_UP="$(awk -F'\t' '$1=="PSPDFKit"{print $3}' <<<"$parsed")"
  UI_URL="$(awk -F'\t' '$1=="PSPDFKitUI"{print $2}'   <<<"$parsed")"
  UI_SUM_UP="$(awk -F'\t' '$1=="PSPDFKitUI"{print $3}' <<<"$parsed")"

  if [[ -z "$KIT_URL" || -z "$UI_URL" || -z "$KIT_SUM_UP" || -z "$UI_SUM_UP" ]]; then
    log "[${VERSION}] failed to parse PSPDFKit/PSPDFKitUI targets from upstream Package.swift"
    return 1
  fi

  # 2. Download the frameworks under this repo's asset naming convention.
  local KIT_ASSET UI_ASSET
  KIT_ASSET="$(kit_asset_name "$VERSION")"
  UI_ASSET="$(ui_asset_name "$VERSION")"
  log "[${VERSION}] downloading frameworks"
  curl -fSL "$KIT_URL" -o "$tmp/$KIT_ASSET"
  curl -fSL "$UI_URL"  -o "$tmp/$UI_ASSET"

  # 3. Verify. The SPM binaryTarget checksum is the SHA-256 of the archive, so
  #    re-hosting the identical bytes preserves it. A mismatch means the file
  #    changed in transit (or upstream lied) — abort.
  if ! verify_checksum "$tmp/$KIT_ASSET" "$KIT_SUM_UP"; then
    log "[${VERSION}] PSPDFKit checksum mismatch against upstream ${KIT_SUM_UP}"
    return 1
  fi
  if ! verify_checksum "$tmp/$UI_ASSET" "$UI_SUM_UP"; then
    log "[${VERSION}] PSPDFKitUI checksum mismatch against upstream ${UI_SUM_UP}"
    return 1
  fi
  log "[${VERSION}] checksums verified"

  # 4. Create the feature branch with an updated Package.swift.
  log "[${VERSION}] creating branch ${BRANCH}"
  git checkout -B "$BRANCH" origin/main
  rewrite_manifest "Package.swift" "$MIRROR_REPO" "$PRE_TAG" \
    "$KIT_ASSET" "$KIT_SUM_UP" "$UI_ASSET" "$UI_SUM_UP"
  git add Package.swift
  git commit -m "Mirror PSPDFKit-SP ${VERSION}"
  git push origin "$BRANCH"

  # 5. Prerelease with the framework zips attached. Marked as a prerelease so
  #    it is never resolved as "latest"; the `pre-` tag prefix keeps SPM from
  #    resolving it as a package version.
  log "[${VERSION}] creating prerelease ${PRE_TAG}"
  gh release create "$PRE_TAG" \
    "$tmp/$KIT_ASSET" "$tmp/$UI_ASSET" \
    --repo "$MIRROR_REPO" \
    --target "$BRANCH" \
    --title "$PRE_TAG" \
    --prerelease \
    --notes "Framework binaries for upstream ${UPSTREAM_REPO} ${VERSION}, re-hosted via GitHub. This prerelease backs the \`${VERSION}\` release and must not be marked as latest."

  # 6. Open the pull request.
  log "[${VERSION}] opening pull request"
  gh pr create \
    --repo "$MIRROR_REPO" \
    --base main \
    --head "$BRANCH" \
    --title "Mirror PSPDFKit-SP ${VERSION}" \
    --body "Automated mirror of upstream \`${UPSTREAM_REPO}\` **${VERSION}**.

- \`Package.swift\` now points at the [\`${PRE_TAG}\`](https://github.com/${MIRROR_REPO}/releases/tag/${PRE_TAG}) release assets.
- Prerelease \`${PRE_TAG}\` created with the framework zips attached (not marked latest).
- Downloaded bytes verified against the checksums declared in upstream's \`Package.swift\`.

When this PR is merged, the \`${VERSION}\` release will be published and marked latest automatically."

  log "[${VERSION}] done"
}

# Print, without side effects, what would be mirrored.
dry_run_plan() {
  local version tmp parsed
  for version in "$@"; do
    tmp="$(mktemp -d)"
    if ! curl -fsSL "https://raw.githubusercontent.com/${UPSTREAM_REPO}/${version}/Package.swift" -o "$tmp/Package.swift"; then
      log "[dry-run] ${version}: could not fetch upstream Package.swift"
      rm -rf "$tmp"; continue
    fi
    parsed="$(parse_manifest "$tmp/Package.swift")"
    echo "[dry-run] ${version}:"
    echo "    branch : feature/${version}"
    echo "    tag    : pre-${version} (prerelease, not latest)"
    echo "    assets : $(kit_asset_name "$version"), $(ui_asset_name "$version")"
    while IFS=$'\t' read -r name url checksum; do
      [[ -z "$name" ]] && continue
      echo "    source ${name}: ${url}"
      echo "           checksum: ${checksum}"
    done <<<"$parsed"
    rm -rf "$tmp"
  done
}

main() {
  set -euo pipefail

  MIRROR_REPO="${MIRROR_REPO:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}"

  mapfile -t UPSTREAM_VERSIONS < <(upstream_versions)
  log "Upstream ${UPSTREAM_REPO} has ${#UPSTREAM_VERSIONS[@]} version tags"

  local -a CANDIDATES=()
  if [[ -n "$FORCE_VERSION" ]]; then
    log "FORCE_VERSION set — mirroring only ${FORCE_VERSION}"
    CANDIDATES=("$FORCE_VERSION")
  else
    local -a MIRROR_VERSIONS=()
    mapfile -t MIRROR_VERSIONS < <(mirror_versions)
    if [[ ${#MIRROR_VERSIONS[@]} -eq 0 ]]; then
      log "Could not determine any existing mirror version. Refusing to mirror the"
      log "entire upstream tag history. Run the workflow manually with a 'version'"
      log "input to seed the first release."
      exit 1
    fi
    local MIRROR_MAX
    MIRROR_MAX="$(printf '%s\n' "${MIRROR_VERSIONS[@]}" | sort -V | tail -n1)"
    log "Newest version already mirrored: ${MIRROR_MAX}"
    mapfile -t CANDIDATES < <(printf '%s\n' "${UPSTREAM_VERSIONS[@]}" | compute_candidates "$MIRROR_MAX")
  fi

  if [[ ${#CANDIDATES[@]} -eq 0 ]]; then
    log "No new upstream versions to mirror. Nothing to do."
    exit 0
  fi
  log "Versions to mirror: ${CANDIDATES[*]}"

  if [[ "$DRY_RUN" == "1" ]]; then
    log "DRY_RUN=1 — no branches, releases, or pull requests will be created."
    dry_run_plan "${CANDIDATES[@]}"
    exit 0
  fi

  # Process oldest-to-newest so releases land in order.
  for VERSION in "${CANDIDATES[@]}"; do
    mirror_version "$VERSION"
  done

  log "All done."
}

# Only run when executed directly; sourcing (e.g. from tests) just defines the
# functions above.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
