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
#   GH_TOKEN        token used by `gh` and `git push`
#
set -euo pipefail

UPSTREAM_REPO="${UPSTREAM_REPO:-PSPDFKit/PSPDFKit-SP}"
MIRROR_REPO="${MIRROR_REPO:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}"
FORCE_VERSION="${FORCE_VERSION:-}"

# Matches a plain semantic version tag, e.g. 26.11.0
SEMVER='^[0-9]+\.[0-9]+\.[0-9]+$'

log() { echo "==> $*" >&2; }

# Returns 0 if $1 is strictly greater than $2 (semver-ish, via `sort -V`).
version_gt() {
  [[ "$1" != "$2" ]] && [[ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -n1)" == "$1" ]]
}

# --- Discover candidate versions -------------------------------------------

mapfile -t UPSTREAM_VERSIONS < <(
  gh api --paginate "repos/${UPSTREAM_REPO}/tags" --jq '.[].name' \
    | grep -E "$SEMVER" | sort -V -u
)
log "Upstream ${UPSTREAM_REPO} has ${#UPSTREAM_VERSIONS[@]} version tags"

CANDIDATES=()
if [[ -n "$FORCE_VERSION" ]]; then
  log "FORCE_VERSION set — mirroring only ${FORCE_VERSION}"
  CANDIDATES=("$FORCE_VERSION")
else
  # Newest version already present here (from tags like `26.11.0` or `pre-26.11.0`).
  mapfile -t MIRROR_VERSIONS < <(
    gh api --paginate "repos/${MIRROR_REPO}/tags" --jq '.[].name' \
      | sed -E 's/^pre-//' | grep -E "$SEMVER" | sort -V -u
  )
  if [[ ${#MIRROR_VERSIONS[@]} -eq 0 ]]; then
    log "Could not determine any existing mirror version. Refusing to mirror the"
    log "entire upstream tag history. Run the workflow manually with a 'version'"
    log "input to seed the first release."
    exit 1
  fi
  MIRROR_MAX="$(printf '%s\n' "${MIRROR_VERSIONS[@]}" | sort -V | tail -n1)"
  log "Newest version already mirrored: ${MIRROR_MAX}"

  for v in "${UPSTREAM_VERSIONS[@]}"; do
    if version_gt "$v" "$MIRROR_MAX"; then
      CANDIDATES+=("$v")
    fi
  done
fi

if [[ ${#CANDIDATES[@]} -eq 0 ]]; then
  log "No new upstream versions to mirror. Nothing to do."
  exit 0
fi
log "Versions to mirror: ${CANDIDATES[*]}"

# --- Mirror one version -----------------------------------------------------

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

  # Emit "<name>\t<url>\t<checksum>" for each binaryTarget.
  local parsed
  parsed="$(python3 - "$tmp/Package.swift" <<'PY'
import re, sys
text = open(sys.argv[1]).read()
pat = re.compile(
    r'\.binaryTarget\(\s*name:\s*"([^"]+)"\s*,\s*'
    r'url:\s*"([^"]+)"\s*,\s*checksum:\s*"([^"]+)"'
)
for m in pat.finditer(text):
    print("\t".join(m.groups()))
PY
)"

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
  local KIT_ASSET="Nutrient-iOS-SDK-PSPDFKit.xcframework-${VERSION}.zip"
  local UI_ASSET="Nutrient-iOS-SDK-PSPDFKitUI.xcframework-${VERSION}.zip"
  log "[${VERSION}] downloading frameworks"
  curl -fSL "$KIT_URL" -o "$tmp/$KIT_ASSET"
  curl -fSL "$UI_URL"  -o "$tmp/$UI_ASSET"

  # 3. Verify. The SPM binaryTarget checksum is the SHA-256 of the archive, so
  #    re-hosting the identical bytes preserves it. A mismatch means the file
  #    changed in transit (or upstream lied) — abort.
  local KIT_SUM UI_SUM
  KIT_SUM="$(sha256sum "$tmp/$KIT_ASSET" | awk '{print $1}')"
  UI_SUM="$(sha256sum "$tmp/$UI_ASSET"  | awk '{print $1}')"
  if [[ "$KIT_SUM" != "$KIT_SUM_UP" ]]; then
    log "[${VERSION}] PSPDFKit checksum mismatch: got ${KIT_SUM}, upstream declares ${KIT_SUM_UP}"
    return 1
  fi
  if [[ "$UI_SUM" != "$UI_SUM_UP" ]]; then
    log "[${VERSION}] PSPDFKitUI checksum mismatch: got ${UI_SUM}, upstream declares ${UI_SUM_UP}"
    return 1
  fi
  log "[${VERSION}] checksums verified"

  # 4. Create the feature branch with an updated Package.swift.
  log "[${VERSION}] creating branch ${BRANCH}"
  git checkout -B "$BRANCH" origin/main

  python3 - "$MIRROR_REPO" "$PRE_TAG" "$KIT_ASSET" "$KIT_SUM" "$UI_ASSET" "$UI_SUM" <<'PY'
import re, sys
repo, pre_tag, kit_asset, kit_sum, ui_asset, ui_sum = sys.argv[1:7]
base = f"https://github.com/{repo}/releases/download/{pre_tag}"
path = "Package.swift"
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

# Process oldest-to-newest so releases land in order.
for VERSION in "${CANDIDATES[@]}"; do
  mirror_version "$VERSION"
done

log "All done."
