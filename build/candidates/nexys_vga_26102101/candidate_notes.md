# Nexys MiniDP-to-VGA configuration 26102101

This configuration supports the tested MiniDP-to-VGA adapter while retaining the r15 PYNQ application and the default DP configuration separately. Run `demo_vga.cmd` from the project root; `demo.cmd` selects the standard DP path.

## Hardware change

Only the copied `aux_channel.vhd` differs from the standard RTL:

- Accept 0x00, 0x01 or 0x02 on the completed DPCD power pre-read.
- Require an acknowledged D0 write and the existing settling interval.
- Preserve normal 0x01 post-read verification.
- When both reads are zero and D0 was acknowledged, attempt real link training and mark diagnostic bit 36. The D0-verified bit 35 remains clear.
- Retain failure handling for invalid values, failed writes, malformed replies, HPD and link-training errors.

FPGA USR_ACCESS and UART identity are both `26102101`. `artifacts/` contains the final incremental implementation; `simulation/` contains nine power-handling cases. The default bridge reference remains `26101201`.

## Build and verification

Complete commands, including the initial synthesis, incremental route and sealing order, are in the [reproduction guide](../../../docs/reproduction.md). Rebuild in a complete project copy because preparation and sealing create a new candidate inventory.

The incremental implementation uses the candidate synthesized checkpoint and `build/artifacts/nexys/route/routed.dcp` as its physical reference. `source.json` records the original hardware build. Later host-path maintenance is recorded separately in `path_validation.json`; it does not change the bitstream or replace the original implementation evidence.

```powershell
.\tools\python.cmd host build/candidates/nexys_vga_26102101/test_host.py
.\tools\python.cmd host build/candidates/nexys_vga_26102101/test_demo.py
.\tools\python.cmd host build/candidates/nexys_vga_26102101/test_paths.py
.\demo_vga.cmd --check
```

## Runtime and evidence

Normal VGA demonstration has no total-runtime deadline. Loading, shutdown, restoration and heartbeat lease remain bounded. UART logs rotate at 16 MiB with four copies; the telemetry tail is bounded. Ctrl+C requests source/DMA shutdown, service recovery and restoration of the original Nexys configuration. FPGA loading uses SRAM and does not write configuration Flash.

The direct `test_candidate.py` entry without `--demo` remains a bounded hardware test. Its `finish.request` must identify the run and record the operator's actual display observation. Digital link readiness alone is not a physical-display acceptance.

The archived [physical test evidence](../../../report/validation/vga_candidate_20261021/) binds the final bitstream to successful UI observation and restoration. Logs from new runs are generated under `board/analysis/`.
