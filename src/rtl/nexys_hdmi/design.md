# Nexys HDMI → DDR → DisplayPort 桥接

r15 使用 `bridge_board_top_continuous.sv` 顶层，接收 PYNQ 的 1920×1080p30 RGB/DVI 兼容视频，通过三块 RGBX8888 DDR 帧缓存，送往 1080p60 DisplayPort 扫描。DP 重复扫描不算新的超分帧。

## 模块职责

- `hdmi_receiver.vhd` 封装 Digilent 接收器；`n4_1080p30.data` 为固定输入模式的 EDID。
- `hdmi_geometry.sv` 检查光栅尺寸；`hdmi_token_cdc.sv` 跨时钟传送输入并处理溢出；`hdmi_frame_input.sv` 完成已启动的写入范围，损坏帧不进入可显示队列。
- `bridge_frame_owner.sv` 管理 FREE/READY/RETIRING 和未完成引用，防止重用仍在读写的缓存；`bridge_pipeline_control.sv` 协调启动、清空和异常恢复。
- `sr_identity_marker_rx.sv`、`sr_identity_tracker.sv` 跟踪来源帧身份；`video_crc_check.sv` 与 `frame_crc32.vh` 检查传输/存储一致性。
- `atomic_status_cdc.sv`、`bridge_telemetry.sv`、`bridge_uart.sv` 将状态原子传送并编码为串口证据；`bridge_core.sv` 集成上述控制。
- 当前演示仅使用 continuous 顶层；未被当前构建或仿真引用的旧实验顶层已移出正式目录，来源仍可由 Git 历史追溯。

## 第三方派生与构建

`twi_slavectl.vhd` 和 `eeprom_8b.vhd` 保留 Digilent BSD-3-Clause 头和实体接口，并明确实现计数回绕。上游原件及许可位于 `src/third_party/digilent_hdmi_rx.zip`。HDMI 引脚约束位于 `build/scripts/nexys/bridge_hdmi_pins.xdc`。

从根目录执行 `tools/rebuild.ps1 -Target NexysSimulation`，使用 Vivado 2026.1 的真实 VDMA 模型与独立参考完成核心仿真；`NexysRoute` 完成综合和布局布线，`NexysBit` 从路由 DCP 生成配置。新生成结果不自动替换 `build/artifacts/nexys` 中的当前冻结配置。

历史 N4 和当前 N5 的来源、硬件身份与验收口径各自独立。最终状态见 `report/validation/status.md`；软件模拟、CRC 正确或 Vivado 成功退出均不能单独证明实屏完整验收通过。
