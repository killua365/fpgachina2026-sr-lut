# 校验脚本使用说明

适用：任何有明确参考数据、产物清单及实现报告的 FPGA/PYNQ 工程。题目外可验证图像、传感器、音频、数值加速项目。来源为本队黄金像素、哈希、时序和完整新显示帧的验证经验。

入口：`python run_checks.py --config config.json --out out/check`。配置内的文件路径相对于配置文件所在目录，禁止越出该目录；只有明确指定的检查才执行。回归见 `tests/test_skill.py`，已验证效果见 `evidence/qualification.json`。

## 自己的配置

下例是配置格式说明，引用的文件须由使用者提供：

```json
{
  "files": [{"path": "accelerator.bit", "sha256": "actual_sha256_here"}],
  "hwh": {"path": "accelerator.hwh", "required_ips": ["axi_dma_0"], "dma_ip": "axi_dma_0"},
  "golden": {"actual": "actual.npy", "expected": "expected.npy", "atol": 0.0, "rtol": 0.0},
  "timing": "timing.rpt",
  "utilization": {"path": "utilization.rpt", "instance": "top", "limits": {"lut": 20000}},
  "throughput": {"events": "events.json", "start_s": 0.0, "end_s": 10.0,
                 "stage": "display_complete", "epoch": 1, "minimum": 20.0}
}
```

`minimum` 是使用者的目标，可省略；20 仅为格式示例，不是通用 PYNQ 合格线。省略资源 `limits` 时只解析记录数量，不评价资源使用是否合理。

事件文件示例：

```json
[
  {"time_s": 0.1, "id": 1, "stage": "display_complete", "epoch": 1, "complete": true},
  {"time_s": 0.2, "id": 1, "stage": "display_complete", "epoch": 1, "complete": true},
  {"time_s": 0.3, "id": 2, "stage": "display_complete", "epoch": 1, "complete": true}
]
```

重复的 `(epoch,id)` 只计一次，窗口为 `[start_s,end_s)`；窗口前已经完成的身份在窗口内重复出现也不计新完成，因此应提供包含预热期的完整事件序列。所有事件使用同一个单调时钟并按时间排序。统计指定阶段，不能把 `submit` 与 `display_complete` 混在一起。只有明确窗口，才计算完成数量/窗口时长，不用首末事件间隔冒充完整测量窗口。日志采集完整性需由上游保证，缺事件不能由此脚本自动修复。

## 每项的通过标准与失效条件

| 检查 | 通过标准 | 不能证明/失效条件 |
|---|---|---|
| 文件 | 路径存在、清单非空、SHA-256匹配 | 只能证明和声明清单一致，不能从任意BIT内容反推算法或器件适配 |
| HWH | required_ips存在；指定DMA为AXI DMA双向simple模式 | 不读取开发板；元数据正确不等于板端链路正常 |
| 黄金 | shape与dtype完全匹配；整数精确；浮点显式容差 | 空数组、NaN/Inf、类型变化、complex不支持 |
| 时序 | 完整汇总中setup/hold/pulse无负裕量及失败端点 | 仅汇总已约束路径，不证明约束完备、CDC正确或板级信号质量 |
| 资源 | 明确实例行可解析；存在预算则不超预算 | 当前解析Vivado层次表，未知格式报错；不把资源少直接判成设计优秀 |
| 吞吐 | 明确完成事件、代次、固定时窗；有目标则达到 | 模拟数据只能验证计数逻辑；没有硬件完成事件不能宣称真实显示FPS |

原项目特有 HDMI 时钟、链路和帧标记检查没有伪装为任意板卡通用检查。需要这类功能时，应根据自己的显示链路定义额外检查。
