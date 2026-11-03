function Resolve-SrLutVivado {
    param([string]$Executable = "")
    if ([string]::IsNullOrWhiteSpace($Executable)) {
        $Executable = $env:SR_LUT_VIVADO
    }
    if ([string]::IsNullOrWhiteSpace($Executable)) {
        $Executable = "D:\vivado2026.1\2026.1\Vivado\bin\vivado.bat"
    }
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        throw "Vivado is missing: $Executable. Set SR_LUT_VIVADO or pass -Vivado explicitly."
    }
    $Executable = (Resolve-Path -LiteralPath $Executable).Path
    . (Join-Path $PSScriptRoot 'simulator.ps1')
    $null = Resolve-SrLutSimulator (Split-Path -Parent $Executable)
    return $Executable
}
