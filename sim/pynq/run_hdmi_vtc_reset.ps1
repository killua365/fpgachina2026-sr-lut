param([switch]$Scanout)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
. (Join-Path $root 'build/scripts/pynq/toolchain.ps1')
$bin = Split-Path (Resolve-SrLutVivado)
$ip = Join-Path $root 'build/hdmi_x2_work/sr_lut_s40_dyn.gen/sources_1/bd/sr_lut_s40_dyn/ip'
$work = Join-Path $root 'build/hdmi_vtc_reset_test'
New-Item -ItemType Directory -Force -Path $work | Out-Null
Push-Location $work
try {
    & "$bin/xvhdl.bat" "$ip/sr_lut_s40_dyn_hdmi_reset_0/sim/sr_lut_s40_dyn_hdmi_reset_0.vhd" "$ip/sr_lut_s40_dyn_hdmi_vtc_0/sim/sr_lut_s40_dyn_hdmi_vtc_0.vhd"
    if ($LASTEXITCODE) { throw 'VTC IP compilation failed.' }
    & "$bin/xvlog.bat" --sv "$bin/../data/verilog/src/glbl.v" "$ip/sr_lut_s40_dyn_hdmi_video_out_0/sim/sr_lut_s40_dyn_hdmi_video_out_0.v" "$root/sim/pynq/tb_hdmi_vtc_reset.sv"
    if ($LASTEXITCODE) { throw 'VTC reset bench compilation failed.' }
    $top = if ($Scanout) { 'tb_hdmi_native_scanout' } else { 'tb_hdmi_vtc_reset' }
    & "$bin/xelab.bat" -L proc_sys_reset_v5_0_17 -L v_tc_v6_2_12 -L v_axi4s_vid_out_v4_0_21 -L xpm -L unisims_ver $top glbl -s hdmi_vtc_reset_test
    if ($LASTEXITCODE) { throw 'VTC reset elaboration failed.' }
    $result = & "$bin/xsim.bat" hdmi_vtc_reset_test -runall 2>&1
    $result | Write-Output
    if ($LASTEXITCODE -or -not ($result -match 'HDMI_VTC_RESET_PASS')) {
        throw 'VTC reset simulation failed.'
    }
    if ($Scanout -and -not ($result -match 'HDMI_NATIVE_SCANOUT_PASS')) {
        throw 'Native scanout simulation failed.'
    }
} finally { Pop-Location }
