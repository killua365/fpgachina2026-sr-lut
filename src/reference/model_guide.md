# Python 整数参考模型

本目录定义 RTL 必须遵守的整数数值语义，并提供模块级单元测试。该模型用于生成黄金结果和验证硬件正确性，不纳入 FPGA 性能统计。

| 实现文件 | 职责 |
|---|---|
| `model_s_input.py` | 检查 RGB `uint8` 输入并执行 reflect 边界扩展 |
| `model_s_neighborhoods.py` | 提取四个方向的 2x2 邻域 |
| `model_s_quantizer.py` | 将像素拆分为 LUT 基地址分量和余数 |
| `model_s_simplex.py` | 完成 4D simplex 分区并产生顶点掩码及权重 |
| `model_s_address.py` | 将四维 LUT 坐标转换为一维 C-order 地址 |
| `model_s_interpolator.py` | 读取 LUT 顶点并计算带符号整数插值分子 |
| `model_s_fusion.py` | 完成方向恢复、累加、ties-to-even 舍入和裁剪 |
| `model_s_output.py` | 将每个低分辨率像素对应的小块重排为整幅输出图像 |
| `model_s_evaluation.py` | 运行数据集评估并计算 PSNR-Y、SSIM-Y、PSNR-RGB、SSIM-RGB 和文件哈希 |

`sim/software/reference/test_model_s_*.py` 与相应实现文件对应；`test_model_s_hls_vectors.py` 负责兼容性向量交叉核验。修改 RTL 前后均应通过 `tools/python.cmd reference tools/run_tests.py --suite reference --out <新输出目录>` 执行参考模型测试。除非算法规范发生正式变更，不得通过修改参考结果来适配错误的 RTL 输出。
