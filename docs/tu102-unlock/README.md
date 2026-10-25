# TU102 22G unlock — automatic on every boot

## Result

Both cards unlock on every normal boot through the stock initramfs. No GRUB
entry, no manual step.

Measured 2026-10-25, cuBLAS 8192^3, FP16 in / FP32 accumulate:

| card | TFLOPS |
|---|---|
| 03:00.0 | 93.69 |
| 04:00.0 | 94.07 |
| locked reference | 62.40 |

1.51x. Per SM clock the tensor path is 2x; the 280 W limit drops the loaded
clock from ~1860 MHz (locked) to ~1600 MHz (unlocked).

## How it works

- DKMS package `tu102-unlock/1.0` builds `tu102_execute.ko` for every kernel
  into `/lib/modules/<ver>/updates/dkms/`.
- `/etc/initramfs-tools/hooks/tu102-unlock` copies the module (decompressed)
  and the firmware into the initramfs.
- `/etc/initramfs-tools/scripts/init-top/tu102_unlock` runs before udev, loads
  the module for `0000:03:00.0` and `0000:04:00.0`, clears the `driver_override`
  the module leaves behind, and halts the boot if a card is left unsafe.
- The stock GRUB entry boots this initramfs. A kernel upgrade rebuilds the
  module through DKMS and the initramfs through initramfs-tools.

## Verify

```sh
journalctl -k -b | grep -i tu102
```

Expect two `core=0 stage=11 mutated=1 unlocked=1 quiescent=1` and
`result=0 handoff_safe=Y`, then `nvidia-smi -L` shows both GPUs.

## Disable

```sh
sudo /opt/tu102-unlock/disable.sh
```

Removes the initramfs hook and rebuilds the initrd; the next boot is locked.

## Board facts encoded in the module

- 03:00.0 pre-driver: SS `0x40000`/`0x7`, `0x625f04=0x20e09`
- 04:00.0 pre-driver: SS `0x12451055`/`0x6`, `0x625f04=0x1`
- the other nine guarded registers are identical on both cards
- IOMMU is on: `dma_alloc_coherent` returns IOVAs below 4 GiB (~0xfff80000)
- nvidia does not reset SS0/SS1 when it initialises the GPU

## Deviations from upstream KohakUnlock (all required here)

1. Selector pair: upstream refuses anything but 0/0. Firmware leaves a
   different pair per card and per boot, so the pair is recorded (stage=12)
   and not gated; the strict guards are the descriptor, FWSEC image and WPR2.
2. `0x625f04` is `0x20e09` on one card; both `0x1` and `0x20e09` are accepted.
3. `tu102_dma.c` carried Leadtek IDs (`1e04`/`107d:1e04`); replaced with
   board macros in `tu102_dma.h`.
4. The DMA pool required addresses >= 4 GiB; IOMMU IOVAs are below that, so
   the bound is 4K alignment inside `[0x1000, 1 TiB)`.
5. The module does not clear the `driver_override` it sets. The init-top
   script clears it for both cards; without that nvidia skips the card.
6. The module takes a comma-separated target list and calls
   `pci_enable_device()` when firmware left a card with decode off.

## Files

- `/usr/src/tu102-unlock-1.0/` — DKMS source (patched `kernel/`)
- `/etc/initramfs-tools/hooks/tu102-unlock`
- `/etc/initramfs-tools/scripts/init-top/tu102_unlock`
- `/lib/firmware/tu102-lab/*.bin`
- `/opt/tu102-unlock/` — this README, `install-auto.sh`, `disable.sh`,
  `rebuild.sh`, `gemm_bench`

## Limits

Tested only on this VBIOS (90.02.42.00.F9) and driver 595.71.05.
