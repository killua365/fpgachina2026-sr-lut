# HDMI 演示素材

这里保存演示素材的原始来源，供 `src/ps/prepare_hdmi_1080_assets.py` 生成 HDMI 输入。当前 r15 图片评分使用 `data/images/paired/` 的原生 LR 与本目录的 HR 配对；不对原图折叠、镜像或拼接。

- `images/original/`：Set5 的 baby、bird、butterfly、head、woman 五张完整 RGB 原图。
- `video/`：320×180、180 帧运动素材的原件，保留用于历史输入复现。
- `manifest.json`：原始素材审计记录，包含尺寸、SHA-256 和方向审计值。记录中图片名相对于 `images/original/`，视频路径相对于本目录。历史生成器不属于当前工具集；本次完整性交付检查逐文件核对这些素材。

`data/golden/fixed_x4_set5/` 是独立的固定尺寸算法回归输入与黄金结果，不替代本目录原图或当前原生配对素材。
