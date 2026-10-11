# Patched Terraform EdgeCenter provider

This branch distributes a patched provider through GitHub Releases. Team members do not need Go or a local build.

The provider source remains `registry.terraform.io/edge-center/edgecenter`. GitHub Releases is not a Terraform registry or a network mirror. Terraform cannot discover these releases through `terraform init` alone.

## Included fixes

Every release must contain commit `87f07794807ccde5ef484232f1fc1060c9935edc` in its history.

- Region and project selectors use `ForceNew` where the API does not permit changes.
- Transient locked-409 responses use retries.
- Load balancer reads tolerate incomplete status responses.
- Immutable-409 responses use retries for supported operations.
- The debug provider address keeps the upstream default.

These builds are prereleases. Review the Terraform plan before each deployment.

## Install on Linux or macOS

Requirements: Bash, curl, unzip, and `sha256sum` or `shasum`. No cloud credentials are necessary for installation.

1. Set the repository address and the exact release version.

```bash
REPO=OWNER/REPOSITORY
VERSION=0.16.99-patch4.20261011.2
MIRROR="$PWD/.provider-mirror"
CONFIG="$PWD/edgecenter-generated.tfrc"
TOOLS=$(mktemp -d)
```

2. Download the helper from the trusted release.

```bash
curl --fail --location --proto '=https' --proto-redir '=https' \
  "https://github.com/$REPO/releases/download/v$VERSION/install-provider.sh" \
  -o "$TOOLS/install-provider.sh"
```

3. Review the helper before you run it.
4. Install the archive for the local operating system and CPU.

```bash
bash "$TOOLS/install-provider.sh" "$REPO" "$VERSION" "$MIRROR" "$CONFIG"
export TF_CLI_CONFIG_FILE="$CONFIG"
```

The helper downloads the archive and `SHA256SUMS` over HTTPS. It checks the SHA-256 checksum and the archive file list. It does not run the provider.

The helper installs the ZIP in the packed filesystem-mirror layout:

```text
MIRROR/registry.terraform.io/edge-center/edgecenter/
  terraform-provider-edgecenter_VERSION_OS_ARCH.zip
```

Each archive contains one binary. The binary name is `terraform-provider-edgecenter_vVERSION_x5`, with `.exe` on Windows. The `_x5` suffix identifies plugin protocol 5.

If the destination contains different bytes for the same version, the helper stops. It never replaces an existing version or a different CLI configuration.

## Install on Windows

Requirements: PowerShell 7 and Terraform. The helper supports AMD64 and ARM64.

1. Set the repository address and the exact release version.

```powershell
$Repository = 'OWNER/REPOSITORY'
$Version = '0.16.99-patch4.20261011.2'
$Mirror = Join-Path $PWD '.provider-mirror'
$Config = Join-Path $PWD 'edgecenter-generated.tfrc'
$Tools = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $Tools
$Helper = Join-Path $Tools 'install-provider.ps1'
Invoke-WebRequest -Uri "https://github.com/$Repository/releases/download/v$Version/install-provider.ps1" -OutFile $Helper
```

2. Review the helper before you run it.
3. Run the helper under the approved PowerShell execution policy.

```powershell
& $Helper -Repository $Repository -Version $Version -Mirror $Mirror -Config $Config
$env:TF_CLI_CONFIG_FILE = $Config
```

The helper does not change the execution policy. It stores the packed ZIP, not an extracted executable.

## Terraform configuration

The helper creates a dedicated CLI configuration:

```hcl
provider_installation {
  filesystem_mirror {
    path = "/absolute/path/to/.provider-mirror"
    include = ["registry.terraform.io/edge-center/edgecenter"]
  }
  direct {
    exclude = ["registry.terraform.io/edge-center/edgecenter"]
  }
}
```

Only this provider uses the filesystem mirror. Other providers retain direct registry installation.

`TF_CLI_CONFIG_FILE` selects this file instead of the global CLI configuration. The helper never edits `~/.terraformrc` or `%APPDATA%\terraform.rc`.

If your global configuration contains credentials, caches, or other mirrors, prepare a separate team configuration before initialization:

1. Copy the required blocks into the team configuration.
2. Add the EdgeCenter filesystem mirror to the existing `provider_installation` block.
3. Exclude only `registry.terraform.io/edge-center/edgecenter` from other applicable installation methods.
4. Preserve all unrelated provider routes and exclusions.
5. Set `TF_CLI_CONFIG_FILE` to the team configuration.

Do not add a second `provider_installation` block. Do not commit credentials, local mirror contents, or machine-specific CLI configuration paths.

Keep the helper-generated configuration separate from your team configuration. Future helper runs can reuse the unchanged generated file.

In each Terraform root, pin the release version:

```hcl
terraform {
  required_providers {
    edgecenter = {
      source = "Edge-Center/edgecenter"
      version = "= 0.16.99-patch4.20261011.2"
    }
  }
}
```

A Git repository URL is not a provider source. A branch name is not a provider version. This installation does not use `dev_overrides`.

After you install the archive and set the exact version, initialize Terraform:

```bash
terraform init
```

## Lock files and multiple platforms

The release checksum file covers the six archives, both helpers, and `provenance.json`. Checksums detect changed bytes. They do not authenticate the publisher.

Trust depends on the repository, the release workflow, and HTTPS. This workflow does not publish GPG signatures or GitHub artifact attestations. An administrator with repository write access can still change tags or releases outside this workflow.

Terraform creates local lock checksums for a filesystem mirror. These packages do not carry an upstream registry signature.

For each required team platform, install its archive into the same mirror:

```bash
for PLATFORM in darwin_amd64 darwin_arm64 linux_amd64 linux_arm64 windows_amd64 windows_arm64; do
  bash "$TOOLS/install-provider.sh" "$REPO" "$VERSION" "$MIRROR" "$CONFIG" "$PLATFORM"
done
terraform providers lock -fs-mirror="$MIRROR" \
  -platform=darwin_amd64 -platform=darwin_arm64 \
  -platform=linux_amd64 -platform=linux_arm64 \
  -platform=windows_amd64 -platform=windows_arm64 \
  registry.terraform.io/edge-center/edgecenter
```

On Windows, use the optional `-Platform` argument to download each required archive. Then run the same `terraform providers lock` command as one line.

Review the EdgeCenter entry in `.terraform.lock.hcl`. Commit the reviewed lock file with the version change. Do not delete the whole lock file or replace unrelated provider entries.

Do not run the lock command against the public registry for this patched version. The public registry does not contain this release.

## Import, upgrade, and rollback

`terraform import` uses the same selected provider binary as other Terraform commands. Keep `TF_CLI_CONFIG_FILE` set for each command.

After initialization, use the resource-specific import format from the provider documentation:

```bash
terraform import RESOURCE_ADDRESS RESOURCE_ID
```

This guide does not run an import or change infrastructure.

For an upgrade:

1. Download the new release with the helper.
2. Change only the exact EdgeCenter version constraint.
3. Refresh the EdgeCenter lock entry from the filesystem mirror.
4. Run `terraform init`.
5. Review `terraform plan` before deployment.

For a rollback:

1. Install the earlier release into the mirror.
2. Restore the earlier version constraint and its reviewed lock entry.
3. Run `terraform init`.
4. Review the plan for compatibility with the current state.

A binary rollback does not roll back infrastructure or Terraform state. Keep earlier mirror packages until the rollback window ends.

## Build and reproducibility

Release builds use Go `1.26.8`, `CGO_ENABLED=0`, and the module versions from `go.mod` and `go.sum`. The builder uses `-mod=readonly`, `-trimpath`, `-buildvcs=false`, and an empty build ID. Linker flags set `main.version` to the exact semantic prerelease version.

The builder creates all six targets:

| Operating system | CPU architectures |
| --- | --- |
| macOS | AMD64, ARM64 |
| Linux | AMD64, ARM64 |
| Windows | AMD64, ARM64 |

ZIP files use stored entries, fixed timestamps, fixed permissions, and fixed filenames. Compression-library versions cannot change these archive bytes.

`provenance.json` records the source commit, required fixes commit, toolchain, build flags, module checksums, and binary checksums. It contains no cloud credentials or author identity.

From a clean checkout of the release tag, rebuild with Python 3.11 or later and Go:

```bash
VERSION=0.16.99-patch4.20261011.2
python3 -I scripts/release.py build --version "$VERSION" --directory /tmp/provider-rebuild-one
python3 -I scripts/release.py build --version "$VERSION" --directory /tmp/provider-rebuild-two
diff /tmp/provider-rebuild-one/SHA256SUMS /tmp/provider-rebuild-two/SHA256SUMS
```

Use new output directories for each build. Compare the rebuilt checksums with the trusted release checksum file.

The release workflow uses `scripts/release.py`, not the inherited `.goreleaser.yml`. It does not use GPG keys, storage buckets, or the inherited Makefile installation targets.

## CI and release procedure

`.github/workflows/build.yml` runs credential-free checks on the dedicated branch and on pull requests. The release workflow reuses these checks.

CI runs unit tests with the race detector, `go vet`, module checks, installer tests, and all six cross-builds. It keeps `TF_ACC` empty. It does not use cloud credentials or run cloud acceptance tests.

Normal CI has `contents: read`. Only the publish job has `contents: write`. All action references use immutable commit SHAs. The publish job uses the short-lived `github.token`. No additional secret is necessary.

To publish a new version:

1. Work on `ci/patched-provider-releases`.
2. Keep commit `87f07794807ccde5ef484232f1fc1060c9935edc` in the branch history.
3. Run the local checks.
4. Commit only the reviewed source and release files.
5. Push the dedicated branch without force.
6. Wait for its CI run to succeed.
7. Choose a new version in the form `0.16.99-patch4.YYYYMMDD.N`.
8. Make sure that neither a tag nor a release uses this version.
9. Create a lightweight tag on the reviewed branch commit.
10. Push that tag.

```bash
VERSION=0.16.99-patch4.20261011.2
git switch ci/patched-provider-releases
git tag "v$VERSION"
git push origin "refs/tags/v$VERSION"
```

Do not reuse `0.16.99-patch4.20261011.1`. That version identifies a separate local build.

The tag workflow checks the tag format, source commit, required ancestry, and dedicated branch ancestry. The workflow does not require a default-branch change.

After all checks pass, the publish job creates a draft prerelease. It uploads all assets and downloads them again for checksum checks. Only then does it publish the prerelease.

If a draft contains identical assets, a retry adds only missing assets. If any existing bytes differ, publication stops. A published version always rejects another publish attempt.

If source changes are necessary after a failed run, use a new tag and version. Do not move a tag or replace release assets.

Monitor the tag workflow in the repository Actions page. A pushed tag is not evidence of a successful release.

## Provider documentation

Resource documentation remains in [docs](docs). Examples remain in [examples](examples).

The V1-to-V2 migration guide remains in [docs/guides/v1-to-v2-migration.md](docs/guides/v1-to-v2-migration.md). Provider upgrades do not run that migration automatically.
