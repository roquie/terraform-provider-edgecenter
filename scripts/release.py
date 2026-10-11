import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_COMMIT = "87f07794807ccde5ef484232f1fc1060c9935edc"
RELEASE_BRANCH = "ci/patched-provider-releases"
GO_VERSION = "go1.26.8"
PROVIDER = "terraform-provider-edgecenter"
PLATFORMS = (
    "darwin_amd64", "darwin_arm64", "linux_amd64", "linux_arm64",
    "windows_amd64", "windows_arm64",
)
VERSION_PATTERN = r"0\.16\.99-patch4\.[0-9]{8}\.[1-9][0-9]*"
HELPERS = ("install-provider.sh", "install-provider.ps1")


def run(*args, **kwargs):
    return subprocess.check_output(args, cwd=ROOT, text=True, **kwargs).strip()


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_version(version):
    if not re.fullmatch(VERSION_PATTERN, version):
        raise ValueError("Use a version in the form 0.16.99-patch4.YYYYMMDD.N.")
    if version == "0.16.99-patch4.20261011.1":
        raise ValueError("This version belongs to an existing local build. Use a new version.")


def check_source(version, tag=False):
    check_version(version)
    commit = run("git", "rev-parse", "HEAD")
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", REQUIRED_COMMIT, commit],
        cwd=ROOT, check=True,
    )
    if run("git", "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("The tracked source files must be clean.")
    if tag:
        expected_ref = f"refs/tags/v{version}"
        if os.environ.get("GITHUB_REF") != expected_ref:
            raise ValueError("The release ref does not match the version.")
        if os.environ.get("GITHUB_SHA") != commit:
            raise ValueError("The workflow commit does not match HEAD.")
        if run("git", "rev-parse", f"{expected_ref}^{{commit}}") != commit:
            raise ValueError("The tag does not match the workflow commit.")
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", commit,
             f"refs/remotes/origin/{RELEASE_BRANCH}"],
            cwd=ROOT, check=True,
        )
    return commit


def archive_name(version, platform):
    return f"{PROVIDER}_{version}_{platform}.zip"


def binary_name(version, platform):
    suffix = ".exe" if platform.startswith("windows_") else ""
    return f"{PROVIDER}_v{version}_x5{suffix}"


def write_zip(path, binary, name):
    entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    entry.create_system = 3
    entry.external_attr = 0o100755 << 16
    entry.compress_type = zipfile.ZIP_STORED
    with zipfile.ZipFile(path, "x") as archive:
        archive.writestr(entry, binary.read_bytes())


def check_platform(data, platform):
    operating_system, architecture = platform.split("_")
    if operating_system == "linux":
        machine = int.from_bytes(data[18:20], "little")
        expected = 62 if architecture == "amd64" else 183
        valid = data[:6] == b"\x7fELF\x02\x01" and machine == expected
    elif operating_system == "darwin":
        machine = int.from_bytes(data[4:8], "little")
        expected = 0x1000007 if architecture == "amd64" else 0x100000C
        valid = data[:4] == b"\xcf\xfa\xed\xfe" and machine == expected
    else:
        offset = int.from_bytes(data[60:64], "little")
        machine = int.from_bytes(data[offset + 4:offset + 6], "little")
        expected = 0x8664 if architecture == "amd64" else 0xAA64
        valid = data[:2] == b"MZ" and data[offset:offset + 4] == b"PE\0\0" and machine == expected
    if not valid:
        raise ValueError(f"The binary does not match {platform}.")


def expected_files(version):
    return {
        *(archive_name(version, platform) for platform in PLATFORMS),
        *HELPERS, "provenance.json", "SHA256SUMS",
    }


def check_bundle(directory, version, commit):
    check_version(version)
    if {path.name for path in directory.iterdir()} != expected_files(version):
        raise ValueError("The release file list is incomplete or contains extra files.")
    checksums = {}
    for line in (directory / "SHA256SUMS").read_text().splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)", line)
        if not match or match[2] in checksums:
            raise ValueError("The checksum file is invalid.")
        checksums[match[2]] = match[1]
    if set(checksums) != expected_files(version) - {"SHA256SUMS"}:
        raise ValueError("The checksum file does not cover all release assets.")
    for name, checksum in checksums.items():
        if digest(directory / name) != checksum:
            raise ValueError(f"The checksum does not match {name}.")
    provenance = json.loads((directory / "provenance.json").read_text())
    if (provenance["version"] != version or provenance["source_commit"] != commit
            or provenance["required_commit"] != REQUIRED_COMMIT
            or provenance["go_version"] != GO_VERSION
            or provenance["go_mod_sha256"] != digest(ROOT / "go.mod")
            or provenance["go_sum_sha256"] != digest(ROOT / "go.sum")
            or set(provenance["platforms"]) != set(PLATFORMS)):
        raise ValueError("The release provenance does not match the source.")
    for helper in HELPERS:
        if digest(directory / helper) != digest(ROOT / "scripts" / helper):
            raise ValueError("The installation helper does not match the source.")
    for platform in PLATFORMS:
        with zipfile.ZipFile(directory / archive_name(version, platform)) as archive:
            if archive.namelist() != [binary_name(version, platform)]:
                raise ValueError("The archive does not contain exactly one provider binary.")
            info = archive.infolist()[0]
            if (info.date_time != (1980, 1, 1, 0, 0, 0)
                    or info.external_attr != 0o100755 << 16
                    or info.compress_type != zipfile.ZIP_STORED):
                raise ValueError("The archive metadata is not deterministic.")
            data = archive.read(info)
            check_platform(data, platform)
            if hashlib.sha256(data).hexdigest() != provenance["platforms"][platform]["binary_sha256"]:
                raise ValueError("The binary checksum does not match the provenance.")
    print(f"Checked {len(PLATFORMS)} platform archives and all release checksums.")


def build(directory, version):
    commit = check_source(version)
    directory.mkdir(parents=True, exist_ok=False)
    env = os.environ | {
        "GOENV": "off", "GOTOOLCHAIN": GO_VERSION, "GOWORK": "off",
        "GOPROXY": "https://proxy.golang.org,direct", "GOSUMDB": "sum.golang.org",
        "CGO_ENABLED": "0", "GOAMD64": "v1", "GOARM64": "v8.0",
        "GOFLAGS": "", "GOEXPERIMENT": "", "GOTELEMETRY": "off",
    }
    if run("go", "env", "GOVERSION", env=env) != GO_VERSION:
        raise ValueError("The Go toolchain does not match the release toolchain.")
    subprocess.run(["go", "mod", "verify"], cwd=ROOT, env=env, check=True)
    ldflags = f"-s -w -buildid= -X main.version={version}"
    provenance = {
        "version": version, "source_commit": commit, "required_commit": REQUIRED_COMMIT,
        "go_version": GO_VERSION, "provider_source": "registry.terraform.io/edge-center/edgecenter",
        "protocol_version": 5, "cgo_enabled": False,
        "go_mod_sha256": digest(ROOT / "go.mod"), "go_sum_sha256": digest(ROOT / "go.sum"),
        "build_flags": ["-mod=readonly", "-trimpath", "-buildvcs=false", "-ldflags", ldflags],
        "archive_format": "zip-stored", "archive_timestamp": "1980-01-01T00:00:00",
        "platforms": {},
    }
    with tempfile.TemporaryDirectory(prefix="provider-build-") as temporary:
        for platform in PLATFORMS:
            operating_system, architecture = platform.split("_")
            target_env = env | {"GOOS": operating_system, "GOARCH": architecture}
            binary = Path(temporary) / binary_name(version, platform)
            subprocess.run(
                ["go", "build", "-mod=readonly", "-trimpath", "-buildvcs=false",
                 "-ldflags", ldflags, "-o", str(binary), "."],
                cwd=ROOT, env=target_env, check=True,
            )
            build_info = run("go", "version", "-m", str(binary), env=env)
            if GO_VERSION not in build_info or f"-X main.version={version}" not in build_info:
                raise ValueError("The binary does not contain the release build metadata.")
            check_platform(binary.read_bytes(), platform)
            name = archive_name(version, platform)
            write_zip(directory / name, binary, binary.name)
            provenance["platforms"][platform] = {
                "archive": name, "binary": binary.name, "binary_sha256": digest(binary),
            }
            binary.unlink()
            print(f"Built {platform}.", flush=True)
    for helper in HELPERS:
        shutil.copyfile(ROOT / "scripts" / helper, directory / helper)
    (directory / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
    lines = [f"{digest(path)}  {path.name}\n" for path in sorted(directory.iterdir())]
    (directory / "SHA256SUMS").write_text("".join(lines))
    check_bundle(directory, version, commit)


def publish(directory, version):
    commit = check_source(version, tag=True)
    check_bundle(directory, version, commit)
    repository = os.environ["GITHUB_REPOSITORY"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("The repository address is invalid.")
    tag = f"v{version}"
    releases = json.loads(run("gh", "api", "--paginate", "--slurp", f"repos/{repository}/releases?per_page=100"))
    existing = next((release for page in releases for release in page if release["tag_name"] == tag), None)
    if existing:
        if not existing["draft"]:
            raise ValueError("This version is already published. Use a new version.")
        with tempfile.TemporaryDirectory(prefix="provider-release-check-") as temporary:
            remote_names = {asset["name"] for asset in existing["assets"]}
            if remote_names - expected_files(version):
                raise ValueError("The draft contains unexpected assets.")
            if remote_names:
                subprocess.run(
                    ["gh", "release", "download", tag, "--repo", repository, "--dir", temporary],
                    cwd=ROOT, check=True,
                )
            for name in remote_names:
                if digest(Path(temporary) / name) != digest(directory / name):
                    raise ValueError(f"The draft contains different bytes for {name}. Use a new version.")
    else:
        notes = (
            f"Patched provider {version}.\n\n"
            f"Source commit: `{commit}`.\n"
            f"Required fixes commit: `{REQUIRED_COMMIT}`.\n"
            f"Toolchain: `{GO_VERSION}`. CGO is disabled.\n\n"
            "This release includes immutable selectors, locked-409 retries, load balancer read stability, "
            "immutable-409 retries, and the upstream debug address.\n\n"
            "Download the installation helper and read the README at this tag. "
            "The helper installs a Terraform filesystem mirror. "
            "GitHub Releases is not a Terraform registry.\n\n"
            "SHA256SUMS covers the archives, helpers, and provenance.json. "
            "Checksums detect changed bytes. They do not authenticate the publisher.\n"
        )
        subprocess.run(
            ["gh", "release", "create", tag, "--repo", repository, "--verify-tag", "--draft",
             "--prerelease", "--title", tag, "--notes", notes],
            cwd=ROOT, check=True,
        )
        remote_names = set()
    for path in sorted(directory.iterdir()):
        if path.name not in remote_names:
            subprocess.run(
                ["gh", "release", "upload", tag, str(path), "--repo", repository],
                cwd=ROOT, check=True,
            )
    with tempfile.TemporaryDirectory(prefix="provider-release-final-") as temporary:
        subprocess.run(
            ["gh", "release", "download", tag, "--repo", repository, "--dir", temporary],
            cwd=ROOT, check=True,
        )
        downloaded = Path(temporary)
        check_bundle(downloaded, version, commit)
        for path in directory.iterdir():
            if digest(path) != digest(downloaded / path.name):
                raise ValueError("The uploaded asset differs from the build artifact.")
    subprocess.run(
        ["gh", "release", "edit", tag, "--repo", repository, "--draft=false", "--latest=false"],
        cwd=ROOT, check=True,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("build", "check", "check-tag", "publish"))
    parser.add_argument("--version", required=True)
    parser.add_argument("--directory", type=Path, default=ROOT / "dist")
    arguments = parser.parse_args()
    directory = arguments.directory.resolve()
    if arguments.command == "build":
        build(directory, arguments.version)
    elif arguments.command == "check-tag":
        print(check_source(arguments.version, tag=True))
    elif arguments.command == "check":
        check_bundle(directory, arguments.version, check_source(arguments.version))
    else:
        publish(directory, arguments.version)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, subprocess.CalledProcessError, OSError) as error:
        sys.exit(str(error))
