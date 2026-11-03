param(
    [string]$VivadoBin = "",
    [string]$WorkDirectory = "",
    [string]$ResultPath = "",
    [int]$StageNumber = 34,
    [string]$VariantLabel = "34V"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if ([string]::IsNullOrWhiteSpace($VivadoBin)) {
    . (Join-Path $projectRoot "build/scripts/pynq/toolchain.ps1")
    $VivadoBin = Split-Path (Resolve-SrLutVivado)
}
if ([string]::IsNullOrWhiteSpace($WorkDirectory)) {
    $WorkDirectory = Join-Path $projectRoot `
        "build\stage_${StageNumber}_video420_rtl_regression"
}
if ([string]::IsNullOrWhiteSpace($ResultPath)) {
    $ResultPath = Join-Path $projectRoot `
        "board\local\stage_34v_rtl_regression.txt"
}
$stageMarker = "STAGE$($VariantLabel.ToUpperInvariant())"
$xvlog = Join-Path $VivadoBin "xvlog.bat"
$xelab = Join-Path $VivadoBin "xelab.bat"
$xsim = Join-Path $VivadoBin "xsim.bat"
$utf8 = New-Object System.Text.UTF8Encoding($false)
foreach ($tool in @($xvlog, $xelab, $xsim)) {
    if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) {
        throw "Vivado simulator tool is missing: $tool"
    }
}

New-Item -ItemType Directory -Force -Path $WorkDirectory | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $ResultPath) | Out-Null
foreach ($dataDirectory in @("data/lut", "data/golden")) {
    $linkPath = Join-Path $WorkDirectory $dataDirectory
    $targetPath = Join-Path $projectRoot $dataDirectory
    if (-not (Test-Path -LiteralPath $linkPath)) {
        New-Item -ItemType Junction -Path $linkPath -Target $targetPath | Out-Null
    }
}

$rtlNames = @(
    "sr_lut_pkg.sv", "sr_lut_quantizer.sv", "sr_lut_simplex_selector.sv",
    "sr_lut_address_gen.sv", "sr_lut_rom.sv", "sr_lut_interpolator.sv",
    "sr_lut_line_buffer_dynamic.sv", "sr_lut_rotation_map.sv",
    "sr_lut_result_pipeline.sv", "sr_lut_task_pipeline.sv",
    "sr_lut_rgb_scheduler.sv", "sr_lut_fusion.sv", "sr_lut_block_fifo.sv",
    "sr_lut_output_fifo_dynamic.sv", "sr_lut_axis_top_dynamic.sv",
    "sr_lut_perf_monitor.sv", "sr_lut_crc32_word.sv",
    "sr_lut_video420_packer.sv",
    "sr_lut_axis_wrapper_video.v"
)
$rtl = @($rtlNames | ForEach-Object {
    Join-Path $projectRoot "src/rtl\$_"
})
$testbenches = @(
    (Join-Path $projectRoot "sim/pynq\tb_sr_lut_video420_packer.sv"),
    (Join-Path $projectRoot "sim/pynq\tb_sr_lut_axis_wrapper_video.sv")
)
$cases = @(
    @{ Top = "tb_sr_lut_video420_packer"; Marker = "VIDEO420_PACKER_PASS" },
    @{ Top = "tb_sr_lut_axis_wrapper_video"; Marker = "VIDEO_AXIS_WRAPPER_PASS" }
)

Push-Location $WorkDirectory
try {
    $log = New-Object System.Collections.Generic.List[string]
    $compile = & $xvlog -sv @rtl @testbenches 2>&1 |
        ForEach-Object { $_.ToString() }
    if ($LASTEXITCODE -ne 0) { throw "$stageMarker xvlog failed" }
    $log.AddRange([string[]]$compile)
    foreach ($case in $cases) {
        $snapshot = "stage${StageNumber}_video420_" + $case.Top
        $elab = & $xelab $case.Top -L xpm -s $snapshot 2>&1 |
            ForEach-Object { $_.ToString() }
        if ($LASTEXITCODE -ne 0) { throw "$stageMarker xelab failed: $($case.Top)" }
        $sim = & $xsim $snapshot -runall 2>&1 |
            ForEach-Object { $_.ToString() }
        if ($LASTEXITCODE -ne 0) { throw "$stageMarker xsim failed: $($case.Top)" }
        if (($sim -join "`n") -notmatch [regex]::Escape($case.Marker)) {
            throw "$($case.Top) did not print $($case.Marker)"
        }
        $log.Add("")
        $log.Add("=== $($case.Top) ===")
        $log.AddRange([string[]]$elab)
        $log.AddRange([string[]]$sim)
    }
    $log.Add("")
    $log.Add("${stageMarker}_RTL_REGRESSION_PASS cases=$($cases.Count)")
    [System.IO.File]::WriteAllLines($ResultPath, $log, $utf8)
    Write-Output "${stageMarker}_RTL_REGRESSION_PASS cases=$($cases.Count)"
    Write-Output "Evidence: $ResultPath"
}
finally {
    Pop-Location
}
