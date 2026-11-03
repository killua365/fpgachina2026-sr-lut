# 验证结果与证据索引

当前发布合同为 r17，板端包为 `sr_lut_demo_r17_repack.zip`，具体版本与哈希以 `board/release/source.json` 为准。DP 与 VGA 配置独立管理。以下保留既有功能、硬件和性能记录；2026-11-04 仓库整理的离线控制器、Skill 与启动资格检查见 `repository_20261104/`，不替代历史板端实测。

## 提交包检查（2026-10-23）

最终文件清单与逐文件 SHA-256 位于 `board/release/file_manifest.json`，验证结果为 [verify_result.json](verify_result.json)。本次软件、路径和离线环境检查汇总于 [submission_20261023](submission_20261023/)。

整理保留了算法源码、LUT、黄金数据、BIT/HWH、发布包和恢复依赖。VGA 脚本改为相对项目目录定位资源，并支持 `SR_LUT_VIVADO_BIN`。这些修改只涉及主机路径，不构成新的 FPGA 实现或实板验收。

## 已有软件与实现结果（2026-10-20）

| 项目 | 结果与证据 |
|---|---|
| 离线环境 | 首次生成、复用、中断恢复与损坏修复通过；[记录](reorganization_checks/offline_runtime.json) |
| 参考模型 | 58 项通过；[记录](reorganization_checks/reference_result.json) |
| 黄金数据、LUT | 25 项、5 项通过；[黄金数据](reorganization_checks/golden_result.json)、[LUT](reorganization_checks/lut_result.json) |
| PS 软件主机测试 | 243 项通过，5 项 ARM/Linux 专属跳过，无失败；[记录](reorganization_checks/ps_result.json) |
| 控制器、启动器 | 190 项、32 项通过；[控制器](reorganization_20261020/result.json)、[启动器](demo_launcher_20261020/result.json) |
| PYNQ x2 重建 | 建立/保持裕量 0.194/0.028 ns；[记录](../../build/reports/rebuild_20261020/pynq_x2/result.json) |
| PYNQ x4 重建 | 建立/保持裕量 0.144/0.014 ns；[记录](../../build/reports/rebuild_20261020/pynq_x4/result.json) |
| Nexys 重建 | 建立/保持裕量 0.106/0.039 ns；[路由](../../build/reports/rebuild_20261020/nexys/result.json)、[BIT](../../build/reports/rebuild_20261020/nexys_bit/result.json) |
| 硬件配置比较 | 三套重建 BIT 从同步字开始与冻结产物逐字节一致；[记录](../../build/reports/rebuild_20261020/bitstream_comparison.json) |
| PYNQ RTL | 16 组通过；[记录](../../build/reports/rebuild_20261020/pynq_rtl/result.json) |
| Nexys VDMA 仿真 | 5 次完整扫描，含 3 个新帧和 2 次重复扫描；[记录](../../build/reports/rebuild_20261020/nexys_sim/result.json) |
| Skill 独立验证 | 两套环境通过；[主机](../../skill/evidence/reorganized_host/qualification.json)、[参考](../../skill/evidence/reorganized_reference/qualification.json) |

## 实板与性能

- r15 图片、视频、摄像头的界面与 Wipe 交互已由操作者确认；历史原始记录在 [historical_validation.zip](historical_validation.zip)。
- VGA 配置 `26102101` 的实现、仿真和实屏确认保存在 [vga_candidate_20261021](vga_candidate_20261021/)；位流及实现报告在 `build/candidates/nexys_vga_26102101/`。正常 DP 配置仍为 `26101201`。
- r5 两段各 300 秒的完整新显示帧率为 21.7567 / 22.4233 fps；[测量说明](../performance/camera_20fps_2026-10-18.md)。复算入口为 `tools/recompute_camera.py`。
- r11 的 22.6143 fps 为短测结果，不作为长期性能成绩；不同软件版本、布局、显示配置的结果不能混用。

尚无覆盖全部模式的统一长期性能、插拔可靠性与无主机启动验收结论。本次目录整理没有重新执行实板测试。历史记录中的状态仅适用于对应会话，不用于判断当前硬件是否正在运行或已恢复。

`reorganization_20261020` 和 `demo_launcher_20261020` 中的冻结源码与资格记录参与启动校验，需要随工程保留。
