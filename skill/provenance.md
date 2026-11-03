# 来源与授权边界

本包新增代码和中文说明由本队项目开发任务产生。生命周期、比较与报告检查方法从本队现有实现提炼；不导入或复制SR-LUT算法、LUT、训练图像、HDMI/DP第三方RTL或字体。历史报告保留原工具版权及来源信息。

本次没有替项目所有者给既有代码重新授权，也没有进行公开发布。赛事强烈建议MIT/Apache-2.0；正式开源时需由团队确认适用许可证及原项目声明。本文件不是对所有原始工程内容的再分发授权。

## 项目内来源

相对主项目根目录的来源及哈希见 `evidence/source_index.json`。核心依据：

- `src/ps/video_pipeline/frame_session.py`：可复用分配、同步、完成检查。
- `src/ps/hdmi_display/engine.py`：停止确认、切换与缓冲区所有权。
- `board/pynq/hdmi_hardware_validation.py`：读原报告而非只信清单。
- `board/analysis/2026-10-18/camera_r5_scan_result/result.json`：历史完整新显示帧率。
- r11实施及ARM回归记录：背景填充和非连续数组问题。

独立包包含最小必要证据副本，完整历史原始采集日志仍在主工程。复制报告不是重新测量。

## 官方接口参考

核对日期：2026-10-20。板端模板按本项目PYNQ 3.1.1经验整理；官方3.0 DMA源码用于核对 simple mode API 和等待行为。API核对不替代新板实测。

- [PYNQ Overlay](https://pynq.readthedocs.io/en/v3.1/pynq_libraries/overlay.html)：配置加载与元数据。
- [PYNQ allocate](https://pynq.readthedocs.io/en/v3.1/pynq_libraries/allocate.html)：物理缓冲区及同步API。
- [PYNQ DMA官方源码](https://pynq.readthedocs.io/en/v3.0.0/_modules/pynq/lib/dma.html)：simple DMA状态与传输接口。本文没有复制其完整实现。

`requirements.txt`引用NumPy，使用者自行安装其发行包并遵守其许可证。PYNQ必须来自适配目标板的系统环境。
