#!/usr/bin/env python3
"""Tests for mirror_upstream.py.

Importing mirror_upstream defines its functions without running anything (the
module guards its entrypoint), so we exercise them in isolation.

Unlike a shell script sourced for its pure functions, the Python version lets us
also test the *side-effecting* flow: the external process/network seams
(`run`, `download_file`, `fetch_text`, `ref_exists`, ...) are module-level
functions, so we patch them and assert on control flow — idempotency skips, the
checksum-mismatch abort, and command ordering — without touching git, gh, the
network, or the ~200 MB downloads.

Usage:
  python3 .github/scripts/test_mirror_upstream.py            # pure + mocked
  RUN_LIVE_TESTS=1 python3 .github/scripts/test_mirror_upstream.py   # + live GitHub
"""

import contextlib
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mirror_upstream as mu  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

# A manifest matching the shape of a real upstream Package.swift.
FIXTURE_MANIFEST = """// swift-tools-version: 6.3
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
"""

KIT_SUM = "672218cfb02b615b89ccdc640aaa957743b8fcb0358e57b5bc136df0c6133f01"
UI_SUM = "6c1ffa9d4e79cccf821bfefa2d166da2841e8231f4751876c614a925547e8b4f"


@contextlib.contextmanager
def pushd(path):
    prev = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prev)


class PureFunctionTests(unittest.TestCase):
    def test_version_gt(self):
        self.assertTrue(mu.version_gt("26.12.0", "26.11.0"))
        self.assertFalse(mu.version_gt("26.11.0", "26.11.0"))  # equal
        self.assertFalse(mu.version_gt("26.9.0", "26.11.0"))
        self.assertTrue(mu.version_gt("26.11.1", "26.11.0"))   # patch
        self.assertTrue(mu.version_gt("27.0.0", "26.11.0"))    # major
        self.assertFalse(mu.version_gt("26.2.0", "26.10.0"))   # numeric, not lexical

    def test_filter_semver(self):
        got = mu.filter_semver(
            ["26.11.0", "pre-26.11.0", "26.10.0", "v1.2.3", "foo", "14.8.0", "26.10.0"]
        )
        self.assertEqual(got, ["14.8.0", "26.10.0", "26.11.0"])

    def test_compute_candidates(self):
        up = ["26.7.0", "26.8.0", "26.9.0", "26.10.0", "26.11.0"]
        self.assertEqual(mu.compute_candidates("26.9.0", up), ["26.10.0", "26.11.0"])
        self.assertEqual(mu.compute_candidates("26.11.0", up), [])
        self.assertEqual(
            mu.compute_candidates("26.11.0", ["26.11.0", "26.11.1", "27.0.0"]),
            ["26.11.1", "27.0.0"],
        )

    def test_asset_names(self):
        self.assertEqual(
            mu.kit_asset_name("26.11.0"),
            "Nutrient-iOS-SDK-PSPDFKit.xcframework-26.11.0.zip",
        )
        self.assertEqual(
            mu.ui_asset_name("26.11.0"),
            "Nutrient-iOS-SDK-PSPDFKitUI.xcframework-26.11.0.zip",
        )

    def test_parse_manifest(self):
        targets = mu.manifest_targets_by_name(FIXTURE_MANIFEST)
        self.assertEqual(
            targets["PSPDFKit"].url,
            "https://my.nutrient.io/pspdfkit-xcframework-26.11.0.zip",
        )
        self.assertEqual(targets["PSPDFKit"].checksum, KIT_SUM)
        self.assertEqual(
            targets["PSPDFKitUI"].url,
            "https://my.nutrient.io/pspdfkitui-xcframework-26.11.0.zip",
        )
        self.assertEqual(targets["PSPDFKitUI"].checksum, UI_SUM)


class RewriteManifestTests(unittest.TestCase):
    def test_rewrite_against_real_manifest(self):
        original = (REPO_ROOT / "Package.swift").read_text()
        repo = "MFB-Technologies-Inc/PSPDFKit-SP-Mirror"
        out = mu.rewrite_manifest(
            original, repo, "pre-26.12.0",
            "Nutrient-iOS-SDK-PSPDFKit.xcframework-26.12.0.zip", "aaaakitsum",
            "Nutrient-iOS-SDK-PSPDFKitUI.xcframework-26.12.0.zip", "bbbbuisum",
        )
        self.assertIn(
            "releases/download/pre-26.12.0/Nutrient-iOS-SDK-PSPDFKit.xcframework-26.12.0.zip",
            out,
        )
        self.assertIn(
            "releases/download/pre-26.12.0/Nutrient-iOS-SDK-PSPDFKitUI.xcframework-26.12.0.zip",
            out,
        )
        self.assertIn('checksum: "aaaakitsum"', out)
        self.assertIn('checksum: "bbbbuisum"', out)
        self.assertNotIn("pre-26.11.0", out)  # no leftover old version
        # Exactly two rewritten targets — proves we didn't duplicate/mangle.
        self.assertEqual(out.count("releases/download/pre-26.12.0/"), 2)

    def test_rewrite_fails_when_target_absent(self):
        only_one = (
            '    targets: [\n'
            '        .binaryTarget(\n'
            '            name: "PSPDFKit",\n'
            '            url: "https://example.com/old.zip",\n'
            '            checksum: "old"),\n'
            '    ]\n'
        )
        with self.assertRaises(mu.MirrorError):
            mu.rewrite_manifest(only_one, "repo", "pre-1.0.0", "a", "x", "b", "y")


class ChecksumTests(unittest.TestCase):
    def test_sha256_and_verify(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"nutrient-mirror-test")
            path = f.name
        try:
            real = hashlib.sha256(b"nutrient-mirror-test").hexdigest()
            self.assertEqual(mu.sha256_file(path), real)
            self.assertTrue(mu.verify_checksum(path, real))
            self.assertFalse(mu.verify_checksum(path, "0" * 64))
        finally:
            os.unlink(path)


class MirrorVersionFlowTests(unittest.TestCase):
    """The payoff of the port: the side-effecting flow is now testable."""

    def _config(self):
        return mu.Config(upstream_repo="up/stream", mirror_repo="mirror/repo")

    @mock.patch.object(mu, "run")
    @mock.patch.object(mu, "download_file")
    @mock.patch.object(mu, "fetch_text")
    @mock.patch.object(mu, "branch_exists", return_value=False)
    @mock.patch.object(mu, "ref_exists", return_value=True)
    def test_skips_when_tag_exists(self, ref, branch, fetch, dl, run):
        mu.mirror_version("26.12.0", self._config())
        fetch.assert_not_called()
        dl.assert_not_called()
        run.assert_not_called()

    @mock.patch.object(mu, "run")
    @mock.patch.object(mu, "download_file")
    @mock.patch.object(mu, "fetch_text")
    @mock.patch.object(mu, "branch_exists", return_value=True)
    @mock.patch.object(mu, "ref_exists", return_value=False)
    def test_skips_when_branch_exists(self, ref, branch, fetch, dl, run):
        mu.mirror_version("26.12.0", self._config())
        fetch.assert_not_called()
        dl.assert_not_called()
        run.assert_not_called()

    @mock.patch.object(mu, "run")
    @mock.patch.object(mu, "sha256_file", return_value="deadbeef")
    @mock.patch.object(mu, "download_file")
    @mock.patch.object(mu, "fetch_text", return_value=FIXTURE_MANIFEST)
    @mock.patch.object(mu, "branch_exists", return_value=False)
    @mock.patch.object(mu, "ref_exists", return_value=False)
    def test_checksum_mismatch_aborts_before_git(self, ref, branch, fetch, dl, sha, run):
        with self.assertRaises(mu.MirrorError):
            mu.mirror_version("26.11.0", self._config())
        # It downloaded, but must never have touched git/gh.
        dl.assert_called()
        run.assert_not_called()

    @mock.patch.object(mu, "run")
    @mock.patch.object(mu, "download_file")
    @mock.patch.object(mu, "fetch_text", return_value=FIXTURE_MANIFEST)
    @mock.patch.object(mu, "branch_exists", return_value=False)
    @mock.patch.object(mu, "ref_exists", return_value=False)
    def test_happy_path_rewrites_and_orders_commands(self, ref, branch, fetch, dl, run):
        def fake_sha(path):
            # UI must be checked first: "PSPDFKit" is a substring of "PSPDFKitUI".
            return UI_SUM if "PSPDFKitUI" in str(path) else KIT_SUM

        with tempfile.TemporaryDirectory() as work:
            (Path(work) / "Package.swift").write_text((REPO_ROOT / "Package.swift").read_text())
            with pushd(work), mock.patch.object(mu, "sha256_file", side_effect=fake_sha):
                mu.mirror_version("26.12.0", self._config())
            rewritten = (Path(work) / "Package.swift").read_text()

        # Manifest was rewritten to point at the mirror's pre-26.12.0 assets.
        self.assertIn(
            "https://github.com/mirror/repo/releases/download/pre-26.12.0/"
            "Nutrient-iOS-SDK-PSPDFKit.xcframework-26.12.0.zip",
            rewritten,
        )
        self.assertEqual(rewritten.count("releases/download/pre-26.12.0/"), 2)
        self.assertNotIn("pre-26.11.0", rewritten)

        # Commands ran in the right order: branch -> commit -> push -> release -> PR.
        commands = [" ".join(call.args[0]) for call in run.call_args_list]
        self.assertTrue(commands[0].startswith("git checkout -B feature/26.12.0"))
        pushed = next(i for i, c in enumerate(commands) if c.startswith("git push"))
        released = next(i for i, c in enumerate(commands) if c.startswith("gh release create pre-26.12.0"))
        pr = next(i for i, c in enumerate(commands) if c.startswith("gh pr create"))
        self.assertLess(pushed, released)
        self.assertLess(released, pr)


class MainTests(unittest.TestCase):
    @mock.patch.object(mu, "mirror_version")
    @mock.patch.object(mu, "upstream_versions", return_value=["26.10.0", "26.11.0"])
    @mock.patch.object(mu, "build_config", return_value=mu.Config("up", "mir", force_version="26.99.0"))
    def test_force_version_mirrors_only_that(self, cfg, ups, mv):
        self.assertEqual(mu.main([]), 0)
        mv.assert_called_once_with("26.99.0", cfg.return_value)

    @mock.patch.object(mu, "mirror_version")
    @mock.patch.object(mu, "mirror_versions", return_value=[])
    @mock.patch.object(mu, "upstream_versions", return_value=["26.11.0"])
    @mock.patch.object(mu, "build_config", return_value=mu.Config("up", "mir"))
    def test_empty_mirror_versions_refuses(self, cfg, ups, mirrors, mv):
        self.assertEqual(mu.main([]), 1)
        mv.assert_not_called()

    @mock.patch.object(mu, "mirror_version")
    @mock.patch.object(mu, "mirror_versions", return_value=["26.9.0", "26.10.0"])
    @mock.patch.object(mu, "upstream_versions", return_value=["26.12.0", "26.10.0", "26.11.0"])
    @mock.patch.object(mu, "build_config", return_value=mu.Config("up", "mir"))
    def test_processes_candidates_oldest_first(self, cfg, ups, mirrors, mv):
        self.assertEqual(mu.main([]), 0)
        called = [call.args[0] for call in mv.call_args_list]
        self.assertEqual(called, ["26.11.0", "26.12.0"])

    @mock.patch.object(mu, "mirror_version")
    @mock.patch.object(mu, "mirror_versions", return_value=["26.11.0"])
    @mock.patch.object(mu, "upstream_versions", return_value=["26.11.0"])
    @mock.patch.object(mu, "build_config", return_value=mu.Config("up", "mir"))
    def test_nothing_to_do_returns_0(self, cfg, ups, mirrors, mv):
        self.assertEqual(mu.main([]), 0)
        mv.assert_not_called()

    @mock.patch.object(mu, "dry_run_plan")
    @mock.patch.object(mu, "mirror_version")
    @mock.patch.object(mu, "mirror_versions", return_value=["26.10.0"])
    @mock.patch.object(mu, "upstream_versions", return_value=["26.11.0"])
    @mock.patch.object(mu, "build_config", return_value=mu.Config("up", "mir", dry_run=True))
    def test_dry_run_makes_no_changes(self, cfg, ups, mirrors, mv, plan):
        self.assertEqual(mu.main([]), 0)
        mv.assert_not_called()
        plan.assert_called_once()


@unittest.skipUnless(
    os.environ.get("RUN_LIVE_TESTS") == "1",
    "set RUN_LIVE_TESTS=1 to run tests that hit the live GitHub API",
)
class LiveIntegrationTests(unittest.TestCase):
    UPSTREAM = "PSPDFKit/PSPDFKit-SP"
    MIRROR = "MFB-Technologies-Inc/PSPDFKit-SP-Mirror"

    def test_upstream_versions_live(self):
        config = mu.Config(upstream_repo=self.UPSTREAM, mirror_repo=self.MIRROR)
        uv = mu.upstream_versions(config)
        self.assertGreater(len(uv), 5)
        self.assertIn("26.11.0", uv)
        for v in uv:
            self.assertRegex(v, r"^\d+\.\d+\.\d+$")

    def test_candidates_are_newer_upstream_versions(self):
        # Upstream ships independently of the mirror, so candidates may or may
        # not be empty; assert only that discovery is self-consistent.
        config = mu.Config(upstream_repo=self.UPSTREAM, mirror_repo=self.MIRROR)
        uv = mu.upstream_versions(config)
        mirror_max = mu.mirror_versions(config)[-1]
        self.assertRegex(mirror_max, r"^\d+\.\d+\.\d+$")
        for v in mu.compute_candidates(mirror_max, uv):
            self.assertIn(v, uv)
            self.assertTrue(mu.version_gt(v, mirror_max))

    def test_live_manifest_parse(self):
        text = mu.fetch_text(
            f"https://raw.githubusercontent.com/{self.UPSTREAM}/26.11.0/Package.swift"
        )
        targets = mu.manifest_targets_by_name(text)
        self.assertEqual(targets["PSPDFKit"].checksum, KIT_SUM)


if __name__ == "__main__":
    unittest.main(verbosity=2)
