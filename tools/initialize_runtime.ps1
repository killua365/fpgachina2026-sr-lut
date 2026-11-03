$ErrorActionPreference='Stop'
# Do not bind Python options as PowerShell common parameters (for example --out).
$Mode=$args[0]
if ($Mode -notin @('host','reference')) { throw 'Usage: python.cmd host|reference <Python arguments>' }
$PythonArguments=@($args | Select-Object -Skip 1)
$ResolveOnly=$PythonArguments.Count -eq 1 -and $PythonArguments[0] -eq '--resolve-only'
function Get-SrHash([string]$path) {
    $stream=[IO.File]::OpenRead($path)
    $algorithm=[Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace('-','').ToLowerInvariant() }
    finally { $algorithm.Dispose(); $stream.Dispose() }
}
$projectRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$settings=Get-Content -LiteralPath (Join-Path $projectRoot 'runtime/environments.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$environment=$settings.environments.$Mode
$archive=Join-Path $projectRoot $environment.path
$inventoryPath=Join-Path $projectRoot $environment.inventory
$inventory=Get-Content -LiteralPath $inventoryPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ((Get-SrHash $archive) -ne $environment.sha256) {
    throw "Offline $Mode runtime archive is damaged."
}
$cacheBase=if($env:SR_LUT_CACHE_DIR){$env:SR_LUT_CACHE_DIR}else{Join-Path $env:LOCALAPPDATA 'sr_lut_fpga\cache'}
$cacheBase=[IO.Path]::GetFullPath($cacheBase)
$runtimePath=Join-Path $cacheBase ($Mode+'-'+$environment.sha256.Substring(0,16))
New-Item -ItemType Directory -Path $cacheBase -Force | Out-Null
$mutexName='Local\sr_lut_runtime_'+$environment.sha256.Substring(0,24)
$mutex=New-Object Threading.Mutex($false,$mutexName)
$locked=$false
function Test-RuntimeFiles([string]$folder) {
    if (!(Test-Path -LiteralPath (Join-Path $folder '.complete.json'))) { return $false }
    foreach($entry in $inventory.files){
        $file=Join-Path $folder $entry.path
        if (!(Test-Path -LiteralPath $file -PathType Leaf)) { return $false }
        if ((Get-Item -LiteralPath $file).Length -ne $entry.bytes) { return $false }
        if ((Get-SrHash $file) -ne $entry.sha256) { return $false }
    }
    return $true
}
try {
    $locked=$mutex.WaitOne(300000)
    if (!$locked) { throw 'Timed out waiting for offline runtime initialization.' }
    if (!(Test-RuntimeFiles $runtimePath)) {
        Write-Host "Preparing verified offline Python runtime ($Mode)..."
        $staging=Join-Path $cacheBase ($Mode+'-'+[guid]::NewGuid().ToString('N')+'.partial')
        New-Item -ItemType Directory -Path $staging | Out-Null
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $zip=[IO.Compression.ZipFile]::OpenRead($archive)
        try {
            foreach($entry in $zip.Entries){
                $destination=[IO.Path]::GetFullPath((Join-Path $staging $entry.FullName))
                if (!$destination.StartsWith($staging+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) {
                    throw 'Unsafe runtime archive member.'
                }
                if ($entry.FullName.EndsWith('/')) { continue }
                [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination)) | Out-Null
                [IO.Compression.ZipFileExtensions]::ExtractToFile($entry,$destination,$false)
            }
        } finally { $zip.Dispose() }
        @{archive_sha256=$environment.sha256; inventory_sha256=(Get-SrHash $inventoryPath)} |
            ConvertTo-Json | Set-Content -LiteralPath (Join-Path $staging '.complete.json') -Encoding UTF8
        if (!(Test-RuntimeFiles $staging)) { throw "Extracted $Mode runtime did not match its inventory: $staging" }
        if (Test-Path -LiteralPath $runtimePath) {
            $resolved=[IO.Path]::GetFullPath($runtimePath)
            if (!$resolved.StartsWith($cacheBase+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase) -or
                (Split-Path $resolved -Leaf) -ne ($Mode+'-'+$environment.sha256.Substring(0,16))) { throw 'Unsafe runtime cache target.' }
            if ((Get-Item -LiteralPath $resolved).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Runtime cache cannot be a junction.' }
            Remove-Item -LiteralPath $resolved -Recurse -Force
        }
        Move-Item -LiteralPath $staging -Destination $runtimePath
    }
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
$python=Join-Path $runtimePath $environment.executable
if ($ResolveOnly) { Write-Output $python; return }
$env:PYTHONDONTWRITEBYTECODE='1'
& $python -X utf8 -E -B @PythonArguments
exit $LASTEXITCODE
