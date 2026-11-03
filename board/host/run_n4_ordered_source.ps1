param([Parameter(Mandatory=$true)][string]$Launch, [switch]$Run, [string]$PythonPath)
$ErrorActionPreference='Stop'
$root=(Resolve-Path -LiteralPath "$PSScriptRoot\..\..").Path
. "$PSScriptRoot\resolve_hdmi_python.ps1"
$python=Resolve-HDMIHostPython -PreferredPath $PythonPath
$launchPath=(Resolve-Path -LiteralPath $Launch).Path
$taskCheck=@'
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'board/host'))
from bridge_ordered_remote import validate_launch
validate_launch(json.loads(Path(sys.argv[1]).read_text('utf-8')))
'@
Push-Location $root
try {
    & $python -X utf8 -E -B -c $taskCheck $launchPath
    if($LASTEXITCODE){throw 'Invalid launch'}
    & $python -X utf8 -E -B "$PSScriptRoot\hdmi_hardware_validation.py" --package "$root\board\releases\2026-10-08\n4_fixed_source_r1.zip"
    if($LASTEXITCODE){throw 'Local source package check failed'}
} finally {Pop-Location}
if(!$Run){Write-Host 'LOCAL_PREFLIGHT_ONLY: no SSH or hardware access';return}
$a=Get-Content -LiteralPath $launchPath -Raw -Encoding utf8 | ConvertFrom-Json
$record=[IO.File]::Open(($launchPath+'.started'),[IO.FileMode]::CreateNew,[IO.FileAccess]::Write)
$record.Dispose()
$key=Join-Path $env:USERPROFILE '.ssh\sr_lut_pynq_z2_ed25519'
$options=@('-i',$key,'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=8')
$remote=$a.remote_package; $result=$a.remote_results; $scale=[int]$a.scale; $duration=[int]$a.pattern_seconds_total
$boardPython='/home/xilinx/sr_lut_hdmi_envs/hdmi_m1_20260909_r11/bin/python3'
& ssh.exe @options xilinx@192.168.2.99 "test ! -e $result && echo '$($a.package_sha256)  $remote.zip' | sha256sum -c - && $boardPython -B $remote/board/host/n4_fixed_source.py check --root $remote --scale $scale --duration $duration"
if($LASTEXITCODE){throw 'Remote source identity or new-directory check failed; launch remains reserved'}
$Host.UI.RawUI.WindowTitle="SR-LUT ordered source x$scale - visible board sudo"
Write-Host 'Enter sudo only here. Keep this terminal open. If requested, press Ctrl+C here once to stop its owned worker.'
Write-Host 'Automatic cancellation uses sudo -n and may be unavailable; the bounded controller records that failure.'
& ssh.exe @options -tt xilinx@192.168.2.99 "sudo -H env XILINX_XRT=/usr $boardPython -B $remote/board/host/n4_fixed_source.py run --root $remote --destination $result --scale $scale --duration $duration"
$code=$LASTEXITCODE
[ordered]@{run_id=$a.run_id;host_utc=(Get-Date).ToUniversalTime().ToString('o');ssh_exit=$code;remote_results=$result;physical_recovery_verified=$false} | ConvertTo-Json | Set-Content -LiteralPath ($launchPath+'.exit.json') -Encoding utf8
Write-Host 'Source exited; final worker/DMA/service evidence still requires verification.'
exit $code
