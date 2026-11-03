# 操作手册

## 连接与启动

PYNQ HDMI OUT 连接 Nexys HDMI IN；摄像头和鼠标通过供电 USB 集线器连接 PYNQ USB HOST。Nexys 使用 MiniDP 输出，显示器输入源应与 DP 或 VGA 接线一致。

在项目根目录使用 `demo.cmd` 启动 DP 配置，或使用 `demo_vga.cmd` 启动独立 VGA 配置。VGA 当前验证对象为现有转换器和 DELL P1917S。默认 DP 身份为 `26101201`，VGA 身份为 `26102101`；两者分别保留资格记录。

正常演示没有累计运行时限。启动检查仍校验文件、源码和设备身份；初始化包含板端旧服务停止及硬件加载。退出在原终端按一次 Ctrl+C，等待恢复完成。

## 模式与查看

| 功能 | 操作 |
|---|---|
| 图片 | BTN0；Prev/Next image 切图，选择 ROI，Save PNG 导出 |
| 视频 | BTN1；播放/暂停、Frame ±、Restart、Speed、Loop |
| 摄像头 | BTN2；固定 x4；Freeze/Resume 冻结和恢复 |
| 空闲 | BTN3 |
| 倍率 | 图片/视频使用 SW0：0=x2，1=x4 |
| 布局 | SR full、Compare、Nearest、Wipe |
| Fit/1:1 | Fit 等比显示完整内容；1:1 按输出像素显示，右键拖动平移 |
| Reset view | 恢复 Fit、平移归零、Wipe 居中，保留布局和图片 ROI |
| Reset ROI | 重置图片输入区域 |

查看状态不改变算法输入输出。部分尺寸恰好匹配面板时，Fit 与 1:1 的外观可能相同。切图、倍率或输入模式时清理旧结果并重建对应状态。

Wipe 左侧为 Nearest，右侧为同帧 SR。在有效画面内按住左键连续拖动分界线；分界线覆盖可见画面，并避开工具栏和按钮。

## 图片与质量指标

采用 Set5 的 baby、bird、butterfly、head、woman 原生 HR/x2 LR/x4 LR 配对。LR ROI `(x,y,w,h)` 对应 HR 的 `(s*x,s*y,s*w,s*h)`，其中 s 为实际倍率。

Compare 显示图片名称、倍率、PSNR-Y、SSIM-Y 和 SR 相对 Nearest 的带符号差值；图片顶部不显示像素尺寸。PSNR-Y 保留两位小数、裁去四像素边界；SSIM-Y 保留四位小数，使用 11×11 高斯窗口。计算使用原始算法结果，不包含显示缩放、UI 或黑边。

松开 ROI 后后台计算；切图或倍率变化时旧结果失效。正差显示绿色、负差橙色、相同灰色，无参考或不可评分时显示不可用。不同图像和 ROI 的质量收益可能不同。

## 视频导入与导出

文件选择器访问 PYNQ 文件系统。将 H.264 MP4 或 MJPEG AVI 放到 `/home/xilinx/sr_lut_media`，或已挂载的 `/media/xilinx`、`/run/media/xilinx`。

```powershell
ssh -i "$env:USERPROFILE\.ssh\sr_lut_pynq_z2_ed25519" xilinx@192.168.2.99 'mkdir -p /home/xilinx/sr_lut_media'
scp -i "$env:USERPROFILE\.ssh\sr_lut_pynq_z2_ed25519" .\sample.mp4 xilinx@192.168.2.99:/home/xilinx/sr_lut_media/
```

BTN1 → Open video → 选择文件 → 等待导入完成 → Play/Pause。导入可取消；默认包已有演示片段。导出显示 Saved MP4 后回到暂停状态。视频 Compare 只显示实际输入、输出尺寸和倍率。

## 摄像头与同帧快照

算法输入固定 480×270，x4 输出 1920×1080。Compare 仅在 Fit 下显示 FPS，固定青蓝色刻度为 0–30；1:1 隐藏 FPS，保留分辨率。FPS 基于 PYNQ VDMA 确认的新帧，最近两秒滚动、每秒更新；冻结和同帧重绘不计新帧。

Freeze 将当前已显示帧的输入及 SR 复制为独立快照。冻结后可平移、切换布局、拖动 Wipe 和保存，Resume 恢复最新采集帧。保存到板端本次结果目录的 `worker/camera_<编号>/`：

| 文件 | 内容 |
|---|---|
| `source.png` | 480×270 算法输入 |
| `sr.png` | 完整 SR 输出，不含界面 |
| `nearest.png` | 同一输入的最近邻结果 |
| `comparison.png` | 同帧对照图，使用保存时的查看参数 |
| `export.json` | 帧身份、时间、查看配置和文件哈希 |

保存先写临时文件并读回校验，完成后发布。空间不足或写入失败会显示错误；切换模式或退出会取消未完成任务。Compare 指标常驻，不依赖底部工具栏是否可见。
