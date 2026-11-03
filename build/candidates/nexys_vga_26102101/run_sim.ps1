param([string]$RunName='power_sim_r3', [string]$VivadoBin=$env:SR_LUT_VIVADO_BIN)
$ErrorActionPreference='Stop'
$taskHere=$PSScriptRoot
$taskRoot=[IO.Path]::GetFullPath((Join-Path $taskHere '..\..\..'))
$taskOut=Join-Path (Join-Path $taskRoot 'build\reproduction_work\nexys_vga_26102101') $RunName
if(Test-Path -LiteralPath $taskOut){throw 'Simulation output already exists'}
New-Item -ItemType Directory -Path $taskOut | Out-Null
$taskBin=$VivadoBin
if (!$taskBin) { $taskBin='D:\vivado2026.1\2026.1\Vivado\bin' }
Push-Location -LiteralPath $taskOut
try {
  $taskWrappers=for($taskCase=0;$taskCase -le 8;$taskCase++) {
    "entity power_case_$taskCase is end entity; architecture test of power_case_$taskCase is begin dut: entity work.tb_vga_power generic map(CASE_ID => $taskCase); end architecture;"
  }
  $taskWrappers | Set-Content -LiteralPath 'wrappers.vhd' -Encoding ascii
  & "$taskBin\xvhdl.bat" --2008 "$taskRoot\src\rtl\nexys_bridge\dp_aux_messages.vhd" "$taskHere\aux_channel.vhd" "$taskHere\tb_vga_power.vhd" 'wrappers.vhd' *> compile.log
  if($LASTEXITCODE){throw 'Candidate VHDL compile failed'}
  for($taskCase=0;$taskCase -le 8;$taskCase++) {
    & "$taskBin\xelab.bat" "power_case_$taskCase" -s "power_case_$taskCase" --mt 2 --debug off *> "elaborate_$taskCase.log"
    if($LASTEXITCODE){throw "Candidate elaboration failed, case $taskCase"}
    & "$taskBin\xsim.bat" "power_case_$taskCase" -runall -log "case_$taskCase.log" *> "console_$taskCase.log"
    if($LASTEXITCODE){throw "Candidate simulation failed, case $taskCase"}
    if(!(Select-String -LiteralPath "case_$taskCase.log" -Pattern "VGA_POWER_CASE_PASS $taskCase" -Quiet)){throw "Missing successful assertion marker, case $taskCase"}
    Write-Output "VGA_POWER_CASE_PASS $taskCase"
  }
} finally {Pop-Location}
