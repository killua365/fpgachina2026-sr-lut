param(
    [ValidateSet(2, 4)][int]$Scale = 4,
    [ValidateSet('build', 'implement', 'all')][string]$Step = 'all',
    [string]$Vivado = ''
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'toolchain.ps1')
$Vivado = Resolve-SrLutVivado $Vivado
$root = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
$temporary = Join-Path $root "build/hdmi_x${Scale}_work/temp"
$modeSuffix = switch ($env:SR_LUT_HDMI_MODE) {
    '1080p30' { '_1080p30'; break }
    '1080p60' { '_1080p60'; break }
    default { '' }
}
$outputStem = "hdmi_x${Scale}${modeSuffix}_work"
$resultsStem = "hdmi_x${Scale}${modeSuffix}"
New-Item -ItemType Directory -Force -Path $temporary | Out-Null
$settings = [ordered]@{
    TEMP = $temporary.Replace('\', '/')
    TMP = $temporary.Replace('\', '/')
    SR_LUT_HDMI = '1'
    SR_LUT_STAGE_NUMBER = '40'
    SR_LUT_RESULTS_FOLDER = "local/$resultsStem"
    SR_LUT_BUILD_LABEL = $outputStem
    SR_LUT_OVERLAY_DIR = "build/$outputStem/export"
    SR_LUT_WRAPPER_VARIANT = 'dynamic'
    SR_LUT_S2MM_PORT = 'acp'
    SR_LUT_MODE_GPIO = '1'
    SR_LUT_SCALE_GPIO = '1'
    SR_LUT_SCALE = [string]$Scale
    SR_LUT_SAMPLE_BITS = if ($Scale -eq 2) { '4' } else { '5' }
    SR_LUT_Q = if ($Scale -eq 2) { '16' } else { '32' }
    SR_LUT_LEVELS = if ($Scale -eq 2) { '17' } else { '9' }
    SR_LUT_LUT_DEPTH = if ($Scale -eq 2) { '83521' } else { '6561' }
    SR_LUT_ADDR_BITS = if ($Scale -eq 2) { '17' } else { '13' }
    SR_LUT_SCHEDULER_CORES = if ($Scale -eq 2) { '1' } else { '3' }
    SR_LUT_MEM_FILE = if ($Scale -eq 2) { 'data/lut/bram_mem/model_s_x2_4bit.mem' } else { 'data/lut/bram_mem/model_s_x4_5bit.mem' }
    SR_LUT_IMPL_STRATEGY = 'Performance_ExplorePostRoutePhysOpt'
    SR_LUT_MIN_SETUP_WNS = '0.0'
}
$previous = @{}
foreach ($key in $settings.Keys) {
    $previous[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
    [Environment]::SetEnvironmentVariable($key, $settings[$key], 'Process')
}
try {
    if ($Step -in @('build', 'all')) {
        & $Vivado -mode batch -notrace -source (Join-Path $PSScriptRoot 'build_zynq_dynamic_system.tcl')
        if ($LASTEXITCODE -ne 0) { throw 'HDMI BD generation failed.' }
    }
    if ($Step -in @('implement', 'all')) {
        & $Vivado -mode batch -notrace -source (Join-Path $PSScriptRoot 'implement_zynq_dynamic_system.tcl')
        if ($LASTEXITCODE -ne 0) { throw 'HDMI implementation failed.' }
    }
    Write-Output "HDMI_X${Scale}_${Step}_PASS (not board acceptance)"
} finally {
    foreach ($key in $settings.Keys) {
        [Environment]::SetEnvironmentVariable($key, $previous[$key], 'Process')
    }
}
