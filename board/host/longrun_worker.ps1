param([Parameter(Mandatory=$true)][string]$Spec)
$ErrorActionPreference='Stop'
$taskSpec=Get-Content -LiteralPath $Spec -Raw | ConvertFrom-Json
$taskWatch=[Diagnostics.Stopwatch]::StartNew()
while(!(Test-Path -LiteralPath $taskSpec.go)) {
    if($taskWatch.Elapsed.TotalSeconds -gt 30){throw 'Supervisor did not assign job and release worker'}
    Start-Sleep -Milliseconds 50
}
Set-Location -LiteralPath $taskSpec.cwd
& $taskSpec.executable @($taskSpec.arguments)
if($null -ne $LASTEXITCODE){exit $LASTEXITCODE}
