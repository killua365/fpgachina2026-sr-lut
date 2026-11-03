param([Parameter(Mandatory=$true)][string]$Destination)
$ErrorActionPreference='Stop'
$taskBusy=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -match '^(hw_server|cs_server|vivado|xsdb)\.exe$'})
$taskPorts=@(Get-PnpDevice -PresentOnly | Where-Object {$_.InstanceId -match 'A904CVLW|210276B3E2AD'} | Select-Object Status,Class,FriendlyName,InstanceId)
[ordered]@{time=(Get-Date -Format o);busy=@($taskBusy | Select-Object Name,ProcessId,CreationDate,CommandLine);devices=$taskPorts} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $Destination -Encoding utf8
if($taskBusy.Count){throw 'Existing hardware/tool owner; no device access'}
if(!($taskPorts | Where-Object {$_.Status -eq 'OK' -and $_.InstanceId -eq 'USB\VID_0403&PID_6010\210276B3E2AD'})){throw 'Recorded JTAG missing'}
