# A single append-only writer, shared with passive readers for its lifetime.
# Never truncate, replace or reopen the heartbeat while capture is active.
function New-HeartbeatWriter {
    param([Parameter(Mandatory=$true)][string]$Path)
    $taskStream=[IO.File]::Open($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::ReadWrite)
    try {
        $taskWriter=[IO.StreamWriter]::new($taskStream,[Text.UTF8Encoding]::new($false))
        $taskWriter.AutoFlush=$true
        return $taskWriter
    } catch { $taskStream.Dispose();throw }
}
function Write-Heartbeat {
    param([Parameter(Mandatory=$true)][IO.StreamWriter]$Writer,
          [Parameter(Mandatory=$true)][long]$Value)
    $Writer.WriteLine($Value.ToString([Globalization.CultureInfo]::InvariantCulture))
}
