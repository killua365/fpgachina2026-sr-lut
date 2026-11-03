# LUT 数据与 BRAM 初始化文件

本目录将训练得到的 NumPy LUT 转换为 Vivado 可综合 ROM 初始化数据，并保存转换结果的完整性记录。

## 目录内容

- `original_npy/model_s_x4_5bit_int8.npy`：最终 x4 5-bit 模型 LUT，6561 个地址，每个地址包含 16 个 `int8` 值。
- `original_npy/model_s_x4_4bit_int8.npy`：x4 4-bit 原始算法基线，仅用于参考比较；其容量超过 XC7Z020 BRAM 预算。
- `original_npy/model_s_x2_4bit_int8.npy`：正式 x2 4-bit LUT，供 x2 Overlay 与回归使用。
- `bram_mem/model_s_x4_5bit.mem`：最终硬件综合使用的 128-bit ROM 初始化文件。
- `bram_mem/model_s_x2_4bit.mem`：正式 x2 与参数化 testbench 使用的 32-bit ROM 初始化文件。
- `bram_mem/lut_mem_manifest.json`：源文件哈希、形状、字宽、地址数及 `.mem` 哈希清单。
- `tools/npy_to_mem.py`：执行形状、数据类型和字节顺序检查，并生成 `.mem` 与 manifest。
- `tests/test_npy_to_mem.py`：回读并验证转换文件的逐项一致性。
- `lut_inventory.txt`：三套原始 LUT 的形状、容量和数值范围清单。

正式 x2 使用 `model_s_x2_4bit.mem`，正式 x4 使用 `model_s_x4_5bit.mem`；
`model_s_x4_4bit` 只用于算法溯源，不能替代清单冻结的正式 LUT。
