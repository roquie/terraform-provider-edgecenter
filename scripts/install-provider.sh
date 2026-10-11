#!/usr/bin/env bash
set -euo pipefail
umask 022

fail() {
  printf '%s\n' "$*" >&2
  exit 1
}

[[ $# -ge 4 && $# -le 5 ]] || fail 'Usage: bash install-provider.sh OWNER/REPOSITORY VERSION MIRROR CONFIG [OS_ARCH]'
repository=$1
version=$2
mirror=$3
config=$4
[[ $repository =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || fail 'The repository address is invalid.'
[[ $version =~ ^0\.16\.99-patch4\.[0-9]{8}\.[1-9][0-9]*$ ]] || fail 'The release version is invalid. Omit the v prefix.'

if [[ $# -eq 5 ]]; then
  platform=$5
else
  case "$(uname -s)" in
    Darwin) operating_system=darwin ;;
    Linux) operating_system=linux ;;
    *) fail 'Use the PowerShell helper on Windows.' ;;
  esac
  case "$(uname -m)" in
    x86_64|amd64) architecture=amd64 ;;
    aarch64|arm64) architecture=arm64 ;;
    *) fail 'This CPU architecture is not supported.' ;;
  esac
  platform="${operating_system}_${architecture}"
fi
case "$platform" in
  darwin_amd64|darwin_arm64|linux_amd64|linux_arm64|windows_amd64|windows_arm64) ;;
  *) fail 'This platform is not supported.' ;;
esac

for tool in curl unzip cmp mktemp; do
  command -v "$tool" >/dev/null || fail "Install $tool before you run this helper."
done
hash_file() {
  if command -v sha256sum >/dev/null; then
    sha256sum "$1"
  else
    shasum -a 256 "$1"
  fi
}

mkdir -p "$mirror"
mirror=$(cd "$mirror" && pwd -P)
case "$mirror" in
  *'"'*|*'\'*|*'$'*|*'%'*|*$'\n'*|*$'\r'*) fail 'Use a mirror path without quotes, backslashes, interpolation markers, or newlines.' ;;
esac
[[ $config = /* ]] || config="$PWD/$config"
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
mkdir "$temporary/checksums" "$temporary/archive"
archive="terraform-provider-edgecenter_${version}_${platform}.zip"
base_url="https://github.com/$repository/releases/download/v$version"
curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' --tlsv1.2 \
  "$base_url/SHA256SUMS" -o "$temporary/checksums/SHA256SUMS"
curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' --tlsv1.2 \
  "$base_url/$archive" -o "$temporary/archive/$archive"
expected=''
while read -r checksum name extra; do
  if [[ $name == "$archive" ]]; then
    [[ -z $expected && -z ${extra:-} && $checksum =~ ^[0-9a-f]{64}$ ]] || fail 'The checksum entry is invalid or duplicated.'
    expected=$checksum
  fi
done < "$temporary/checksums/SHA256SUMS"
[[ -n $expected ]] || fail 'The archive has no checksum entry.'
actual=$(hash_file "$temporary/archive/$archive")
[[ ${actual%% *} == "$expected" ]] || fail 'The archive checksum does not match.'
binary="terraform-provider-edgecenter_v${version}_x5"
[[ $platform != windows_* ]] || binary="$binary.exe"
[[ $(unzip -Z1 "$temporary/archive/$archive") == "$binary" ]] || fail 'The archive must contain exactly one provider binary.'
unzip -tqq "$temporary/archive/$archive" >/dev/null

printf 'provider_installation {\n  filesystem_mirror {\n    path = "%s"\n    include = ["registry.terraform.io/edge-center/edgecenter"]\n  }\n  direct {\n    exclude = ["registry.terraform.io/edge-center/edgecenter"]\n  }\n}\n' \
  "$mirror" > "$temporary/terraform.rc"
if [[ -e $config || -L $config ]]; then
  cmp -s "$temporary/terraform.rc" "$config" || fail 'The CLI configuration already exists with different content. Use another path.'
fi
target="$mirror/registry.terraform.io/edge-center/edgecenter"
mkdir -p "$target"
if [[ -e $target/$archive || -L $target/$archive ]]; then
  cmp -s "$temporary/archive/$archive" "$target/$archive" || fail 'The mirror contains different bytes for this version. Use a new version.'
else
  staged=$(mktemp "$target/.install.XXXXXX")
  cp "$temporary/archive/$archive" "$staged"
  chmod 0644 "$staged"
  if ! ln "$staged" "$target/$archive"; then
    rm -f "$staged"
    fail 'The mirror file appeared during installation. No file was replaced.'
  fi
  rm -f "$staged"
fi
if [[ ! -e $config && ! -L $config ]]; then
  (set -o noclobber; printf '%s\n' "$(< "$temporary/terraform.rc")" > "$config")
fi
printf 'Installed %s.\nCLI configuration: %s\n' "$archive" "$config" >&2
printf 'export TF_CLI_CONFIG_FILE=%q\n' "$config"
