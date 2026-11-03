# Resolve only the installed 2026.1 simulator. Never fall back to another release.
function Resolve-SrLutSimulator {
    param([string]$SimulatorBin = 'D:/vivado2026.1/2026.1/Vivado/bin')
    $bin = (Resolve-Path -LiteralPath $SimulatorBin -ErrorAction Stop).Path
    foreach ($tool in @('xvhdl.bat', 'xvlog.bat', 'xelab.bat', 'xsim.bat')) {
        if (-not (Test-Path -LiteralPath (Join-Path $bin $tool) -PathType Leaf)) {
            throw "Required Vivado 2026.1 simulator tool missing: $tool"
        }
    }
    $versionFile = Join-Path $bin '../data/version.bat'
    $versionText = Get-Content -LiteralPath $versionFile -Raw -ErrorAction Stop
    if ($versionText -notmatch '(?im)^\s*SET\s+XILINX_VERSION_DEFAULT=2026\.1\s*$') {
        throw 'This project requires the Vivado 2026.1 simulator; other releases are refused.'
    }
    return $bin
}
