param([Parameter(Mandatory=$true)][string]$Destination,[switch]$Demo)
$ErrorActionPreference='Stop'
if(Test-Path -LiteralPath $Destination){throw 'Refuse reused capture directory'}
$taskDevices=@(Get-PnpDevice -Class Ports -PresentOnly | Where-Object {$_.InstanceId -like 'FTDIBUS\VID_0403+PID_6001+A904CVLW*' -and $_.Status -eq 'OK'})
if($taskDevices.Count -ne 1 -or $taskDevices[0].FriendlyName -notmatch '\((COM\d+)\)$'){throw 'Recorded Nexys UART missing or ambiguous'}
$taskPortName=$Matches[1]
$taskRoot=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$taskPython=(& (Join-Path $taskRoot 'tools/initialize_runtime.ps1') host --resolve-only | Select-Object -Last 1)
if(!(Test-Path -LiteralPath $taskPython)){throw 'Bundled capture Python missing'}
$taskModeOptions=@()
if($Demo){$taskModeOptions=@('--demo')}
& $taskPython -X utf8 -E -B (Join-Path $PSScriptRoot 'capture_ftdi_uart.py') --destination $Destination --port $taskPortName --pnp-id $taskDevices[0].InstanceId @taskModeOptions
if($LASTEXITCODE -ne 0){throw "D2XX capture failed: exit $LASTEXITCODE"}
