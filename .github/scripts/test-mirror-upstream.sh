#!/usr/bin/env bash
#
# Tests for the pure/logic parts of mirror-upstream.sh.
#
# Sourcing mirror-upstream.sh defines its functions without running anything
# (the script guards its entrypoint), so we can exercise them in isolation.
# The side-effecting steps (git push, gh release/pr create, 221 MB downloads)
# are intentionally NOT tested here — they only prove out on a real Actions run.
#
# Usage: bash .github/scripts/test-mirror-upstream.sh
#
set -o pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
# shellcheck source=mirror-upstream.sh
source "$HERE/mirror-upstream.sh"

pass=0
fail=0
ok()  { printf '  \033[32mok\033[0m   %s\n' "$1"; pass=$((pass + 1)); }
no()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=$((fail + 1)); }

assert_eq()   { if [[ "$2" == "$3" ]]; then ok "$1"; else no "$1 — expected [$2], got [$3]"; fi; }
assert_ok()   { local d="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$d"; else no "$d — command failed: $*"; fi; }
assert_fail() { local d="$1"; shift; if "$@" >/dev/null 2>&1; then no "$d — expected failure, but it succeeded"; else ok "$d"; fi; }

echo "== version_gt =="
assert_ok   "26.12.0 > 26.11.0"                 version_gt 26.12.0 26.11.0
assert_fail "26.11.0 > 26.11.0 (equal)"         version_gt 26.11.0 26.11.0
assert_fail "26.9.0  > 26.11.0"                 version_gt 26.9.0  26.11.0
assert_ok   "26.11.1 > 26.11.0 (patch)"         version_gt 26.11.1 26.11.0
assert_ok   "27.0.0  > 26.11.0 (major)"         version_gt 27.0.0  26.11.0
assert_fail "26.2.0  > 26.10.0 (numeric order)" version_gt 26.2.0  26.10.0

echo "== filter_semver =="
got="$(printf '26.11.0\npre-26.11.0\n26.10.0\nv1.2.3\nfoo\n14.8.0\n26.10.0\n' | filter_semver | paste -sd, -)"
assert_eq "keeps semver only, sorted, de-duped" "14.8.0,26.10.0,26.11.0" "$got"

echo "== compute_candidates =="
UP=$'26.7.0\n26.8.0\n26.9.0\n26.10.0\n26.11.0'
assert_eq "newer than 26.9.0"            "26.10.0,26.11.0" "$(printf '%s\n' "$UP" | compute_candidates 26.9.0  | paste -sd, -)"
assert_eq "newer than 26.11.0 (none)"    ""                "$(printf '%s\n' "$UP" | compute_candidates 26.11.0 | paste -sd, -)"
assert_eq "patch + major bumps included" "26.11.1,27.0.0"  "$(printf '26.11.0\n26.11.1\n27.0.0\n' | compute_candidates 26.11.0 | paste -sd, -)"

echo "== asset names =="
assert_eq "kit asset name" "Nutrient-iOS-SDK-PSPDFKit.xcframework-26.11.0.zip"   "$(kit_asset_name 26.11.0)"
assert_eq "ui  asset name" "Nutrient-iOS-SDK-PSPDFKitUI.xcframework-26.11.0.zip" "$(ui_asset_name 26.11.0)"

echo "== parse_manifest (fixture) =="
fixture="$(mktemp)"
cat > "$fixture" <<'EOF'
// swift-tools-version: 6.3
import PackageDescription
let package = Package(
    name: "Nutrient",
    targets: [
        .binaryTarget(
            name: "PSPDFKit",
            url: "https://my.nutrient.io/pspdfkit-xcframework-26.11.0.zip",
            checksum: "672218cfb02b615b89ccdc640aaa957743b8fcb0358e57b5bc136df0c6133f01"),
        .binaryTarget(
            name: "PSPDFKitUI",
            url: "https://my.nutrient.io/pspdfkitui-xcframework-26.11.0.zip",
            checksum: "6c1ffa9d4e79cccf821bfefa2d166da2841e8231f4751876c614a925547e8b4f"),
    ]
)
EOF
parsed="$(parse_manifest "$fixture")"
assert_eq "PSPDFKit url"      "https://my.nutrient.io/pspdfkit-xcframework-26.11.0.zip"          "$(awk -F'\t' '$1=="PSPDFKit"{print $2}'   <<<"$parsed")"
assert_eq "PSPDFKit sum"      "672218cfb02b615b89ccdc640aaa957743b8fcb0358e57b5bc136df0c6133f01" "$(awk -F'\t' '$1=="PSPDFKit"{print $3}'   <<<"$parsed")"
assert_eq "PSPDFKitUI url"    "https://my.nutrient.io/pspdfkitui-xcframework-26.11.0.zip"        "$(awk -F'\t' '$1=="PSPDFKitUI"{print $2}' <<<"$parsed")"
assert_eq "PSPDFKitUI sum"    "6c1ffa9d4e79cccf821bfefa2d166da2841e8231f4751876c614a925547e8b4f" "$(awk -F'\t' '$1=="PSPDFKitUI"{print $3}' <<<"$parsed")"
rm -f "$fixture"

echo "== rewrite_manifest (against real Package.swift) =="
work="$(mktemp -d)"
cp "$REPO_ROOT/Package.swift" "$work/Package.swift"
repo="MFB-Technologies-Inc/PSPDFKit-SP-Mirror"
assert_ok "rewrite exits 0" rewrite_manifest "$work/Package.swift" "$repo" "pre-26.12.0" \
  "Nutrient-iOS-SDK-PSPDFKit.xcframework-26.12.0.zip"   "aaaakitsum" \
  "Nutrient-iOS-SDK-PSPDFKitUI.xcframework-26.12.0.zip" "bbbbuisum"
assert_ok   "kit url rewritten"     grep -q "releases/download/pre-26.12.0/Nutrient-iOS-SDK-PSPDFKit.xcframework-26.12.0.zip"   "$work/Package.swift"
assert_ok   "ui  url rewritten"     grep -q "releases/download/pre-26.12.0/Nutrient-iOS-SDK-PSPDFKitUI.xcframework-26.12.0.zip" "$work/Package.swift"
assert_ok   "kit checksum rewritten" grep -q 'checksum: "aaaakitsum"' "$work/Package.swift"
assert_ok   "ui  checksum rewritten" grep -q 'checksum: "bbbbuisum"'  "$work/Package.swift"
assert_fail "no leftover old version" grep -q "pre-26.11.0" "$work/Package.swift"
# There must be exactly two binaryTarget URLs — proves we didn't duplicate/mangle.
assert_eq "still exactly 2 targets" "2" "$(grep -c 'releases/download/pre-26.12.0/' "$work/Package.swift")"

# Rewrite must fail loudly if an expected target is missing (guards silent no-ops).
onlyone="$(mktemp -d)"
cat > "$onlyone/Package.swift" <<'EOF'
    targets: [
        .binaryTarget(
            name: "PSPDFKit",
            url: "https://example.com/old.zip",
            checksum: "old"),
    ]
EOF
assert_fail "rewrite fails when PSPDFKitUI target absent" rewrite_manifest "$onlyone/Package.swift" "$repo" "pre-1.0.0" a x b y
rm -rf "$work" "$onlyone"

echo "== verify_checksum =="
blob="$(mktemp)"; printf 'nutrient-mirror-test' > "$blob"
realsum="$(sha256sum "$blob" | awk '{print $1}')"
assert_ok   "accepts matching checksum"  verify_checksum "$blob" "$realsum"
assert_fail "rejects wrong checksum"     verify_checksum "$blob" "0000000000000000000000000000000000000000000000000000000000000000"
rm -f "$blob"

echo "== integration (live public GitHub) =="
if curl -fsS -o /dev/null --max-time 15 https://api.github.com; then
  UPSTREAM_REPO="PSPDFKit/PSPDFKit-SP"
  MIRROR_REPO="MFB-Technologies-Inc/PSPDFKit-SP-Mirror"

  mapfile -t UV < <(upstream_versions)
  assert_ok   "upstream_versions returns tags"     test "${#UV[@]}" -gt 5
  assert_ok   "upstream list includes 26.11.0"     bash -c "printf '%s\n' \"\${@}\" | grep -qx 26.11.0" _ "${UV[@]}"
  assert_eq   "upstream list is all semver"        "" "$(printf '%s\n' "${UV[@]}" | grep -vE "$SEMVER")"

  # Full discovery pipeline against live data: mirror is currently at the
  # upstream tip, so there should be nothing new to mirror.
  mm="$(mirror_versions | sort -V | tail -n1)"
  assert_ok   "mirror max is a semver"             bash -c "[[ '$mm' =~ $SEMVER ]]"
  assert_eq   "no new versions right now"          "" "$(printf '%s\n' "${UV[@]}" | compute_candidates "$mm" | paste -sd, -)"

  # Live upstream manifest parse.
  live="$(mktemp)"
  if curl -fsSL --max-time 20 "https://raw.githubusercontent.com/$UPSTREAM_REPO/26.11.0/Package.swift" -o "$live"; then
    lp="$(parse_manifest "$live")"
    assert_eq "live PSPDFKit checksum @26.11.0" "672218cfb02b615b89ccdc640aaa957743b8fcb0358e57b5bc136df0c6133f01" "$(awk -F'\t' '$1=="PSPDFKit"{print $3}' <<<"$lp")"
  else
    no "could not fetch live upstream Package.swift"
  fi
  rm -f "$live"
else
  echo "  -- skipped (no network) --"
fi

echo
echo "Result: ${pass} passed, ${fail} failed"
[[ $fail -eq 0 ]]
