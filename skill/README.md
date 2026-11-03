# PYNQ 流式加速开发与验证 Skill

本技能包从本队 SR-LUT 超分项目的 PYNQ-Z2 开发、验证和性能优化过程中提炼，面向采用 **PYNQ + Overlay + AXI DMA** 的其他队伍。按《2026 FPGA 赛道选题指南（AMD）》3.3.2、3.3.5.2 和 3.3.5.3 组织，日期为 2026-10-20。

目标是让陌生队伍能够取走此目录，替换算法和板卡配置，用于图像滤波、二值分割、音频采样、传感器批处理等题目。**通用的是调用模式、接口契约、检查工具与诊断流程；BIT/HWH 不能跨器件通用。**

## 先看什么

| 赛题形式 | 入口及实际内容 | 已验证效果与边界 |
|---|---|---|
| 提示词工作流 | [四套提示词及操作步骤](prompts/usage.md) | 由本项目经验提炼；本轮示例与纠错记录见 [协作记录](evidence/collaboration_record.md)，不宣称跨模型统计稳定性 |
| 案例模板 | [PYNQ 调用与迁移说明](templates/usage.md)、真实 PYNQ 后端、模拟后端、接口及报告模板 | 新模板通过离线/模拟设备测试；原项目实板经验有历史证据，新模板未重新上板 |
| 校验脚本 | [脚本输入与输出](checks/usage.md)、`run_checks.py` | 两个独立外部场景、故意损坏数据、原始工程报告及异常路径均有可重跑入口 |
| 踩坑清单 | [结构化问题库](troubleshooting/usage.md) | 每项标注触发、诊断、修复、证据及不能推广的边界 |

外部场景的逐项迁移要求见 [适用场景与迁移矩阵](migration.md)。赛题逐条对照见 [要求对照](requirements_mapping.md)。

## 普通电脑：五分钟离线使用

先把整个 `skill/` 复制到任意目录。以下命令在该目录执行，Windows PowerShell/Linux shell 均可。需要 Python 3.10 或以上。

```text
python -m pip install -r requirements.txt
python run_checks.py --config examples/image_threshold.json --out out/image_threshold
python run_checks.py --config examples/sensor_gain.json --out out/sensor_gain
python run_checks.py --config evidence/project_reports.json --out out/project_reports
python tests/run_tests.py --out out/regression.json
python run_checks.py --config manifest.json --out out/package_integrity
```

本项目电脑也可用 `..\tools\python.cmd host` 代替 `python`，但独立包不依赖该相对位置。

- 图像示例：32×48 灰度输入，阈值 128，输出 0/255；覆盖阈值两侧及等于阈值。
- 传感器示例：9 批×4 通道 `int16` 采样，增益 3，结果饱和到 −32768…32767。
- 两者使用标量黄金参考与独立向量实现；模拟内存区分 CPU/设备内容，实际执行分配、同步、接收、启动、发送、完成与释放流程。
- 输出 `input.npy`、`expected.npy`、`actual.npy`、`result.json`、`report.md`。成功返回码为 0，失败为 1；没有申请的检查列在 `not_requested`，不是通过项。
- 模拟后端的时间只表示主机模拟执行时间，不能作为 FPGA 吞吐。

本次实际执行记录在 [验证汇总](evidence/qualification.json)，便于阅读的版本见 [验证结果表](evidence/validation_summary.md)。它区分新包测试、历史工程证据及未做的跨板卡实测。`manifest.json` 由交付打包步骤生成；修改包内内容后，原清单检查会失败，不能用重算清单代替功能验证。

## 开发板：PYNQ 是必需依赖

**PYNQ 是板端硬件运行的必需依赖；离线校验不需要安装 PYNQ。** 在目标板安装匹配该板系统镜像的 PYNQ，配置同次构建的 BIT/HWH、DMA 和计算 IP，并按 [板端模板说明](templates/usage.md) 执行。不要在普通 Windows 上安装 PYNQ 来替代板端系统。

`templates/pynq_backend.py` 实际使用 `pynq.Overlay`、`pynq.allocate`、AXI DMA `transfer` 与 MMIO 状态；调用不含本队 LUT、480×270 假设、设备 IP 地址或固定 SR-LUT 寄存器。

支持范围：完整 Overlay、双向 AXI DMA simple mode、CPU 可访问的 PynqBuffer、单线程独占会话、有限批次流。VDMA、SG/cyclic DMA、PCIe/Alveo、部分重配置、多个并发调用者及板卡特定 ACP 零拷贝路径不属于此模板的已支持范围。

软件无法保证故障 AXI 总线上的一次 MMIO 访问能返回，也无法保证厂商驱动构造/下载阶段一定有界。模板的超时针对能正常读写状态寄存器的传输阶段；严重总线故障需要系统级恢复。

## 已验证效果如何看

1. 本项目历史 r5：两个 300 秒窗口，完整新显示帧率分别为 21.7567 和 22.4233 fps；该结论绑定当时包和测量点。
2. 本项目历史 r11：修复 ARM 背景逐通道填充，短测 22.6143 fps、用户确认鼠标恢复；这不是 r11 的长期 20 fps 验收。
3. 新技能包：离线独立场景、数据损坏检出、模拟 DMA 生命周期、原报告解析和脱离项目目录运行；详见机器生成记录。
4. **没有第二块 PYNQ 板的实测，也没有给两个新算子生成 BIT/HWH。** 板端模板需要目标队伍完成自己的实板资格验证。

历史来源与 SHA-256 见 [证据索引](evidence/source_index.json)；提炼过程见 [过程说明](evidence/derivation.md)。不得把历史版本性能替换成当前演示版本或其他板卡性能。

## 目录与复用边界

`prompts/` 工作流；`templates/` PYNQ 模板；`checks/` 校验函数；`troubleshooting/` 故障卡片；`examples/` 独立数据算例；`tests/` 回归；`evidence/` 本轮记录和最小历史证据。

除赛题明确指定的 `README.md` 外，新文件使用英文小写、数字、下划线及正常扩展名。独立包可放在英文路径运行；主工程的路径不作为本包依赖。

本次没有发布代码、改变主工程许可证、附带系统镜像或模型素材；来源及授权边界见 [来源说明](provenance.md)。也不改变当前演示入口或启动过程。
