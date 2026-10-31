# TU102 22G 算力解锁核实

日期：2026-10-31

状态：已核实，算力解锁不是当前速度瓶颈；不再重复研究、修改或重启解锁流程。

## 项目内既有事实

项目文档 [`docs/tu102-unlock/README.md`](../tu102-unlock/README.md) 记录了 TU102 22G
解锁。相同 `8192^3`、FP16 in / FP32 accumulate 的 cuBLAS 测量为：

| 状态 | TFLOPS |
|---|---:|
| 锁定参考 | 62.40 |
| 03:00.0 解锁 | 93.69 |
| 04:00.0 解锁 | 94.07 |

整体约 `1.51x`。每 SM 时钟的 tensor 路径约 2 倍；`280 W` 功耗墙使满载频率低于锁定
浅载参考，但不表示解锁失败。

## 当前 boot 只读核实

当前内核为 `7.0.0-38-generic`，boot 时间为 `2026-10-31 02:43:50 UTC`。内核日志中
两张卡均完成 handoff：

```text
0000:03:00.0 core=0 stage=11 mutated=1 unlocked=1 quiescent=1
0000:04:00.0 core=0 stage=11 mutated=1 unlocked=1 quiescent=1
result=0 handoff_safe=1
tu102: result=0 handoff_safe=Y
```

固定路径也成立：

- `tu102-unlock/1.0` DKMS 已为当前内核安装；
- initramfs hook、`scripts/init-top/tu102_unlock`、模块和固件均包含在 `/boot/initrd.img`；
- 模块在 boot handoff 后释放资源，随后由 NVIDIA 驱动接管；
- 两张卡 `power.limit = power.max_limit = 280 W`。

解锁由每次正常 boot 自动执行，不依赖手工命令或 GRUB 特例。

## 满载边界

此前真实模型请求期间同时采样到：

- 空闲 P8 / PCIe Gen1 是省电状态，不代表锁定；
- 请求期间进入 P2 / PCIe Gen3 x16；
- SM 频率约 `1880–1920 MHz`；
- GPU0 峰值功耗 `278.1 W`，接近 `280 W`；
- 温度最高 `57 / 52 °C`；
- 出现 software power cap，没有热降频或硬件降频。

因此当前“慢”不是算力未解锁，也不是功耗墙未打开。`gemm_bench` 曾出现明显不可能的
`1962–2086 TFLOPS`，该结果有计时或计算错误，不能引用。

## 生产事实校正

当前 B4 是手动实例：

- server cwd：`/opt/ai/src/strata-two-stream-serial-20261031`
- engine：`/opt/ai/src/strata-freeze-throughput-20261029T0812Z/build75/strata`
- engine cwd：`/opt/ai/src/strata-freeze-throughput-20261029T0812Z`
- server/vision/engine PID：`93552/93554/93559`
- health loaded、262144 context、6 slots / 2 groups

server cwd 与 engine 二进制路径不同。交接文档必须同时写出两者，不能只写 server 目录。

## 判决

`TU102-UNLOCK-VERIFY` 完成。后续优化从真实 Agent 请求路径开始，优先普通热续轮固定成本；
不再研究解锁、DKMS、initramfs、功耗墙或相邻 GPU 参数，也不为这些事项重启服务器。
