"""Hardware readings for the Linux NVIDIA server's Monitor tab.

A background thread samples once a second and keeps the last 60 readings of each series for sparklines.
GPU readings come from NVIDIA's NVML library through ctypes.  CPU and RAM use psutil when installed,
otherwise Linux /proc.  Anything that cannot be read is None; telemetry never stops the server.
"""
from __future__ import annotations

import collections
import ctypes
import glob
import os
import sys
import threading
import time

HISTORY = 60


class _Nvml:
    class Util(ctypes.Structure):
        _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

    class Mem(ctypes.Structure):
        _fields_ = [
            ("total", ctypes.c_ulonglong),
            ("free", ctypes.c_ulonglong),
            ("used", ctypes.c_ulonglong),
        ]

    def __init__(self, index=0):
        self.lib = self.dev = None
        for name in ("libnvidia-ml.so.1", "libnvidia-ml.so"):
            try:
                self.lib = ctypes.CDLL(name)
                break
            except OSError:
                continue
        if self.lib is None:
            return
        try:
            init = getattr(self.lib, "nvmlInit_v2", None) or self.lib.nvmlInit
            if init() != 0:
                self.lib = None
                return
            handle = ctypes.c_void_p()
            get = (
                getattr(self.lib, "nvmlDeviceGetHandleByIndex_v2", None)
                or self.lib.nvmlDeviceGetHandleByIndex
            )
            if get(ctypes.c_uint(index), ctypes.byref(handle)) != 0:
                self.lib = None
                return
            self.dev = handle
        except (AttributeError, OSError):
            self.lib = None

    def ok(self):
        return self.lib is not None and self.dev is not None

    def _uint(self, fn, *args):
        value = ctypes.c_uint()
        try:
            return value.value if getattr(self.lib, fn)(self.dev, *args, ctypes.byref(value)) == 0 else None
        except (AttributeError, OSError):
            return None

    def name(self):
        buffer = ctypes.create_string_buffer(96)
        try:
            if self.lib.nvmlDeviceGetName(self.dev, buffer, ctypes.c_uint(96)) == 0:
                return buffer.value.decode(errors="replace")
        except (AttributeError, OSError):
            pass
        return None

    def read(self):
        out = {}
        utilization = self.Util()
        try:
            if self.lib.nvmlDeviceGetUtilizationRates(self.dev, ctypes.byref(utilization)) == 0:
                out["util"] = utilization.gpu
        except (AttributeError, OSError):
            pass
        memory = self.Mem()
        try:
            if self.lib.nvmlDeviceGetMemoryInfo(self.dev, ctypes.byref(memory)) == 0:
                out["mem_used"], out["mem_total"] = memory.used, memory.total
        except (AttributeError, OSError):
            pass
        out["temp"] = self._uint("nvmlDeviceGetTemperature", ctypes.c_uint(0))
        milliwatts = self._uint("nvmlDeviceGetPowerUsage")
        out["power"] = milliwatts / 1000.0 if milliwatts is not None else None
        limit = self._uint("nvmlDeviceGetEnforcedPowerLimit")
        out["power_limit"] = limit / 1000.0 if limit is not None else None
        out["pcie_gen"] = self._uint("nvmlDeviceGetCurrPcieLinkGeneration")
        out["pcie_gen_max"] = self._uint("nvmlDeviceGetMaxPcieLinkGeneration")
        out["pcie_width"] = self._uint("nvmlDeviceGetCurrPcieLinkWidth")
        rx = self._uint("nvmlDeviceGetPcieThroughput", ctypes.c_uint(1))
        tx = self._uint("nvmlDeviceGetPcieThroughput", ctypes.c_uint(0))
        out["pcie_rx_mb"] = rx / 1024.0 if rx is not None else None
        out["pcie_tx_mb"] = tx / 1024.0 if tx is not None else None
        return out


def gpu_reader(index=0):
    """Read one NVIDIA GPU by the stable PCI-order index."""
    return _Nvml(index)


def free_vram_mib(index=0):
    """Free VRAM of a CUDA card in MiB, or None when NVML cannot read it."""
    reader = gpu_reader(index)
    if not reader.ok():
        return None
    reading = reader.read()
    total, used = reading.get("mem_total"), reading.get("mem_used")
    if total is None or used is None:
        return None
    return int((total - used) >> 20)


def _cpu_topology(root: str = "/sys/devices/system"):
    """(sockets, NUMA nodes) from Linux sysfs."""
    try:
        packages = set()
        for path in glob.glob(os.path.join(root, "cpu", "cpu[0-9]*", "topology", "physical_package_id")):
            try:
                with open(path, encoding="utf-8") as handle:
                    packages.add(handle.read().strip())
            except OSError:
                continue
        nodes = len(glob.glob(os.path.join(root, "node", "node[0-9]*")))
        return (len(packages) or None), (nodes or None)
    except Exception:  # noqa: BLE001 - a missing figure is shown as missing, never guessed
        return None, None


def _cpu_name():
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


class _CpuRamFallback:
    """CPU load and RAM from /proc when psutil is absent."""

    def __init__(self):
        self.prev = self._times()

    @staticmethod
    def _times():
        try:
            with open("/proc/stat", encoding="ascii") as handle:
                fields = [int(value) for value in handle.readline().split()[1:]]
            return fields[3] + fields[4], sum(fields)
        except (OSError, ValueError, IndexError):
            return None

    def cpu(self):
        current = self._times()
        previous, self.prev = self.prev, current
        if not current or not previous or current[1] == previous[1]:
            return None
        return max(0.0, min(100.0, 100.0 * (1 - (current[0] - previous[0]) / (current[1] - previous[1]))))

    @staticmethod
    def ram():
        try:
            with open("/proc/meminfo", encoding="ascii") as handle:
                info = dict(line.split(":", 1) for line in handle)
            total = int(info["MemTotal"].split()[0]) * 1024
            available = int(info["MemAvailable"].split()[0]) * 1024
            return total - available, total
        except (OSError, KeyError, ValueError):
            return None, None


class Telemetry:
    def __init__(self, extra=None, gpu_index=0, gpu_indices=None):
        """``extra()`` adds server figures each second; ``gpu_indices`` records a layer split as one aggregate."""
        self.extra = extra
        self.lock = threading.Lock()
        self.now: dict = {}
        self.hist = collections.defaultdict(lambda: collections.deque(maxlen=HISTORY))
        indices = list(gpu_indices) if gpu_indices and len(gpu_indices) > 1 else [gpu_index]
        self.gpus = [(index, gpu_reader(index)) for index in indices]
        self.gpus = [(index, reader) for index, reader in self.gpus if reader.ok()] or self.gpus[:1]
        self.gpu = self.gpus[0][1]
        try:
            import psutil  # noqa: F401
            self.ps = sys.modules["psutil"]
        except ImportError:
            self.ps = None
        self.fallback = _CpuRamFallback()
        gpu_name = " + ".join(reader.name() or "?" for _, reader in self.gpus) if self.gpu.ok() else None
        vram_total = sum(
            total for _, reader in self.gpus
            if (total := reader.read().get("mem_total")) is not None
        )
        self.static = {
            "gpu_name": gpu_name,
            "gpu_vram_total_mib": int(vram_total >> 20) if vram_total else None,
            "gpu_count": len(self.gpus),
            "gpu_note": None if self.gpu.ok() else "NVIDIA NVML could not read the GPU",
            "cpu_name": _cpu_name(),
            "cores": (self.ps.cpu_count(logical=False) if self.ps else None) or None,
            "threads": os.cpu_count(),
            "sockets": _cpu_topology()[0],
            "numa_nodes": _cpu_topology()[1],
            "psutil": self.ps is not None,
        }
        self._disk_prev = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _disk(self):
        if not self.ps:
            return None, None
        try:
            counters = self.ps.disk_io_counters()
        except (OSError, RuntimeError):
            return None, None
        if counters is None:
            return None, None
        now = time.time()
        previous, self._disk_prev = self._disk_prev, (now, counters.read_bytes, counters.write_bytes)
        if previous is None or now <= previous[0]:
            return None, None
        elapsed = now - previous[0]
        return (
            (counters.read_bytes - previous[1]) / elapsed / 2**20,
            (counters.write_bytes - previous[2]) / elapsed / 2**20,
        )

    def sample(self):
        result = {}
        if self.gpu.ok():
            readings = [(index, reader.read()) for index, reader in self.gpus]
            aggregate = dict(readings[0][1])
            if len(readings) > 1:
                def values(key):
                    return [reading[key] for _, reading in readings if reading.get(key) is not None]

                for key in ("mem_used", "mem_total", "power", "power_limit", "pcie_rx_mb", "pcie_tx_mb"):
                    found = values(key)
                    aggregate[key] = sum(found) if found else None
                utilizations = values("util")
                aggregate["util"] = (
                    sum(utilizations) / len(utilizations) if utilizations else None
                )
                temperatures = values("temp")
                aggregate["temp"] = max(temperatures) if temperatures else None
                result["gpus"] = [
                    {
                        "index": index,
                        "name": reader.name(),
                        "util": reading.get("util"),
                        "mem_used": reading.get("mem_used"),
                        "mem_total": reading.get("mem_total"),
                        "temp": reading.get("temp"),
                        "power": reading.get("power"),
                    }
                    for (index, reading), (_, reader) in zip(readings, self.gpus)
                ]
            result.update({f"gpu_{key}": value for key, value in aggregate.items()})
        if self.ps:
            try:
                result["cpu"] = self.ps.cpu_percent(interval=None)
                memory = self.ps.virtual_memory()
                result["ram_used"], result["ram_total"] = memory.total - memory.available, memory.total
            except (OSError, RuntimeError):
                pass
        else:
            result["cpu"] = self.fallback.cpu()
            result["ram_used"], result["ram_total"] = self.fallback.ram()
        result["disk_read_mb"], result["disk_write_mb"] = self._disk()
        if self.extra:
            try:
                result.update(self.extra())
            except Exception:  # noqa: BLE001 - telemetry must never take the server down
                pass
        return result

    def close(self):
        self._stop.set()
        thread = getattr(self, "_thread", None)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def _loop(self):
        while not self._stop.is_set():
            reading = self.sample()
            with self.lock:
                self.now = reading
                for key in (
                    "gpu_util",
                    "gpu_mem_used",
                    "gpu_temp",
                    "gpu_power",
                    "gpu_pcie_rx_mb",
                    "cpu",
                    "ram_used",
                    "disk_read_mb",
                    "tok_s",
                    "prefill_tok_s_mean",
                ):
                    value = reading.get(key)
                    self.hist[key].append(round(value, 2) if isinstance(value, float) else value)
            self._stop.wait(1.0)

    def snapshot(self):
        with self.lock:
            return {
                "now": dict(self.now),
                "history": {key: list(value) for key, value in self.hist.items()},
                "static": dict(self.static),
            }
