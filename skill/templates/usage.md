# PYNQ 案例模板与板端接入

适用：单线程控制一个完整 Overlay，双向 AXI DMA simple mode，每批输入输出尺寸固定并具有明确结束条件。题目外场景包括灰度分割、滤波、采样增益、音频块处理。原项目观察来源为 Overlay/DMA/缓存生命周期；新提炼版本的验证限于离线模拟，见 evidence。

## 可复用接口

- `BatchSession(backend, contract, timeout_s)`：分配并重复使用两块缓冲区。
- `run(source)`：严格检查输入，返回独立结果副本和本次主机总耗时。
- `close()`：确认 DMA 停止后释放；停止失败则 quarantine 并保留强引用，禁止继续运行。
- backend 方法：`allocate/configure/receive/start/send/poll/halt/free`。
- hooks 方法：`configure(overlay, contract)`、`start(overlay)`、`done(overlay)`；寄存器语义由算法适配器负责。

不能并发调用同一个 session。返回副本可在下一批运行时保留，不会随着 DMA 复用被覆盖。此模板优先可理解和正确性，不复刻本队特定 ACP/零拷贝性能优化。

## 准备板端配置

依据 [接口合同](interface_contract.md) 填写自己的真实数据。复制下面 profile，将占位符全部替换，保存为 `profile.json`，BIT/HWH 放在 profile 同目录：

```json
{
  "board": "your_board_and_pynq_version",
  "bit": "accelerator.bit",
  "hwh": "accelerator.hwh",
  "dma_ip": "axi_dma_0",
  "required_ips": ["axi_dma_0", "your_compute_ip"],
  "files": [
    {"path": "accelerator.bit", "sha256": "replace_with_actual_sha256"},
    {"path": "accelerator.hwh", "sha256": "replace_with_actual_sha256"}
  ],
  "contract": {
    "input": {"shape": [32, 48], "dtype": "uint8"},
    "output": {"shape": [32, 48], "dtype": "uint8"}
  },
  "control": {
    "protocol": "hls_ap_ctrl_hs",
    "ip": "your_compute_ip",
    "scalar_writes": []
  },
  "timeout_s": 2.0
}
```

`scalar_writes` 为 `[字节偏移, uint32值]` 数组，必须来自自己的 HLS 导出头文件/HWH。不能直接套用本队自定义 RTL 的启动位。已有 `HlsControlHooks` 只用于明确采用 `ap_ctrl_hs` 的 IP；自由运行或自定义控制 IP 应实现自己的三个 hooks。

HWH 提供寄存器描述时，可在自己已正确加载的 Overlay 上查看：

```python
print(overlay.ip_dict["your_compute_ip"])
print(overlay.your_compute_ip.register_map)
```

缺失寄存器描述不代表寄存器不存在。不能靠名字猜地址；使用真实接口文档和 `read/write`，并确保寄存器范围、读清除位等语义正确。

## 运行方式

先停止旧程序的采集、提交、DMA 及后台访问，确保独占目标板。目录包含 `checks/` 和 `templates/`；板端必须安装匹配镜像的 PYNQ。

```text
python run_hardware.py --profile profile.json --input input.npy --out out/hardware --download
```

该命令**会改变目标 FPGA 配置**，没有 `--download` 不加载。新算子/新板的功能正确性需另用 `run_checks.py` 与独立 `expected.npy` 比对。输出中 `hardware_access=true` 只表示该路径实际访问硬件，不等于黄金正确或持续性能验收。

底层使用 PYNQ transfer 启动传输，通过 MMIO 轮询错误/idle/实际长度及算法 done，避免在传输阶段直接调用无超时 `wait()`。CPU读取前同步；释放前停两路 DMA，确认 Halted。恢复失败时保持 session 引用；命令行保持进程存活并重试停止，直到状态确认，不用 Ctrl+C 冒充硬件停止。不要用杀进程代替正常释放，不自动改载其他 Overlay。导入模块自行编写调用程序时也须保留同样的生命周期。

## 失效条件与迁移限制

- SG/cyclic DMA、VDMA、部分重配置、多线程共享、输出尺寸不确定等需要单独后端。
- 缺失 TLAST、TKEEP 不符合合同、输出提前结束或数据长度超 DMA 配置都会失败，不能只加长等待。
- PYNQ 下载和驱动构造内部可能阻塞；模板不保证故障 AXI 总线可由超时中断。
- `cacheable` 不等价于已证明 coherent；同步按 PynqBuffer API 执行，不凭板卡名字跳过。
- stop 确认只证明 DMA 不再访存，不自动复位计算 IP。下一次新建/重载前应停止其他消费者并按硬件设计复位。
- 新模板的 fake PYNQ/MMIO 测试检查程序逻辑，不替代新硬件实测。
