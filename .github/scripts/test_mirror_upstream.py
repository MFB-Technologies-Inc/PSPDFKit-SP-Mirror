#!/usr/bin/env python3
"""Tests for mirror_upstream.py.

Importing mirror_upstream defines its functions without running anything (the
module guards its entrypoint), so we exercise them in isolation.

Unlike a shell script sourced for its pure functions, the Python version lets us
also test the *side-effecting* flow: the external process/network seams
(`run`, `capture`, `download_file`, `fetch_text`, `find_pr`, ...) are module-level
functions, so we patch them and assert on control flow (resuming from each partial state, the
checksum-mismatch abort, stacking, and command ordering) without touching git, gh, the
network, or the ~200 MB downloads.

Usage:
  python3 .github/scripts/test_mirror_upstream.py            # pure + mocked
  RUN_LIVE_TESTS=1 python3 .github/scripts/test_mirror_upstream.py   # + live GitHub
"""

import contextlib
import hashlib
import json
import os
import subprocess
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

# Upstream after a toolchain bump: newer swift-tools-version and iOS minimum.
UPSTREAM_MANIFEST = """// swift-tools-version: 6.4
import PackageDescription
let package = Package(
    name: "Nutrient",
    platforms: [
        .iOS(.v18),
        .macCatalyst(.v18),
        .visionOS(.v2)
    ],
    targets: [
        .binaryTarget(
            name: "PSPDFKit",
            url: "https://my.nutrient.io/pspdfkit-xcframework-26.12.0.zip",
            checksum: "%s"),
        .binaryTarget(
            name: "PSPDFKitUI",
            url: "https://my.nutrient.io/pspdfkitui-xcframework-26.12.0.zip",
            checksum: "%s"),
    ]
)
""" % (KIT_SUM, UI_SUM)


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


class SyncToolchainTests(unittest.TestCase):
    def test_copies_tools_version_and_platforms(self):
        ours = (REPO_ROOT / "Package.swift").read_text()
        out = mu.sync_toolchain(ours, UPSTREAM_MANIFEST)
        self.assertTrue(out.startswith("// swift-tools-version: 6.4\n"))
        self.assertIn(".iOS(.v18)", out)
        self.assertNotIn(".iOS(.v17)", out)
        # Everything else is ours, untouched.
        self.assertIn('name: "PSPDFKit-SP-Mirror"', out)
        self.assertIn("pre-26.11.0", out)

    def test_fails_when_upstream_lacks_platforms(self):
        ours = (REPO_ROOT / "Package.swift").read_text()
        with self.assertRaises(mu.MirrorError):
            mu.sync_toolchain(ours, FIXTURE_MANIFEST)


class GitHubStateTests(unittest.TestCase):
    def test_branch_exists_fails_closed(self):
        for code, expected in ((0, True), (2, False)):
            with mock.patch.object(mu.subprocess, "run", return_value=mock.Mock(returncode=code)):
                self.assertIs(mu.branch_exists("feature/1.0.0"), expected)
        with mock.patch.object(mu.subprocess, "run", return_value=mock.Mock(returncode=128)):
            with self.assertRaises(mu.MirrorError):
                mu.branch_exists("feature/1.0.0")

    def test_find_pr_ignores_fork_prs(self):
        prs = [
            {"number": 41, "state": "CLOSED", "isCrossRepository": True},
            {"number": 30, "state": "OPEN", "isCrossRepository": False},
        ]
        with mock.patch.object(mu, "capture", return_value=json.dumps(prs)):
            self.assertEqual(mu.find_pr("mirror/repo", "feature/26.12.0")["number"], 30)
        with mock.patch.object(mu, "capture", return_value=json.dumps(prs[:1])):
            self.assertIsNone(mu.find_pr("mirror/repo", "feature/26.12.0"))

    def test_release_complete(self):
        both = [mu.kit_asset_name("1.0.0"), mu.ui_asset_name("1.0.0")]
        self.assertTrue(mu.release_complete({"id": 1, "draft": False, "assets": both}, "1.0.0"))
        self.assertFalse(mu.release_complete({"id": 1, "draft": True, "assets": both}, "1.0.0"))
        self.assertFalse(mu.release_complete({"id": 1, "draft": False, "assets": both[:1]}, "1.0.0"))
        self.assertFalse(mu.release_complete(None, "1.0.0"))


def _fake_sha(path):
    # UI must be checked first: "PSPDFKit" is a substring of "PSPDFKitUI".
    return UI_SUM if "PSPDFKitUI" in str(path) else KIT_SUM


COMPLETE_RELEASE = {
    "id": 7,
    "draft": False,
    "assets": [mu.kit_asset_name("26.12.0"), mu.ui_asset_name("26.12.0")],
}


class MirrorVersionFlowTests(unittest.TestCase):
    """The side-effecting flow, with git, gh, and the network patched out.

    `state` describes what an earlier run left behind; each test asserts the
    commands this run issues to finish the job from there.
    """

    def setUp(self):
        self.config = mu.Config(upstream_repo="up/stream", mirror_repo="mirror/repo")
        self.work = tempfile.TemporaryDirectory()
        (Path(self.work.name) / "Package.swift").write_text(
            (REPO_ROOT / "Package.swift").read_text()
        )
        self.addCleanup(self.work.cleanup)

    def _run(self, version="26.12.0", base="origin/main", pr=None, release=None,
             branch=False, branch_manifest="", sha=_fake_sha, force_version=""):
        self.config.force_version = force_version
        patches = {
            "find_pr": mock.patch.object(mu, "find_pr", return_value=pr),
            "find_release": mock.patch.object(mu, "find_release", return_value=release),
            "branch_exists": mock.patch.object(mu, "branch_exists", return_value=branch),
            "capture": mock.patch.object(mu, "capture", return_value=branch_manifest),
            "fetch_text": mock.patch.object(mu, "fetch_text", return_value=UPSTREAM_MANIFEST),
            "download_file": mock.patch.object(mu, "download_file"),
            "sha256_file": mock.patch.object(mu, "sha256_file", side_effect=sha),
            "run": mock.patch.object(mu, "run"),
        }
        with contextlib.ExitStack() as stack, pushd(self.work.name):
            self.mocks = {name: stack.enter_context(p) for name, p in patches.items()}
            result = mu.mirror_version(version, self.config, base)
        self.commands = [" ".join(c.args[0]) for c in self.mocks["run"].call_args_list]
        self.manifest = (Path(self.work.name) / "Package.swift").read_text()
        return result

    def _index(self, prefix):
        return next(i for i, c in enumerate(self.commands) if c.startswith(prefix))

    def test_done_when_pr_open_and_stacks_next_on_it(self):
        result = self._run(pr={"number": 30, "state": "OPEN"})
        self.assertEqual(result, "origin/feature/26.12.0")
        self.mocks["fetch_text"].assert_not_called()
        self.assertEqual(self.commands, [])

    def test_done_when_pr_merged_and_next_keeps_base(self):
        self.assertIsNone(self._run(pr={"number": 30, "state": "MERGED"}))
        self.assertEqual(self.commands, [])

    def test_closed_pr_is_done_without_force_version(self):
        self.assertIsNone(self._run(pr={"number": 30, "state": "CLOSED"}))
        self.assertEqual(self.commands, [])

    def test_force_version_mirrors_again_after_closed_pr(self):
        result = self._run(pr={"number": 30, "state": "CLOSED"}, force_version="26.12.0")
        self.assertEqual(result, "origin/feature/26.12.0")
        self.assertTrue(self.commands[-1].startswith("gh pr create"))

    def test_fresh_version_runs_every_step_in_order(self):
        result = self._run()
        self.assertEqual(result, "origin/feature/26.12.0")

        self.assertIn(
            "https://github.com/mirror/repo/releases/download/pre-26.12.0/"
            "Nutrient-iOS-SDK-PSPDFKit.xcframework-26.12.0.zip",
            self.manifest,
        )
        self.assertEqual(self.manifest.count("releases/download/pre-26.12.0/"), 2)
        self.assertNotIn("pre-26.11.0", self.manifest)
        self.assertTrue(self.manifest.startswith("// swift-tools-version: 6.4\n"))
        self.assertIn(".iOS(.v18)", self.manifest)

        self.assertEqual(self.mocks["download_file"].call_count, 2)
        self.assertLess(self._index("git fetch origin main"),
                        self._index("git checkout -B feature/26.12.0 origin/main"))
        self.assertLess(self._index("git push origin feature/26.12.0"),
                        self._index("gh release create pre-26.12.0"))
        self.assertLess(self._index("gh release create pre-26.12.0"),
                        self._index("gh pr create"))

    def test_checksum_mismatch_aborts_before_git(self):
        with self.assertRaises(mu.MirrorError):
            self._run(sha=lambda path: "deadbeef")
        self.mocks["download_file"].assert_called()
        self.mocks["run"].assert_not_called()

    def test_resumes_after_branch_pushed_but_release_failed(self):
        self._run(branch=True, branch_manifest="url: .../releases/download/pre-26.12.0/x.zip")
        self.assertFalse(any(c.startswith("git checkout") for c in self.commands))
        self.assertFalse(any(c.startswith("git commit") for c in self.commands))
        self.assertLess(self._index("gh release create pre-26.12.0"), self._index("gh pr create"))

    def test_resumes_after_release_created_but_pr_failed(self):
        self._run(branch=True, release=COMPLETE_RELEASE,
                  branch_manifest="url: .../releases/download/pre-26.12.0/x.zip")
        self.mocks["download_file"].assert_not_called()
        self.assertFalse(any(c.startswith("gh release") for c in self.commands))
        self.assertTrue(self.commands[-1].startswith("gh pr create"))

    def test_replaces_draft_left_by_failed_upload(self):
        draft = dict(COMPLETE_RELEASE, draft=True)
        self._run(branch=True, release=draft,
                  branch_manifest="url: .../releases/download/pre-26.12.0/x.zip")
        self.assertLess(self._index("gh api -X DELETE repos/mirror/repo/releases/7"),
                        self._index("gh release create pre-26.12.0"))

    def test_refuses_branch_pointing_elsewhere(self):
        with self.assertRaises(mu.MirrorError):
            self._run(branch=True, branch_manifest="url: .../releases/download/pre-26.11.0/x.zip")
        commands = [c.args[0] for c in self.mocks["run"].call_args_list]
        self.assertFalse(any(c[0] == "gh" for c in commands))

    def test_stacks_on_given_base(self):
        self._run(version="27.0.0", base="origin/feature/26.12.0")
        self.assertIn("git fetch origin feature/26.12.0", self.commands)
        self.assertIn("git checkout -B feature/27.0.0 origin/feature/26.12.0", self.commands)
        pr_args = self.mocks["run"].call_args_list[self._index("gh pr create")].args[0]
        self.assertEqual(pr_args[pr_args.index("--base") + 1], "main")
        self.assertIn("stacked on `feature/26.12.0`", pr_args[pr_args.index("--body") + 1])


class MainTests(unittest.TestCase):
    @mock.patch.object(mu, "mirror_version")
    @mock.patch.object(mu, "mirror_versions", return_value=["26.9.0"])
    @mock.patch.object(mu, "upstream_versions", return_value=["26.10.0", "26.11.0"])
    @mock.patch.object(mu, "build_config", return_value=mu.Config("up", "mir", force_version="26.11.0"))
    def test_force_version_mirrors_only_that(self, cfg, ups, mirrors, mv):
        self.assertEqual(mu.main([]), 0)
        mv.assert_called_once_with("26.11.0", cfg.return_value, "origin/main")

    @mock.patch.object(mu, "mirror_version")
    @mock.patch.object(mu, "mirror_versions", return_value=["26.10.0", "26.11.0"])
    @mock.patch.object(mu, "upstream_versions", return_value=["26.10.0", "26.11.0"])
    def test_force_version_refuses_downgrade(self, ups, mirrors, mv):
        for old in ("26.10.0", "26.11.0"):
            with mock.patch.object(mu, "build_config", return_value=mu.Config("up", "mir", force_version=old)):
                self.assertEqual(mu.main([]), 1, old)
        mv.assert_not_called()

    @mock.patch.object(mu, "mirror_version")
    @mock.patch.object(mu, "mirror_versions", return_value=[])
    @mock.patch.object(mu, "upstream_versions", return_value=["26.10.0"])
    @mock.patch.object(mu, "build_config", return_value=mu.Config("up", "mir", force_version="26.10.0"))
    def test_force_version_seeds_first_release(self, cfg, ups, mirrors, mv):
        self.assertEqual(mu.main([]), 0)
        mv.assert_called_once_with("26.10.0", cfg.return_value, "origin/main")

    @mock.patch.object(mu, "mirror_version")
    @mock.patch.object(mu, "upstream_versions", return_value=["26.10.0", "26.11.0"])
    def test_force_version_must_be_an_upstream_tag(self, ups, mv):
        for bad in ("26.99.0", "26.11", "feature/x"):
            with mock.patch.object(mu, "build_config", return_value=mu.Config("up", "mir", force_version=bad)):
                self.assertEqual(mu.main([]), 1, bad)
        mv.assert_not_called()

    def test_build_config_strips_force_version(self):
        env = {"MIRROR_REPO": "m/r", "FORCE_VERSION": " 26.11.0\n"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(mu.build_config().force_version, "26.11.0")

    @mock.patch.object(mu, "mirror_version")
    @mock.patch.object(mu, "mirror_versions", return_value=[])
    @mock.patch.object(mu, "upstream_versions", return_value=["26.11.0"])
    @mock.patch.object(mu, "build_config", return_value=mu.Config("up", "mir"))
    def test_empty_mirror_versions_refuses(self, cfg, ups, mirrors, mv):
        self.assertEqual(mu.main([]), 1)
        mv.assert_not_called()

    @mock.patch.object(mu, "mirror_versions", return_value=["26.9.0", "26.10.0"])
    @mock.patch.object(mu, "upstream_versions", return_value=["26.12.0", "26.10.0", "26.11.0", "27.0.0"])
    @mock.patch.object(mu, "build_config", return_value=mu.Config("up", "mir"))
    def test_stacks_oldest_first_skipping_merged(self, cfg, ups, mirrors):
        # 26.11.0's PR is already merged (None); the rest stack on each other.
        results = {"26.11.0": None, "26.12.0": "origin/feature/26.12.0", "27.0.0": "origin/feature/27.0.0"}
        with mock.patch.object(mu, "mirror_version", side_effect=lambda v, c, b: results[v]) as mv:
            self.assertEqual(mu.main([]), 0)
        calls = [(c.args[0], c.args[2]) for c in mv.call_args_list]
        self.assertEqual(calls, [
            ("26.11.0", "origin/main"),
            ("26.12.0", "origin/main"),
            ("27.0.0", "origin/feature/26.12.0"),
        ])

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

    @mock.patch.object(mu, "publish_releases", return_value=0)
    @mock.patch.object(mu, "upstream_versions")
    @mock.patch.object(mu, "build_config", return_value=mu.Config("up", "mir"))
    def test_publish_subcommand(self, cfg, ups, publish):
        self.assertEqual(mu.main(["publish"]), 0)
        publish.assert_called_once_with(cfg.return_value)
        ups.assert_not_called()


class PublishReleasesTests(unittest.TestCase):
    TAGS = ["26.10.0", "26.11.0", "pre-26.10.0", "pre-26.11.0", "pre-26.12.0", "pre-27.0.0"]

    def _publish(self, tags, reached, latest):
        with mock.patch.object(mu, "list_repo_tags", return_value=tags), \
             mock.patch.object(mu, "manifest_commit", side_effect=lambda v: reached.get(v)), \
             mock.patch.object(mu, "latest_release", return_value=latest), \
             mock.patch.object(mu, "run") as run:
            self.assertEqual(mu.publish_releases(mu.Config("up", "mir")), 0)
        return [c.args[0] for c in run.call_args_list]

    def test_publishes_every_pending_version_and_marks_newest_latest(self):
        # A stacked 27.0.0 PR merged first brings 26.12.0 along with it.
        calls = self._publish(self.TAGS, {"26.12.0": "aaa", "27.0.0": "bbb"}, "26.11.0")
        self.assertEqual([(c[3], c[c.index("--target") + 1], c[-2]) for c in calls], [
            ("26.12.0", "aaa", "--latest=false"),
            ("27.0.0", "bbb", "--latest=true"),
        ])

    def test_skips_versions_not_on_main(self):
        calls = self._publish(self.TAGS, {"26.12.0": "aaa"}, "26.11.0")
        self.assertEqual([c[3] for c in calls], ["26.12.0"])
        self.assertIn("--latest=true", calls[0])

    def test_backport_never_becomes_latest(self):
        tags = ["27.0.0", "pre-27.0.0", "pre-26.11.1"]
        calls = self._publish(tags, {"26.11.1": "ccc"}, "27.0.0")
        self.assertIn("--latest=false", calls[0])

    def test_nothing_pending(self):
        self.assertEqual(self._publish(["26.11.0", "pre-26.11.0"], {}, "26.11.0"), [])


class ManifestCommitTests(unittest.TestCase):
    """Runs real git: stacked branches merged into main out of order."""

    def _git(self, *args):
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            check=True, text=True, stdout=subprocess.PIPE,
        ).stdout.strip()

    def _commit(self, version):
        Path("Package.swift").write_text(f'url: "https://x/releases/download/pre-{version}/a.zip"\n')
        self._git("add", "Package.swift")
        self._git("commit", "-qm", version)
        return self._git("rev-parse", "HEAD")

    def test_finds_each_stacked_version(self):
        with tempfile.TemporaryDirectory() as work, pushd(work):
            self._git("init", "-qb", "main")
            self._commit("1.0.0")
            self._git("checkout", "-qb", "feature/1.1.0")
            sha_110 = self._commit("1.1.0")
            self._git("checkout", "-qb", "feature/1.2.0")
            self._commit("1.2.0")
            self._git("checkout", "-q", "main")
            Path("README").write_text("unrelated main work\n")
            self._git("add", "README")
            self._git("commit", "-qm", "docs")
            # Merge the newer stacked PR first.
            self._git("merge", "-q", "--no-ff", "-m", "merge 1.2.0", "feature/1.2.0")
            head = self._git("rev-parse", "HEAD")

            self.assertEqual(mu.manifest_commit("1.2.0"), head)
            self.assertEqual(mu.manifest_commit("1.1.0"), sha_110)
            self.assertIsNone(mu.manifest_commit("9.9.9"))

    def test_finds_version_behind_a_main_into_feature_merge(self):
        # The shape of 60921b4: main is merged into a feature branch that was cut
        # before the previous version landed, keeping the feature's manifest.
        with tempfile.TemporaryDirectory() as work, pushd(work):
            self._git("init", "-qb", "main")
            self._commit("1.0.0")
            self._git("checkout", "-qb", "feature/1.2.0")
            self._commit("1.2.0")
            self._git("checkout", "-q", "main")
            self._git("checkout", "-qb", "feature/1.1.0")
            self._commit("1.1.0")
            self._git("checkout", "-q", "main")
            self._git("merge", "-q", "--no-ff", "-m", "merge 1.1.0", "feature/1.1.0")
            merged_110 = self._git("rev-parse", "HEAD")
            self._git("checkout", "-q", "feature/1.2.0")
            self._git("merge", "-q", "-s", "ours", "-m", "merge main", "main")
            self._git("checkout", "-q", "main")
            self._git("merge", "-q", "--no-ff", "-m", "merge 1.2.0", "feature/1.2.0")

            # The newest commit pointing at pre-1.1.0 is main's merge of it.
            self.assertEqual(mu.manifest_commit("1.1.0"), merged_110)


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
