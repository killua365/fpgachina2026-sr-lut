# 环境部署与复现

## 1. 复现范围

工程提供当前 x2/x4 BIT/HWH、Nexys DP/VGA 位流、RTL 与 PS 源码、固定 LUT、黄金数据、第三方 IP、运行包及离线 Python 环境。硬件构建使用 Vivado 2026.1。

外部前提包括 Windows、Git、OpenSSH、Vivado 及器件支持、Digilent/FTDI 驱动、匹配的 PYNQ 系统镜像、两块开发板与显示设备。板端 venv 依赖镜像内的 PYNQ 驱动，不能代替完整系统镜像。保留隐藏 `.git`：启动器按历史源码提交核对当前发布合同。

所有命令从工程根目录执行。构建输出使用不存在的纯英文路径；运行与构建不要同时占用 Vivado/JTAG。

## 2. Windows 主机

默认 Vivado bin 为 `D:\vivado2026.1\2026.1\Vivado\bin`。安装位置不同时：

```powershell
$env:SR_LUT_VIVADO_BIN='D:\amd\2026.1\Vivado\bin'
.\demo.cmd --check
.\demo_vga.cmd --check
```

构建入口另支持 `-Vivado '完整的vivado.bat路径'`。主机依赖分为两套固定环境：

| 环境 | 版本与用途 |
|---|---|
| host | CPython 3.12.14、NumPy 2.2.6、OpenCV 4.12.0、Pillow 11.3.0；启动、控制与主机验证 |
| reference | CPython 3.12.14、NumPy 1.26.4、SciPy 1.12.0、OpenCV 4.10.0、Pillow 10.4.0；参考模型及质量评分 |

使用 `tools/python.cmd host` 或 `tools/python.cmd reference`。首次运行校验并解压环境到 `%LOCALAPPDATA%\sr_lut_fpga\cache`，以后核验复用；可用 `SR_LUT_CACHE_DIR` 指定缓存位置。无需联网安装 Python 包。

SSH 目标为 `xilinx@192.168.2.99`，密钥路径为 `%USERPROFILE%\.ssh\sr_lut_pynq_z2_ed25519`。新电脑应配置自己的公钥并核实板卡主机指纹。主机以太网需与板卡位于同一网段。

Nexys JTAG 使用 `210276B3E2AD` 前缀识别；FTDI UART 实体序列号为 `A904CVLW`，115200、8N1。采集需要 FTDI 驱动的 64 位 `C:\Windows\System32\ftd2xx.dll`，不依赖固定 COM 号。程序临时调整接收延迟，退出时恢复。

## 3. PYNQ 环境与部署

已验证环境为 PYNQ 3.1.1、Python 3.10.4、ARMv7、Linux 6.6.10-xilinx-v2024.1、NumPy 1.21.5、OpenCV 4.5.4、Pillow 9.0.1、evdev 1.9.2。详细版本、系统包及服务配置位于 `runtime/board/`。

当前发布合同指定目录 `/home/xilinx/sr_lut_demo_r17_repack`，部署前按 `board/release/source.json` 核对版本。首次部署：

```powershell
.\tools\python.cmd host board/host/deploy_business_session.py --run
```

该命令复制和校验文件，不启动 FPGA。重建板端系统时：

1. 安装适配 PYNQ-Z2 的镜像，配置网络、USB 和 PYNQ 驱动，按 `runtime/board/environment.json` 安装系统依赖。
2. 将 `runtime/board/project_venv_armv7.tar.gz` 解压至 `/home/xilinx/sr_lut_hdmi_envs`，获得 `hdmi_m1_20260909_r11`。确认其中 Python 可导入 `pynq,numpy,cv2,PIL,evdev`；`pynq_baseline.pth` 指向镜像系统包。
3. 校验 `board/recovery/releases/` 中两个 ZIP 的 SHA-256，将各 ZIP 内的 `sr_lut_app_1.9.0_board` 安装到对应位置：

| 包 | 安装目录 | 软链接 |
|---|---|---|
| `sr_lut_app_1.9.0_board_release.zip` | `/opt/sr-lut/releases/app_1_9_0_stage39_formal_6DBCF99BB2BA` | `/opt/sr-lut/current` |
| `sr_lut_app_1.9.0_board_candidate_vivado2026_1.zip` | `/opt/sr-lut/candidates/app_1_9_0_stage39_candidate_3589C1909FD1` | `/opt/sr-lut/candidate` |

4. 安装快照中的两个 `sr-lut-*.service` 到 `/etc/systemd/system`，将 `90-sr-lut-once.conf` 放到 `/etc/systemd/system/bootpy.service.d`；重新加载 systemd，启用 portal 服务，保持 candidate 服务停止。
5. 重启并确认初始化、网络及正式服务正常，然后部署当前 r17 包。

当前合同绑定实验板、SD 卡 CID 和服务身份。更换卡或板需要更新实际身份并重新完成资格验证。项目不附带系统盘镜像、私钥或个人软件许可证。

## 4. 构建 PYNQ 与默认 Nexys 配置

```powershell
.\tools\rebuild.ps1 -Target CheckIP
.\tools\rebuild.ps1 -Target PynqX2 -WorkDirectory D:\sr_lut_build_x2
.\tools\rebuild.ps1 -Target PynqX4 -WorkDirectory D:\sr_lut_build_x4
.\tools\rebuild.ps1 -Target NexysSimulation -WorkDirectory D:\sr_lut_build_sim
.\tools\rebuild.ps1 -Target NexysRoute -WorkDirectory D:\sr_lut_build_nexys
.\tools\rebuild.ps1 -Target NexysBit -Checkpoint D:\sr_lut_build_nexys\route\routed.dcp -WorkDirectory D:\sr_lut_build_bit
```

PYNQ 输出位于工作目录的 `source/build/hdmi_x2_1080p30_work/export` 或 `source/build/hdmi_x4_1080p30_work/export`，含 BIT/HWH/XSA；实现报告位于 `source/board/local/hdmi_x2_1080p30` 或 x4 同名目录。Nexys 路由输出位于 `route`，位流输出位于 `bit`。

准备程序校验源码副本、第三方包和 IP 配置。MIG/VDMA 从固定 XCI 重新生成；PYNQ 核对 HWH 参数和地址映射。LUT 转换入口是 `data/lut/tools/npy_to_mem.py`，ARM 原生像素库的编译命令是 `src/ps/hdmi_display/native/build.json`，应在匹配的 ARM 环境执行。

已有干净构建结果和配置字节比较位于 `build/reports/rebuild_20261020/`。BIT 头的构建时间等信息可能变化，硬件配置比较从同步字开始进行。新产物须独立验证后才能替代当前冻结产物。

## 5. 独立 VGA 配置

日常运行使用 `demo_vga.cmd`。以下重建命令会生成新的候选文件和资格清单，应在完整工程副本中执行；不能覆盖正在使用的已验收目录。

```powershell
if (!$env:SR_LUT_VIVADO_BIN) { $env:SR_LUT_VIVADO_BIN='D:\vivado2026.1\2026.1\Vivado\bin' }
$vgaWork=Join-Path $PWD 'build\reproduction_work\nexys_vga_26102101'
.\tools\python.cmd host tools/prepare_reproduction.py --out $vgaWork
.\tools\python.cmd host build/candidates/nexys_vga_26102101/prepare_candidate.py
.\build\candidates\nexys_vga_26102101\run_sim.ps1
& "$env:SR_LUT_VIVADO_BIN\vivado.bat" -mode batch -notrace -source "$vgaWork\nexys_route.tcl" -log "$vgaWork\vivado.log" -journal "$vgaWork\vivado.jou" -tclargs "$vgaWork\route" 26102101 "$vgaWork\ips"
& "$env:SR_LUT_VIVADO_BIN\vivado.bat" -mode batch -notrace -source .\build\candidates\nexys_vga_26102101\build_incremental.tcl -log "$vgaWork\incremental.log" -journal "$vgaWork\incremental.jou"
.\tools\python.cmd host build/candidates/nexys_vga_26102101/qualify_candidate.py seal
.\demo_vga.cmd --check
```

增量实现使用该轮综合检查点及 `build/artifacts/nexys/route/routed.dcp` 物理参考。候选仅修改 AUX 电源状态兼容处理，保留真实链路训练和错误检查。硬件身份、位流和默认 DP 配置分别管理。

## 6. 离线验证

输出目录必须是新路径。根据修改范围选取对应检查，不要求每次运行全部项目。

```powershell
.\tools\python.cmd host tools/verify_delivery.py
.\tools\python.cmd reference tools/run_tests.py --suite reference --out D:\sr_lut_reference_checks
.\tools\python.cmd reference tools/run_tests.py --suite golden --out D:\sr_lut_golden_checks
.\tools\python.cmd host -m unittest discover -s data/lut/tests
.\tools\python.cmd host tools/run_tests.py --suite ps --out D:\sr_lut_ps_checks
.\tools\python.cmd host tools/run_tests.py --suite controller --out D:\sr_lut_controller_checks
.\tools\python.cmd host tools/run_tests.py --suite launcher --out D:\sr_lut_launcher_checks
.\tools\python.cmd host build/candidates/nexys_vga_26102101/test_host.py
.\tools\python.cmd host build/candidates/nexys_vga_26102101/test_demo.py
.\tools\python.cmd host build/candidates/nexys_vga_26102101/test_paths.py
.\tools\python.cmd host report/validation/vga_candidate_20261021/verify_evidence.py
.\tools\python.cmd host tools/recompute_camera.py --out D:\sr_lut_r5_recomputed
```

PS 测试在 Windows 下有五项 ARM/Linux 专属跳过，工具会返回非零状态；应读取 `executed_tests_passed`、失败和跳过分类。历史 r5 的独立 ZIP 包含原始记录及当时复算代码，不需要恢复旧演示目录。

Skill 独立用法见 `skill/README.md`。新主机路径验证使用 `tools/check_portability.py --out <新目录>`，检查独立 Git、空缓存及两套离线环境，不访问板卡。

## 7. 修改后的发布流程

软件源码变化时先提交实际源码及显式输入清单，再用 `tools/package_current.py --out board/release/<新包名>.zip` 直接打包。主机资格使用 `sim/nexys/qualify_business_session.py <新证据目录>`，控制器通过 `business_session_gate.py seal` 绑定已验证提交；启动器通过 `qualify_demo_session.py` 生成新资格记录。各工具拒绝覆盖既有资格输出。

VGA 路径脚本调整不改变已有位流；其独立清单须对应实际脚本和针对性测试。完整交付清单由 `tools/verify_delivery.py --write-manifest` 生成，验证通过后再用 `tools/package_submission.py --out <新ZIP路径>` 导出提交包。文件完整性、功能正确性和实板验收分别保留证据。

## 8. 常见问题

- 本地校验失败：根据报错恢复对应文件或完成新版本资格验证；不要仅修改哈希跳过验证。
- SSH 超时：检查同网段、板卡启动和密钥/主机指纹，不能据此判定 FPGA 故障。
- 源程序尚在运行：使用所属会话的正常退出接口，等待 DMA/显示关闭及原服务恢复。
- 显示异常：分别检查输入源、HDMI 几何、UART CRC/新鲜度和 DP 训练状态。综合健康错误不直接说明物理线缆掉线。
- 运行后日志：新会话写入 `board/analysis` 或 `board/acceptance`，构建写入独立工作目录；它们不属于静态交付清单。
