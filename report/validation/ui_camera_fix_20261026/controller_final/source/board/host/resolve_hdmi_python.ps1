# Resolve the verified, fixed-version offline environment before board access.
function Resolve-HDMIHostPython {
    param([string]$PreferredPath)
    $taskBootstrap=Join-Path $PSScriptRoot '..\..\tools\initialize_runtime.ps1'
    $taskPython=(& $taskBootstrap host --resolve-only | Select-Object -Last 1)
    if (-not $taskPython -or -not (Test-Path -LiteralPath $taskPython -PathType Leaf)) {
        throw 'Unable to initialize the offline host Python.'
    }
    if ($PreferredPath -and [IO.Path]::GetFullPath($PreferredPath) -ne [IO.Path]::GetFullPath($taskPython)) {
        throw 'Use the verified offline host environment.'
    }
    return $taskPython
}
