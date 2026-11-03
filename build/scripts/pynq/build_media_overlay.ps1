param(
    [ValidateSet(2, 4)][int]$Scale = 4,
    [string]$Vivado = "",
    [ValidateSet("build", "implement", "all")][string]$Step = "all",
    [switch]$ModeGpio,
    [switch]$ScaleGpio,
    [int]$StageNumber = 37,
    [string]$BuildLabel = "",
    [string]$ResultsFolder = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "toolchain.ps1")
$Vivado = Resolve-SrLutVivado $Vivado
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
if (-not (Test-Path -LiteralPath $Vivado -PathType Leaf)) {
    throw "Vivado executable is missing: $Vivado"
}
$label = if ([string]::IsNullOrWhiteSpace($BuildLabel)) {
    "stage37_x${Scale}_work"
} else {
    $BuildLabel
}
$results = if ([string]::IsNullOrWhiteSpace($ResultsFolder)) {
    "local/stage37_x${Scale}"
} else {
    $ResultsFolder
}
$mem = if ($Scale -eq 2) {
    "data/lut/bram_mem/model_s_x2_4bit.mem"
} else {
    "data/lut/bram_mem/model_s_x4_5bit.mem"
}
$settings = [ordered]@{
    SR_LUT_STAGE_NUMBER = [string]$StageNumber
    SR_LUT_RESULTS_FOLDER = $results
    SR_LUT_BUILD_LABEL = $label
    SR_LUT_OVERLAY_DIR = "build/$label/export"
    SR_LUT_WRAPPER_VARIANT = "video420"
    SR_LUT_S2MM_PORT = "acp"
    SR_LUT_MODE_GPIO = if ($ModeGpio) { "1" } else { "0" }
    SR_LUT_SCALE_GPIO = if ($ScaleGpio) { "1" } else { "0" }
    SR_LUT_IMPL_STRATEGY = "Performance_ExplorePostRoutePhysOpt"
    SR_LUT_SCALE = [string]$Scale
    SR_LUT_SAMPLE_BITS = if ($Scale -eq 2) { "4" } else { "5" }
    SR_LUT_Q = if ($Scale -eq 2) { "16" } else { "32" }
    SR_LUT_LEVELS = if ($Scale -eq 2) { "17" } else { "9" }
    SR_LUT_LUT_DEPTH = if ($Scale -eq 2) { "83521" } else { "6561" }
    SR_LUT_ADDR_BITS = if ($Scale -eq 2) { "17" } else { "13" }
    SR_LUT_SCHEDULER_CORES = if ($Scale -eq 2) { "1" } else { "3" }
    SR_LUT_MEM_FILE = $mem
}
$previous = @{}
foreach ($entry in $settings.GetEnumerator()) {
    $previous[$entry.Key] = [Environment]::GetEnvironmentVariable($entry.Key, "Process")
    [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Process")
}
try {
    if ($Step -in @("build", "all")) {
        & $Vivado -mode batch -source (Join-Path $PSScriptRoot "build_zynq_dynamic_system.tcl")
        if ($LASTEXITCODE -ne 0) { throw "Vivado BD generation failed" }
    }
    if ($Step -in @("implement", "all")) {
        & $Vivado -mode batch -source (Join-Path $PSScriptRoot "implement_zynq_dynamic_system.tcl")
        if ($LASTEXITCODE -ne 0) { throw "Vivado implementation failed" }
        & $Vivado -mode batch -source (Join-Path $PSScriptRoot "verify_dynamic_system.tcl")
        if ($LASTEXITCODE -ne 0) { throw "Vivado post-build verification failed" }
    }
    Write-Output "STAGE${StageNumber}_MEDIA_X${Scale}_PASS"
    Write-Output "Overlay: $projectRoot\build\$label\export"
} finally {
    foreach ($entry in $settings.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($entry.Key, $previous[$entry.Key], "Process")
    }
}
