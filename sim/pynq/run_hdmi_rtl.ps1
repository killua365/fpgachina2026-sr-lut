param([string]$WorkDirectory = '')
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
. (Join-Path $root 'build/scripts/pynq/toolchain.ps1')
$bin = Split-Path (Resolve-SrLutVivado)
$work = if ($WorkDirectory) { [IO.Path]::GetFullPath($WorkDirectory) } else { Join-Path $env:TEMP ('sr_lut_hdmi_rtl_' + [guid]::NewGuid().ToString('N')) }
if (Test-Path -LiteralPath $work) { throw 'Choose a new output directory; retain earlier simulation evidence.' }
if ($work -match '[^\x00-\x7F]') { throw 'Use an ASCII-only simulation output directory.' }
New-Item -ItemType Directory -Force -Path $work | Out-Null
Push-Location $work
try {
    & "$bin/xvlog.bat" --sv "$bin/../data/verilog/src/glbl.v" "$root/src/rtl/sr_lut_rgbx_video_adapter.v" "$root/src/rtl/sr_lut_hdmi_monitor.v" "$root/sim/pynq/tb_hdmi_output.sv"
    if ($LASTEXITCODE) { throw 'HDMI RTL compilation failed.' }
    & "$bin/xelab.bat" -L xpm tb_hdmi_output glbl -s hdmi_output_test
    if ($LASTEXITCODE) { throw 'HDMI RTL elaboration failed.' }
    $result = & "$bin/xsim.bat" hdmi_output_test -runall 2>&1
    $result | Write-Output
    if ($LASTEXITCODE -or -not ($result -match 'HDMI_OUTPUT_RTL_PASS')) {
        throw 'HDMI RTL simulation failed.'
    }
} finally { Pop-Location }
