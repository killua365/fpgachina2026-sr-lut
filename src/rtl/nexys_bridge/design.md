# Nexys DisplayPort 链路模块

本目录保存 r15 所用 DP 发射链路及其诊断、复位和协议控制模块。当前完整桥接顶层位于 `../nexys_hdmi/bridge_board_top_continuous.sv`；构建入口是根目录 `tools/rebuild.ps1 -Target NexysRoute`。本目录单独的参考图案顶层供来源审查和诊断使用，不是日常演示入口。

- AUX/EDID/DPCD：`aux_channel.vhd`、`aux_interface.vhd`、`aux_io.vhd`、`dp_aux_messages.vhd`、`dp_register_decode.vhd`、`edid_decode.vhd`、`edid_observer.vhd`。
- 连接与训练：`hotplug_decode.vhd`、`channel_managemnt.vhd`、`link_signal_mgmt.vhd`。
- GTP/复位：`transceiver.vhd`、`gtx_tx_reset_controller.vhd`、`gtp_tx_drive.vhd`、`gtp_ref_monitor.vhd`、`gtp_health_monitor.vhd`。
- 主流及扰码协调：`main_stream_processing.vhd`、`idle_pattern_inserter.vhd`、`enhanced_framing.vhd`、`epoch_scrambler_reset.vhd`。
- 诊断复用、串口、误码窗口与流捕获模块供硬件状态检查使用；具体参与构建的文件由 `build/scripts/nexys/build_n5_business_board.tcl` 显式选择。

未修改的上游内容及 MIT 许可证封装在 `src/third_party/fpga_displayport.zip`；本地派生文件保留来源头。`channel_managemnt` 保留既有实体拼写，避免改变接口。过期 PMA 和旧时间戳实验文件已移出正式交付。

已确认的演示功能与完整阶段验收不是同一结论。当前配置、离线证据及未完成项统一见根目录 `report/validation/status.md`，不以本目录的模块测试推断换屏、真实拔插或长期可靠性已经通过。
