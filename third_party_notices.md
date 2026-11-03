# 第三方来源与许可边界

本项目不对第三方代码、LUT、素材或本队代码追加许可。项目整体尚未指定统一开源许可证；对外发布由项目所有者确认授权范围。

| 组件 | 固定内容与来源说明 | 许可证位置 |
|---|---|---|
| FPGA DisplayPort | `src/third_party/fpga_displayport.zip`；来源和逐文件 SHA 见同名 JSON | 包内 `LICENSE`（MIT）及版权头 |
| Digilent HDMI RX | `digilent_hdmi_rx.zip`，保留上游 dvi2rgb IP | ZIP 内上游许可和源码头 |
| Digilent video IP | `digilent_video.zip`，PYNQ HDMI 输出所需 IP | ZIP 内组件许可及源码头 |
| Digilent Nexys 板级文件 | `digilent_nexys_video_board.zip` | ZIP 内原始元数据 |
| PYNQ-Z2 板级文件 | `build/scripts/pynq/boards/` | 原始板级 XML 与说明 |
| MJPEGZero | `mjpegzero.zip`，构建脚本保留的 JPEG 分支依赖 | ZIP 内原始说明与源码声明 |
| 板端 Python 构建依赖 | `hdmi_python.zip`，evdev/setuptools/packaging/wheel | 各发行包内部许可 |
| FFmpeg | `hdmi_ffmpeg.zip`，固定 ARM Debian 包及依赖清单 | 包内 Debian 版权文件 |
| 算法参考 | `algorithm_sources.zip`，SR-LUT Model S 导出/参考评分、LeRF 评分参考 | 包内 README、LeRF LICENSE、源文件声明；SR-LUT 未据此补授许可 |
| DejaVu Sans | `src/ps/hdmi_display/fonts/`，常规/粗体 TTF | `license.txt` |
| Windows Python 及依赖 | `runtime/host_python_3_12_14.zip` 与 `reference_python_3_12_14.zip` | ZIP 内 `LICENSE.txt`、各 dist-info 的 LICENSE/METADATA |
| ARM 环境 | `runtime/board/project_venv_armv7.tar.gz` | 原始包许可/元数据；还需匹配的 PYNQ 系统镜像 |
| LUT、Set5、演示视频 | `data/` 中来源清单、相关上游参考说明 | 保留原声明；不宣称已取得全部素材的公开再分发许可 |

第三方包逐文件清单与压缩包 SHA-256 位于同名 JSON；汇总入口为 [components.json](src/third_party/components.json)。未修改上游组件的内部大小写及文件名，构建时才展开到外部工作目录。

SR-LUT 参考来源：[SR-LUT](https://github.com/yhjo09/SR-LUT)。实际保存的来源说明以包内原 README 为准；文件哈希固定了本项目使用的字节。LeRF 来源：[LeRF](https://github.com/ddlee-cn/LeRF-PyTorch)。

私钥、密码、Vivado 许可证、整张系统卡镜像与 Nexys 原厂 QSPI 二进制不在正式交付中。它们也未因本次整理被复制或删除。
