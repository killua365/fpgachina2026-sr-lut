param(
    [ValidateSet("all", "media-x2", "media-x4", "camera")]
    [string]$Component = "all",
    [ValidateSet("build", "implement", "all")]
    [string]$Step = "all",
    [string]$Vivado = "",
    [ValidatePattern('^[a-z0-9_]+$')]
    [string]$BuildSuffix = "vivado2026_1",
    [ValidateSet(
        "Performance_ExplorePostRoutePhysOpt",
        "Performance_ExtraTimingOpt",
        "Performance_Explore"
    )][string]$CameraImplStrategy = "Performance_ExtraTimingOpt"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "toolchain.ps1")
$Vivado = Resolve-SrLutVivado $Vivado

function Build-MediaOverlay {
    param([ValidateSet(2, 4)][int]$Scale)

    & (Join-Path $PSScriptRoot "build_media_overlay.ps1") `
        -Scale $Scale `
        -Vivado $Vivado `
        -Step $Step `
        -ModeGpio `
        -ScaleGpio `
        -StageNumber 39 `
        -BuildLabel "stage39_media_x${Scale}_${BuildSuffix}_work" `
        -ResultsFolder "local/stage39_media_x${Scale}_${BuildSuffix}"
    if (-not $?) { throw "Stage 39 media x$Scale build failed" }
}

if ($Component -in @("all", "media-x2")) { Build-MediaOverlay -Scale 2 }
if ($Component -in @("all", "media-x4")) { Build-MediaOverlay -Scale 4 }
if ($Component -in @("all", "camera")) {
    & (Join-Path $PSScriptRoot "build_camera_overlay.ps1") `
        -Vivado $Vivado `
        -Step $Step `
        -S2mmPort hp1 `
        -ModeGpio `
        -ScaleGpio `
        -StageNumber 39 `
        -ImplStrategy $CameraImplStrategy `
        -BuildLabel "stage39_camera_x4_${BuildSuffix}_work" `
        -ResultsFolder "local/stage39_camera_x4_${BuildSuffix}" `
        -OverlayDirectory "build/stage39_camera_x4_${BuildSuffix}_work/export"
    if (-not $?) { throw "Stage 39 HP1 camera build failed" }
}

Write-Output "STAGE39_UNIFIED_$($Component.ToUpperInvariant())_PASS"
