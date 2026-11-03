param(
    [string]$VivadoBin = "",
    [int]$StageNumber = 33,
    [string]$ResultsFolder = "compatibility_stage33",
    [string]$WorkDirectory = "",
    [string]$ResultPath = "",
    [string]$SchedulerFile = "",
    [string]$LineBufferFile = "",
    [string]$CaseName = "",
    [ValidateSet("", "typical", "all")]
    [string]$DebugLevel = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if ([string]::IsNullOrWhiteSpace($VivadoBin)) {
    . (Join-Path $projectRoot "build/scripts/pynq/toolchain.ps1")
    $VivadoBin = Split-Path (Resolve-SrLutVivado)
}
$stageMarker = "STAGE$StageNumber"
if ([string]::IsNullOrWhiteSpace($WorkDirectory)) {
    $workDir = Join-Path $projectRoot "build\stage_${StageNumber}_full_rtl_regression"
}
else {
    $workDir = [System.IO.Path]::GetFullPath($WorkDirectory)
}
if ([string]::IsNullOrWhiteSpace($ResultPath)) {
    $resultPath = Join-Path $projectRoot `
        "board\$ResultsFolder\stage_${StageNumber}a_full_rtl_regression.txt"
}
else {
    $resultPath = [System.IO.Path]::GetFullPath($ResultPath)
}
$xvlog = Join-Path $VivadoBin "xvlog.bat"
$xelab = Join-Path $VivadoBin "xelab.bat"
$xsim = Join-Path $VivadoBin "xsim.bat"
$utf8 = New-Object System.Text.UTF8Encoding($false)

if ([string]::IsNullOrWhiteSpace($SchedulerFile)) {
    $schedulerPath = Join-Path $projectRoot "src/rtl\sr_lut_rgb_scheduler.sv"
}
else {
    $schedulerPath = [System.IO.Path]::GetFullPath($SchedulerFile)
}
if (-not (Test-Path -LiteralPath $schedulerPath -PathType Leaf)) {
    throw "Scheduler RTL is missing: $schedulerPath"
}
if ([string]::IsNullOrWhiteSpace($LineBufferFile)) {
    $lineBufferPath = Join-Path $projectRoot "src/rtl\sr_lut_line_buffer_dynamic.sv"
}
else {
    $lineBufferPath = [System.IO.Path]::GetFullPath($LineBufferFile)
}
if (-not (Test-Path -LiteralPath $lineBufferPath -PathType Leaf)) {
    throw "Line-buffer RTL is missing: $lineBufferPath"
}

foreach ($tool in @($xvlog, $xelab, $xsim)) {
    if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) {
        throw "Vivado simulator tool is missing: $tool"
    }
}

New-Item -ItemType Directory -Force -Path $workDir | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $resultPath) | Out-Null

# Testbenches use project-relative data paths. Junctions keep those paths valid
# while all XSim-generated files stay inside build.
foreach ($dataDirectory in @("data/lut", "data/golden")) {
    $linkPath = Join-Path $workDir $dataDirectory
    $targetPath = Join-Path $projectRoot $dataDirectory
    if (-not (Test-Path -LiteralPath $linkPath)) {
        New-Item -ItemType Junction -Path $linkPath -Target $targetPath | Out-Null
    }
}

function Invoke-Captured {
    param(
        [string]$Executable,
        [string[]]$Arguments,
        [string]$LogPath
    )

    $lines = & $Executable @Arguments 2>&1 | ForEach-Object { $_.ToString() }
    $exitCode = $LASTEXITCODE
    [System.IO.File]::WriteAllLines($LogPath, [string[]]$lines, $utf8)
    if ($exitCode -ne 0) {
        throw "$Executable failed with exit code $exitCode; see $LogPath"
    }
    return [string[]]$lines
}

$rtlFiles = @(
    "src/rtl\sr_lut_pkg.sv",
    "src/rtl\sr_lut_quantizer.sv",
    "src/rtl\sr_lut_simplex_selector.sv",
    "src/rtl\sr_lut_address_gen.sv",
    "src/rtl\sr_lut_rom.sv",
    "src/rtl\sr_lut_interpolator.sv",
    $lineBufferPath,
    "src/rtl\sr_lut_rotation_map.sv",
    "src/rtl\sr_lut_result_pipeline.sv",
    "src/rtl\sr_lut_task_pipeline.sv",
    $schedulerPath,
    "src/rtl\sr_lut_fusion.sv",
    "src/rtl\sr_lut_block_fifo.sv",
    "src/rtl\sr_lut_perf_monitor.sv",
    "src/rtl\sr_lut_output_fifo_dynamic.sv",
    "src/rtl\sr_lut_axis_top_dynamic.sv",
    "src/rtl\sr_lut_axis_wrapper_dynamic.v"
)
$testbenchFiles = @(
    "sim/pynq\tb_sr_lut_quantizer.sv",
    "sim/pynq\tb_sr_lut_simplex_selector.sv",
    "sim/pynq\tb_sr_lut_address_gen.sv",
    "sim/pynq\tb_sr_lut_rom.sv",
    "sim/pynq\tb_sr_lut_interpolator.sv",
    "sim/pynq\tb_sr_lut_interpolator_throughput.sv",
    "sim/pynq\tb_sr_lut_fusion_throughput.sv",
    "sim/pynq\tb_sr_lut_block_fifo.sv",
    "sim/pynq\tb_sr_lut_result_pipeline.sv",
    "sim/pynq\tb_sr_lut_e6.sv",
    "sim/pynq\tb_sr_lut_perf_monitor.sv",
    "sim/pynq\tb_sr_lut_line_buffer_dynamic.sv",
    "sim/pynq\tb_sr_lut_output_fifo_dynamic.sv",
    "sim/pynq\tb_sr_lut_axis_top_throughput.sv",
    "sim/pynq\tb_sr_lut_axis_wrapper_dynamic.sv"
)
$rtlFiles = @(
    $rtlFiles | ForEach-Object {
        if ([System.IO.Path]::IsPathRooted($_)) {
            $_
        }
        else {
            Join-Path $projectRoot $_
        }
    }
)
$testbenchFiles = @(
    $testbenchFiles | ForEach-Object { Join-Path $projectRoot $_ }
)
$cases = @(
    @{ Top = "tb_sr_lut_quantizer"; Marker = "E1_QUANTIZER_PASS" },
    @{ Top = "tb_sr_lut_simplex_selector"; Marker = "E2_SIMPLEX_PASS" },
    @{ Top = "tb_sr_lut_address_gen"; Marker = "E3_ADDRESS_PASS" },
    @{ Top = "tb_sr_lut_rom"; Marker = "E4_ROM_PASS" },
    @{ Top = "tb_sr_lut_interpolator"; Marker = "E4_INTERPOLATOR_PASS" },
    @{
        Top = "tb_sr_lut_interpolator_throughput"
        Marker = "SR_LUT_INTERPOLATOR_THROUGHPUT_PASS"
    },
    @{
        Top = "tb_sr_lut_fusion_throughput"
        Marker = "SR_LUT_FUSION_THROUGHPUT_PASS"
    },
    @{
        Top = "tb_sr_lut_block_fifo"
        Marker = "SR_LUT_BLOCK_FIFO_PASS"
    },
    @{
        Top = "tb_sr_lut_result_pipeline"
        Marker = "SR_LUT_RESULT_PIPELINE_PASS"
    },
    @{ Top = "tb_sr_lut_e6"; Marker = "E6_PIPELINE_PASS" },
    @{ Top = "tb_sr_lut_perf_monitor"; Marker = "PERF_MONITOR_PASS" },
    @{
        Top = "tb_sr_lut_line_buffer_dynamic"
        Marker = "DYNAMIC_LINE_BUFFER_PASS frames=13"
    },
    @{
        Top = "tb_sr_lut_output_fifo_dynamic"
        Marker = "DYNAMIC_OUTPUT_FIFO_PASS frames=13"
    },
    @{
        Top = "tb_sr_lut_axis_top_throughput"
        Marker = "SR_LUT_AXIS_TOP_IDEAL_THROUGHPUT_PASS"
    },
    @{
        Top = "tb_sr_lut_axis_wrapper_dynamic"
        Marker = "DYNAMIC_AXIS_WRAPPER_PASS"
    },
    @{
        Top = "tb_sr_lut_axis_wrapper_dynamic_x2"
        Marker = "DYNAMIC_AXIS_WRAPPER_PASS scale=2"
    }
)
if (-not [string]::IsNullOrWhiteSpace($CaseName)) {
    $cases = @($cases | Where-Object { $_.Top -eq $CaseName })
    if ($cases.Count -ne 1) {
        throw "Unknown RTL regression case: $CaseName"
    }
}
$runLabel = if ([string]::IsNullOrWhiteSpace($CaseName)) {
    "FULL_RTL_REGRESSION"
}
else {
    "RTL_CASE"
}

Push-Location $workDir
try {
    $compileLog = Join-Path $workDir "xvlog.txt"
    $compileLines = Invoke-Captured $xvlog `
        (@("-sv") + $rtlFiles + $testbenchFiles) $compileLog
    $evidence = New-Object System.Collections.Generic.List[string]
    $evidence.Add("${stageMarker}_${runLabel}")
    $evidence.Add("SCHEDULER $schedulerPath")
    $evidence.Add("LINE_BUFFER $lineBufferPath")
    $evidence.Add("COMPILE_EXIT_CODE 0")
    $evidence.AddRange([string[]]$compileLines)

    foreach ($case in $cases) {
        $snapshot = "stage${StageNumber}_all_" + $case.Top
        $elabLog = Join-Path $workDir ($case.Top + "_xelab.txt")
        $simLog = Join-Path $workDir ($case.Top + "_xsim.txt")
        $elabArguments = @($case.Top, "-L", "xpm", "-s", $snapshot)
        if (-not [string]::IsNullOrWhiteSpace($DebugLevel)) {
            $elabArguments += @("-debug", $DebugLevel)
        }
        $elabLines = Invoke-Captured $xelab $elabArguments $elabLog
        $simLines = Invoke-Captured $xsim @($snapshot, "-runall") $simLog
        $simText = $simLines -join "`n"
        if ($simText -notmatch [regex]::Escape($case.Marker)) {
            throw "$($case.Top) did not print $($case.Marker)"
        }
        $evidence.Add("")
        $evidence.Add("=== $($case.Top) ===")
        $evidence.AddRange([string[]]$elabLines)
        $evidence.AddRange([string[]]$simLines)
    }

    $evidence.Add("")
    if ([string]::IsNullOrWhiteSpace($CaseName)) {
        $passMarker = "${stageMarker}_FULL_RTL_REGRESSION_PASS cases=$($cases.Count)"
    }
    else {
        $passMarker = "${stageMarker}_RTL_CASE_PASS case=$CaseName"
    }
    $evidence.Add($passMarker)
    [System.IO.File]::WriteAllLines($resultPath, $evidence, $utf8)
    Write-Output $passMarker
    Write-Output "Evidence: $resultPath"
}
finally {
    Pop-Location
}
