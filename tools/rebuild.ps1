param(
 [ValidateSet('CheckIP','NexysRoute','NexysSimulation','NexysBit','PynqX2','PynqX4')][string]$Target='CheckIP',
 [string]$Vivado='',
 [string]$WorkDirectory='',
 [string]$Checkpoint=''
)
$ErrorActionPreference='Stop'
$taskRoot=(Resolve-Path -LiteralPath "$PSScriptRoot\..").Path
. "$taskRoot\build\scripts\pynq\toolchain.ps1"
$taskVivado=Resolve-SrLutVivado $Vivado
if(!$WorkDirectory){$WorkDirectory=Join-Path $env:TEMP ('srl_'+[guid]::NewGuid().ToString('N').Substring(0,12))}
$taskWork=[IO.Path]::GetFullPath($WorkDirectory)
if(Test-Path -LiteralPath $taskWork){throw 'Choose a new empty output path; existing output is never overwritten.'}
if($taskWork -match '[^\x00-\x7F]'){throw 'Vivado synthesis requires an ASCII-only work directory; use the supplied project sources.'}
if ($Target -in 'PynqX2','PynqX4') {
 $taskScale=if($Target -eq 'PynqX2'){2}else{4}
 & "$taskRoot\tools\python.cmd" host "$PSScriptRoot\prepare_reproduction.py" --platform pynq --out $taskWork
 if($LASTEXITCODE){throw 'PYNQ source snapshot failed'}
 Push-Location -LiteralPath "$taskWork\source"
 try {
  & "$taskWork\source\build\scripts\pynq\build_hdmi_1080p30.ps1" -Scale $taskScale -Step build -Vivado $taskVivado
  if($LASTEXITCODE){throw "PYNQ block-design build failed with exit code $LASTEXITCODE"}
  & "$taskRoot\tools\python.cmd" host "$PSScriptRoot\check_pynq_reproduction.py" --snapshot "$taskWork\source" --scale $taskScale --out "$taskWork\hardware_configuration.json"
  if($LASTEXITCODE){throw 'PYNQ hardware configuration differs from the frozen current design'}
  & "$taskWork\source\build\scripts\pynq\build_hdmi_1080p30.ps1" -Scale $taskScale -Step implement -Vivado $taskVivado
  if($LASTEXITCODE){throw "PYNQ implementation failed with exit code $LASTEXITCODE"}
 } finally {Pop-Location}
 Write-Output "REBUILD_OUTPUT=$taskWork; byte-identical source snapshot; no hardware accessed."
 return
}
if($Target -eq 'NexysBit'){
 if(!$Checkpoint){throw '-Checkpoint must name the routed.dcp produced by NexysRoute'}
 $taskDcp=(Resolve-Path -LiteralPath $Checkpoint).Path
 New-Item -ItemType Directory -Path $taskWork | Out-Null
 Push-Location -LiteralPath $taskWork
 try {
  & $taskVivado -mode batch -notrace -source "$PSScriptRoot\nexys_write_bit.tcl" -log "$taskWork\vivado.log" -journal "$taskWork\vivado.jou" -tclargs $taskDcp "$taskWork\bit"
  if($LASTEXITCODE){throw "Nexys BIT generation failed with exit code $LASTEXITCODE"}
 } finally {Pop-Location}
 Write-Output "REBUILD_OUTPUT=$taskWork; qualification required before hardware use."
 return
}
& "$taskRoot\tools\python.cmd" host "$PSScriptRoot\prepare_reproduction.py" --out $taskWork
if($LASTEXITCODE){throw 'IP preparation failed'}
$taskArgs=@()
switch($Target){
 'CheckIP' {$taskScript="$PSScriptRoot\check_reproduction_ip.tcl"; $taskArgs=@("$taskWork\probe","$taskWork\ips")}
 'NexysRoute' {$taskScript="$taskWork\nexys_route.tcl"; $taskArgs=@("$taskWork\route",'26101201',"$taskWork\ips")}
 'NexysSimulation' {$taskScript="$taskWork\nexys_sim.tcl"; $taskArgs=@("$taskWork\sim","$taskWork\ips")}
}
Push-Location -LiteralPath $taskWork
try {
 & $taskVivado -mode batch -notrace -source $taskScript -log "$taskWork\vivado.log" -journal "$taskWork\vivado.jou" -tclargs @taskArgs
 if($LASTEXITCODE){throw "Vivado failed with exit code $LASTEXITCODE; preserve logs at $taskWork"}
} finally {Pop-Location}
Write-Output "REBUILD_OUTPUT=$taskWork; no hardware accessed or current artifacts replaced."
