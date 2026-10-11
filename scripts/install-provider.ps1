param(
    [Parameter(Mandatory = $true)][string]$Repository,
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$Mirror,
    [Parameter(Mandatory = $true)][string]$Config,
    [ValidateSet('darwin_amd64', 'darwin_arm64', 'linux_amd64', 'linux_arm64', 'windows_amd64', 'windows_arm64')]
    [string]$Platform
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($Repository -cnotmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
    throw 'The repository address is invalid.'
}
if ($Version -cnotmatch '^0\.16\.99-patch4\.[0-9]{8}\.[1-9][0-9]*$') {
    throw 'The release version is invalid. Omit the v prefix.'
}
if (-not $Platform) {
    $architecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
    switch ($architecture) {
        'X64' { $Platform = 'windows_amd64' }
        'Arm64' { $Platform = 'windows_arm64' }
        default { throw 'This CPU architecture is not supported.' }
    }
    if (-not [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform([System.Runtime.InteropServices.OSPlatform]::Windows)) {
        throw 'Use the Bash helper on Linux and macOS, or specify Platform.'
    }
}
$Mirror = [System.IO.Path]::GetFullPath($Mirror)
$Config = [System.IO.Path]::GetFullPath($Config)
$mirrorHcl = $Mirror.Replace('\', '/')
if ($mirrorHcl -match '["\r\n$%]') {
    throw 'Use a mirror path without quotes, interpolation markers, or newlines.'
}
$temporary = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString())
$null = New-Item -ItemType Directory -Path $temporary
try {
    $archiveDirectory = New-Item -ItemType Directory -Path (Join-Path $temporary 'archive')
    $checksumDirectory = New-Item -ItemType Directory -Path (Join-Path $temporary 'checksums')
    $archive = "terraform-provider-edgecenter_${Version}_${Platform}.zip"
    $archivePath = Join-Path $archiveDirectory.FullName $archive
    $checksumPath = Join-Path $checksumDirectory.FullName 'SHA256SUMS'
    $baseUrl = "https://github.com/$Repository/releases/download/v$Version"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri "$baseUrl/SHA256SUMS" -OutFile $checksumPath -UseBasicParsing
    Invoke-WebRequest -Uri "$baseUrl/$archive" -OutFile $archivePath -UseBasicParsing
    $entries = @(Get-Content -LiteralPath $checksumPath | Where-Object { $_ -cmatch ('^[0-9a-f]{64}  ' + [regex]::Escape($archive) + '$') })
    if ($entries.Count -ne 1) {
        throw 'The checksum entry is missing or duplicated.'
    }
    $expected = $entries[0].Substring(0, 64)
    if ((Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $expected) {
        throw 'The archive checksum does not match.'
    }
    $binary = "terraform-provider-edgecenter_v${Version}_x5"
    if ($Platform.StartsWith('windows_')) { $binary += '.exe' }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($archivePath)
    try {
        if ($zip.Entries.Count -ne 1 -or $zip.Entries[0].FullName -cne $binary) {
            throw 'The archive must contain exactly one provider binary.'
        }
    } finally {
        $zip.Dispose()
    }
    $configuration = @"
provider_installation {
  filesystem_mirror {
    path = "$mirrorHcl"
    include = ["registry.terraform.io/edge-center/edgecenter"]
  }
  direct {
    exclude = ["registry.terraform.io/edge-center/edgecenter"]
  }
}
"@ + "`n"
    $configuration = $configuration.Replace("`r`n", "`n")
    if (Test-Path -LiteralPath $Config) {
        if ([System.IO.File]::ReadAllText($Config) -cne $configuration) {
            throw 'The CLI configuration already exists with different content. Use another path.'
        }
    }
    $target = Join-Path $Mirror 'registry.terraform.io/edge-center/edgecenter'
    $null = New-Item -ItemType Directory -Path $target -Force
    $destination = Join-Path $target $archive
    if (Test-Path -LiteralPath $destination) {
        if ((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant() -cne $expected) {
            throw 'The mirror contains different bytes for this version. Use a new version.'
        }
    } else {
        $staged = Join-Path $target ('.install.' + [System.Guid]::NewGuid().ToString())
        try {
            [System.IO.File]::Copy($archivePath, $staged, $false)
            [System.IO.File]::Move($staged, $destination)
        } finally {
            if (Test-Path -LiteralPath $staged) { Remove-Item -LiteralPath $staged }
        }
    }
    if (-not (Test-Path -LiteralPath $Config)) {
        $stream = [System.IO.File]::Open($Config, [System.IO.FileMode]::CreateNew)
        try {
            $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($configuration)
            $stream.Write($bytes, 0, $bytes.Length)
        } finally {
            $stream.Dispose()
        }
    }
    Write-Host "Installed $archive."
    Write-Host "CLI configuration: $Config"
    Write-Output ('$env:TF_CLI_CONFIG_FILE = ' + "'" + $Config.Replace("'", "''") + "'")
} finally {
    Remove-Item -LiteralPath $temporary -Recurse -Force
}
