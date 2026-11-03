param(
    [string]$Vivado = "",
    [ValidateSet("build", "implement", "all")][string]$Step = "all",
    [ValidateSet("acp", "hp0", "hp1")][string]$S2mmPort = "acp",
    [switch]$ModeGpio,
    [switch]$ScaleGpio,
    [int]$StageNumber = 38,
    [ValidateSet(
        "Performance_ExplorePostRoutePhysOpt",
        "Performance_ExtraTimingOpt",
        "Performance_Explore"
    )][string]$ImplStrategy = "Performance_ExplorePostRoutePhysOpt",
    [string]$BuildLabel = "",
    [string]$ResultsFolder = "",
    [string]$OverlayDirectory = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "toolchain.ps1")
$Vivado = Resolve-SrLutVivado $Vivado
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
if (-not (Test-Path -LiteralPath $Vivado -PathType Leaf)) {
    throw "Vivado executable is missing: $Vivado"
}

$strategyCode = @{
    Performance_ExplorePostRoutePhysOpt = ""
    Performance_ExtraTimingOpt = "eto"
    Performance_Explore = "exp"
}[$ImplStrategy]
$variantParts = @()
if ($S2mmPort -ne "acp") {
    $variantParts += $S2mmPort
}
if ($strategyCode) {
    $variantParts += $strategyCode
}
$variantSuffix = if ($variantParts.Count) {
    "_" + ($variantParts -join "_")
} else {
    ""
}
if ([string]::IsNullOrWhiteSpace($BuildLabel)) {
    $BuildLabel = "stage38_mjpeg${variantSuffix}_work"
}
if ([string]::IsNullOrWhiteSpace($ResultsFolder)) {
    $ResultsFolder = "local/stage38_mjpeg$variantSuffix"
}
if ([string]::IsNullOrWhiteSpace($OverlayDirectory)) {
    $OverlayDirectory = "build/stage38_mjpeg${variantSuffix}_work/export"
}

$settings = [ordered]@{
    SR_LUT_STAGE_NUMBER = [string]$StageNumber
    SR_LUT_RESULTS_FOLDER = $ResultsFolder
    SR_LUT_BUILD_LABEL = $BuildLabel
    SR_LUT_OVERLAY_DIR = $OverlayDirectory
    SR_LUT_WRAPPER_VARIANT = "mjpeg"
    SR_LUT_S2MM_PORT = $S2mmPort
    SR_LUT_MODE_GPIO = if ($ModeGpio) { "1" } else { "0" }
    SR_LUT_SCALE_GPIO = if ($ScaleGpio) { "1" } else { "0" }
    SR_LUT_IMPL_STRATEGY = $ImplStrategy
    SR_LUT_SCALE = "4"
    SR_LUT_SAMPLE_BITS = "5"
    SR_LUT_Q = "32"
    SR_LUT_LEVELS = "9"
    SR_LUT_LUT_DEPTH = "6561"
    SR_LUT_ADDR_BITS = "13"
    SR_LUT_SCHEDULER_CORES = "3"
    SR_LUT_MEM_FILE = "data/lut/bram_mem/model_s_x4_5bit.mem"
}

$previous = @{}
foreach ($entry in $settings.GetEnumerator()) {
    $previous[$entry.Key] = [Environment]::GetEnvironmentVariable(
        $entry.Key, "Process")
    [Environment]::SetEnvironmentVariable(
        $entry.Key, $entry.Value, "Process")
}

try {
    if ($Step -in @("build", "all")) {
        & $Vivado -mode batch -source `
            (Join-Path $PSScriptRoot "build_zynq_dynamic_system.tcl")
        if ($LASTEXITCODE -ne 0) {
            throw "Stage $StageNumber MJPEG block design generation failed"
        }
    }
    if ($Step -in @("implement", "all")) {
        & $Vivado -mode batch -source `
            (Join-Path $PSScriptRoot "implement_zynq_dynamic_system.tcl")
        if ($LASTEXITCODE -ne 0) {
            throw "Stage $StageNumber MJPEG implementation failed"
        }
        & $Vivado -mode batch -source `
            (Join-Path $PSScriptRoot "verify_dynamic_system.tcl")
        if ($LASTEXITCODE -ne 0) {
            throw "Stage $StageNumber MJPEG post-build verification failed"
        }
    }
    Write-Output "STAGE${StageNumber}_MJPEG_1080P_PASS"
    Write-Output "S2MM port: $S2mmPort"
    Write-Output "Implementation strategy: $ImplStrategy"
    Write-Output "Overlay: $projectRoot\$($OverlayDirectory.Replace('/', '\'))"
} finally {
    foreach ($entry in $settings.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable(
            $entry.Key, $previous[$entry.Key], "Process")
    }
}
