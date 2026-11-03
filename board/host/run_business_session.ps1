param([Parameter(Mandatory=$true)][string]$Launch, [switch]$Run, [string]$Session, [string]$PythonPath)
$ErrorActionPreference='Stop'
$root=(Resolve-Path -LiteralPath "$PSScriptRoot\..\..").Path
. "$PSScriptRoot\resolve_hdmi_python.ps1"
$python=Resolve-HDMIHostPython -PreferredPath $PythonPath
$launchPath=(Resolve-Path -LiteralPath $Launch).Path
$taskCheck=@'
import json,sys,hashlib
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'board/host'))
from business_session_source import CONTRACT
from business_session_remote import validate_launch
c=json.loads(CONTRACT.read_text('utf-8'))
validate_launch(json.loads(Path(sys.argv[1]).read_text('utf-8')),c)
if hashlib.sha256(Path(c['archive']).read_bytes()).hexdigest()!=c['package_sha256']: raise ValueError('Source ZIP changed')
'@
Push-Location $root
try {
    & $python -X utf8 -E -B -c $taskCheck $launchPath
    if($LASTEXITCODE){throw 'Local session check failed'}
} finally {Pop-Location}
if(!$Run){Write-Host 'LOCAL_PREFLIGHT_ONLY: no SSH or hardware access';return}
if(!$Session){throw 'The active Nexys session directory is required'}
$sessionPath=(Resolve-Path -LiteralPath $Session).Path
$taskActiveCheck=@'
import json,sys
from pathlib import Path
sys.path[:0]=[str(Path(sys.argv[3])/'board/pynq'),str(Path(sys.argv[3])/'board/host')]
from prepare_business_session import source_start_check
source_start_check(Path(sys.argv[2]),json.loads(Path(sys.argv[1]).read_text('utf-8')))
'@
& $python -X utf8 -E -B -c $taskActiveCheck $launchPath $sessionPath $root
if($LASTEXITCODE){throw 'Nexys-first session is not ready'}
$a=Get-Content -LiteralPath $launchPath -Raw -Encoding utf8 | ConvertFrom-Json
$record=[IO.File]::Open(($launchPath+'.started'),[IO.FileMode]::CreateNew,[IO.FileAccess]::Write)
$record.Dispose()
$key=Join-Path $env:USERPROFILE '.ssh\sr_lut_pynq_z2_ed25519'
$options=@('-i',$key,'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=8')
$remote=$a.remote_package; $result=$a.remote_results; $seconds=[int]$a.seconds
$durationOption=" --seconds $seconds"
if($a.end_mode -eq 'demo'){$durationOption=''}
$endOption=''
if($a.end_mode -in @('operator','demo')){$endOption=' --end-mode '+$a.end_mode}
$boardPython='/home/xilinx/sr_lut_hdmi_envs/hdmi_m1_20260909_r11/bin/python3'
& ssh.exe @options xilinx@192.168.2.99 "test ! -e $result && echo '$($a.package_sha256)  $remote.zip' | sha256sum -c - && $boardPython -B $remote/board/pynq/hdmi_business_app.py check --root $remote"
if($LASTEXITCODE){throw 'Remote preflight failed; launch remains reserved'}
& $python -X utf8 -E -B -c $taskActiveCheck $launchPath $sessionPath $root
if($LASTEXITCODE){throw 'Nexys session ended or failed during source preflight'}
$Host.UI.RawUI.WindowTitle='SR-LUT N5 business session - visible board sudo'
Write-Host 'Enter sudo here. Pixel checks run first, then the interactive workspace.'
if($a.end_mode -eq 'demo'){
    Write-Host 'Continuous demo: Ctrl+C exits and restores the original configuration.'
} elseif($a.end_mode -eq 'operator'){
    Write-Host 'Manual session: finish the requested actions, then report completion. No short countdown; 30-minute safety cap.'
} else {Write-Host 'Timed automatic session: the source ends automatically.'}
Write-Host 'Only press Ctrl+C here when requested; keep cables and power unchanged.'
& ssh.exe @options -tt xilinx@192.168.2.99 "sudo -H env XILINX_XRT=/usr $boardPython -B $remote/board/pynq/hdmi_business_app.py run --root $remote --destination $result --run-id $($a.run_id) $durationOption$endOption"
$code=$LASTEXITCODE
[ordered]@{run_id=$a.run_id;host_utc=(Get-Date).ToUniversalTime().ToString('o');ssh_exit=$code;remote_results=$result;physical_recovery_verified=$false} | ConvertTo-Json | Set-Content -LiteralPath ($launchPath+'.exit.json') -Encoding utf8
Write-Host 'Source terminal ended; worker, DMA and service recovery must still be verified.'
exit $code
