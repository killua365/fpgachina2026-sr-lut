# RTL 标准测试向量

本目录保存由 Python 整数参考模型冻结的测试输入、中间结果和黄金输出。Testbench 通过 `$readmemh` 读取这些文件，实现逐项自检。

## 数据分类

- `source_images/hr`：Set5 五张原始高分辨率图像。
- `source_images/lr_x4`：最终 x4 流程使用的五张低分辨率输入图像。
- `source_images/lr_x2`：正式 x2 与参数化回归输入。
- `source_videos/video_validation`：Stage 39 视频真板验收的最小、奇数矩形、边界和按钮演示素材。
- `reference_baselines/x4_4bit`：原始 x4 4-bit 软件基线输出，用于算法溯源；它不是正式板端黄金数据。
- `rtl_core/x4_5bit`：量化、simplex、地址和插值模块的正式 x4 算术向量。
- `rtl_core/x2_4bit`：同一参数化模块的兼容性回归向量。
- `image_stream/x4_5bit`：最终 x4 小图、整图和各级中间流数据。
- `image_stream/x2_4bit`：参数化图像流回归数据。
- `image_stream/flow_control`：固定的输入停顿和输出背压序列。
- `hls_x2/sr_lut_x2_test_vectors.h`：旧 HLS 接口数值合同的交叉核验材料。
- `tools/generate_rtl_core_vectors.py`：生成核心算术向量及 SHA-256 manifest。
- `tools/generate_image_stream_vectors.py`：生成端到端 AXI 图像流和中间检查点。
- `tests/test_rtl_core_vectors.py`：验证核心向量字段、数量、位宽和哈希。
- `tests/test_image_stream_vectors.py`：验证图像流、边界处理、重排结果和流控序列。

正式版本同时包含 x2 4-bit 与 x4 5-bit Overlay。两套向量分别约束各自数值合同，
不得用一个倍率的黄金结果验收另一个倍率。
