param([string]$Vivado = "", [string]$OutputDirectory = "")
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "toolchain.ps1")
$Vivado = Resolve-SrLutVivado $Vivado
$root = (Resolve-Path (Join-Path $PSScriptRoot "../../..")).Path
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $root "board/local/toolchain_2026_1"
}
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$OutputDirectory = (Resolve-Path $OutputDirectory).Path
$result = [ordered]@{
    checked_utc = [DateTime]::UtcNow.ToString("o")
    executable = $Vivado
    expected_version = "2026.1"
    target_part = "xc7z020clg400-1"
    status = "running"
}
$previousPreference = $ErrorActionPreference
try {
    $ErrorActionPreference = "Continue"
    $lines = @(& $Vivado -mode batch -source (Join-Path $PSScriptRoot "check_toolchain.tcl") `
        -log (Join-Path $OutputDirectory "vivado.log") `
        -journal (Join-Path $OutputDirectory "vivado.jou") 2>&1 | ForEach-Object { $_.ToString() })
    $result.exit_code = $LASTEXITCODE
} finally { $ErrorActionPreference = $previousPreference }
$result.output = $lines
$result.status = if ($result.exit_code -eq 0 -and
    ($lines -match 'SR_LUT_TOOLCHAIN_CHECK_PASS version=2026.1')) { "pass" } else { "fail" }
[IO.File]::WriteAllText((Join-Path $OutputDirectory "toolchain_check.json"),
    ($result | ConvertTo-Json -Depth 6) + "`n", [Text.UTF8Encoding]::new($false))
$lines | Write-Output
if ($result.status -ne "pass") { throw "Vivado 2026.1 environment check failed; see $OutputDirectory" }
