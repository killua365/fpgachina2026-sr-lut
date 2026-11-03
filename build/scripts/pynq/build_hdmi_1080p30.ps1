param(
    [ValidateSet(2, 4)][int]$Scale = 4,
    [ValidateSet('build', 'implement', 'all')][string]$Step = 'all',
    [string]$Vivado = ''
)
$ErrorActionPreference = 'Stop'
$previous = $env:SR_LUT_HDMI_MODE
$env:SR_LUT_HDMI_MODE = '1080p30'
try {
    & (Join-Path $PSScriptRoot 'build_hdmi_overlay.ps1') -Scale $Scale -Step $Step -Vivado $Vivado
    if ($LASTEXITCODE -ne 0) { throw "1080p30 HDMI x$Scale build failed" }
} finally {
    if ($null -eq $previous) { Remove-Item Env:SR_LUT_HDMI_MODE -ErrorAction SilentlyContinue }
    else { $env:SR_LUT_HDMI_MODE = $previous }
}
