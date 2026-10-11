import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile


sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("release", SCRIPTS / "release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
VERSION = "0.16.99-patch4.20261011.2"


class ReleaseTests(unittest.TestCase):
    def test_versions(self):
        release.check_version(VERSION)
        for invalid in ("main", "v" + VERSION, "1.0.0", "0.16.99-patch4.20261011.1", "../bad"):
            with self.subTest(version=invalid), self.assertRaises(ValueError):
                release.check_version(invalid)

    def test_deterministic_archives(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "provider"
            binary.write_bytes(b"not executable")
            release.write_zip(root / "first.zip", binary, "provider")
            os.utime(binary, (2000000000, 2000000000))
            release.write_zip(root / "second.zip", binary, "provider")
            self.assertEqual((root / "first.zip").read_bytes(), (root / "second.zip").read_bytes())
            with zipfile.ZipFile(root / "first.zip") as archive:
                self.assertEqual(archive.namelist(), ["provider"])
                self.assertEqual(archive.infolist()[0].date_time, (1980, 1, 1, 0, 0, 0))

    def test_bundle_rejects_missing_assets(self):
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
            release.check_bundle(Path(temporary), VERSION, "a" * 40)

    def test_rejects_published_version(self):
        pages = json.dumps([[{"tag_name": "v" + VERSION, "draft": False}]])
        with mock.patch.object(release, "check_source", return_value="a" * 40), \
                mock.patch.object(release, "check_bundle"), \
                mock.patch.object(release, "run", return_value=pages), \
                mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "example/provider"}), \
                mock.patch.object(release.subprocess, "run") as command:
            with self.assertRaisesRegex(ValueError, "already published"):
                release.publish(Path("unused"), VERSION)
            command.assert_not_called()

    def test_binary_architecture_check(self):
        data = bytearray(64)
        data[:6] = b"\x7fELF\x02\x01"
        data[18:20] = (62).to_bytes(2, "little")
        release.check_platform(data, "linux_amd64")
        with self.assertRaises(ValueError):
            release.check_platform(data, "linux_arm64")


class InstallerChecks:
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="provider-installer-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fixture = self.root / "fixture"
        self.fixture.mkdir()
        self.mirror = self.root / "mirror with spaces"
        self.config = self.root / "test.tfrc"
        self.platform = "windows_arm64"
        self.archive = release.archive_name(VERSION, self.platform)
        with zipfile.ZipFile(self.fixture / self.archive, "w") as archive:
            archive.writestr(release.binary_name(VERSION, self.platform), b"not executable")
        self.write_checksums()

    def write_checksums(self):
        digest = hashlib.sha256((self.fixture / self.archive).read_bytes()).hexdigest()
        (self.fixture / "SHA256SUMS").write_text(f"{digest}  {self.archive}\n")

    def installed_path(self):
        return self.mirror / "registry.terraform.io" / "edge-center" / "edgecenter" / self.archive

    def test_install_and_repeat(self):
        self.invoke(expected=0)
        self.assertEqual(self.installed_path().read_bytes(), (self.fixture / self.archive).read_bytes())
        configuration = self.config.read_text()
        self.assertIn('include = ["registry.terraform.io/edge-center/edgecenter"]', configuration)
        self.assertIn('exclude = ["registry.terraform.io/edge-center/edgecenter"]', configuration)
        self.invoke(expected=0)
        self.assertEqual(configuration, self.config.read_text())

    def test_checksum_failure(self):
        (self.fixture / self.archive).write_bytes(b"changed")
        self.invoke(expected=1)
        self.assertFalse(self.installed_path().exists())
        self.assertFalse(self.config.exists())

    def test_duplicate_checksum(self):
        path = self.fixture / "SHA256SUMS"
        path.write_text(path.read_text() * 2)
        self.invoke(expected=1)
        self.assertFalse(self.installed_path().exists())

    def test_preserve_existing_config(self):
        self.config.write_text("existing configuration\n")
        self.invoke(expected=1)
        self.assertEqual(self.config.read_text(), "existing configuration\n")
        self.assertFalse(self.installed_path().exists())

    def test_preserve_existing_version(self):
        self.invoke(expected=0)
        self.installed_path().write_bytes(b"existing version")
        self.invoke(expected=1)
        self.assertEqual(self.installed_path().read_bytes(), b"existing version")

    def test_reject_archive_path(self):
        with zipfile.ZipFile(self.fixture / self.archive, "w") as archive:
            archive.writestr("../provider", b"not executable")
        self.write_checksums()
        self.invoke(expected=1)
        self.assertFalse(self.installed_path().exists())


@unittest.skipIf(os.name == "nt", "The Windows job checks the PowerShell helper.")
class ShellInstallerTests(InstallerChecks, unittest.TestCase):
    def invoke(self, expected):
        tools = self.root / "tools"
        tools.mkdir(exist_ok=True)
        curl = tools / "curl"
        curl.write_text(
            '#!/usr/bin/env bash\nset -euo pipefail\n'
            'while [[ $# -gt 0 ]]; do\n'
            '  case "$1" in\n'
            '    https://*) url=$1 ;;\n'
            '    -o) shift; output=$1 ;;\n'
            '  esac\n  shift\ndone\n'
            'cp "$INSTALL_FIXTURE/${url##*/}" "$output"\n'
        )
        curl.chmod(0o755)
        environment = os.environ | {"PATH": str(tools) + os.pathsep + os.environ["PATH"], "INSTALL_FIXTURE": str(self.fixture)}
        result = subprocess.run(
            ["bash", str(SCRIPTS / "install-provider.sh"), "example/provider", VERSION,
             str(self.mirror), str(self.config), self.platform],
            env=environment, text=True, capture_output=True, cwd=self.root,
        )
        self.assertEqual(result.returncode == 0, expected == 0, result.stdout + result.stderr)


@unittest.skipUnless(shutil.which("pwsh"), "PowerShell is not installed on this host.")
class PowerShellInstallerTests(InstallerChecks, unittest.TestCase):
    def invoke(self, expected):
        probe = self.root / "probe.ps1"
        probe.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            "function Invoke-WebRequest {\n"
            "  param($Uri, $OutFile, [switch]$UseBasicParsing)\n"
            "  Copy-Item -LiteralPath (Join-Path $env:INSTALL_FIXTURE ($Uri.Split('/')[-1])) -Destination $OutFile\n"
            "}\n"
            "& $env:INSTALL_SCRIPT -Repository 'example/provider' -Version $env:INSTALL_VERSION "
            "-Mirror $env:INSTALL_MIRROR -Config $env:INSTALL_CONFIG -Platform 'windows_arm64'\n"
        )
        environment = os.environ | {
            "INSTALL_FIXTURE": str(self.fixture), "INSTALL_SCRIPT": str(SCRIPTS / "install-provider.ps1"),
            "INSTALL_VERSION": VERSION, "INSTALL_MIRROR": str(self.mirror), "INSTALL_CONFIG": str(self.config),
        }
        result = subprocess.run(
            ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(probe)],
            env=environment, text=True, capture_output=True, cwd=self.root,
        )
        self.assertEqual(result.returncode == 0, expected == 0, result.stdout + result.stderr)


if __name__ == "__main__":
    if os.name == "nt" and not shutil.which("pwsh"):
        raise SystemExit("The Windows CI job requires PowerShell.")
    unittest.main(verbosity=2)
