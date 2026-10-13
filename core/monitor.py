"""实时系统监控(GPU / CPU / 内存)。内容与原 run.py SysMonitor 一致。"""
import shutil
import subprocess
import threading


class SysMonitor:
    """后台线程采样系统指标,供状态栏实时读取。

    - GPU : 常驻一个 ``nvidia-smi --query-gpu=... -l 1`` 子进程,后台线程按行解析,
            每秒得到每块 NVIDIA 卡的 占用%/显存/温度/功耗;
    - CPU/内存 : psutil 采样线程(每 1s);
    - 任一来源不可用(无 nvidia-smi / 无 psutil)时对应字段为 None,状态栏显示 n/a。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._gpu = {}                       # idx -> dict(util, mem_used, mem_total, temp, power)
        self._cpu = None
        self._ram = None
        self._peaks = {"gpu": 0.0, "cpu": 0.0}
        self._stop = threading.Event()
        self._proc = None

        self._psutil = None
        try:
            import psutil as _p
            self._psutil = _p
        except Exception:
            pass

        nvsmi = shutil.which("nvidia-smi")
        if nvsmi:
            cmd = [nvsmi, "--query-gpu=index,utilization.gpu,memory.used,memory.total,"
                          "temperature.gpu,power.draw",
                   "--format=csv,noheader,nounits", "-l", "1"]
            try:
                self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                              stderr=subprocess.DEVNULL,
                                              text=True, encoding="utf-8",
                                              errors="replace", bufsize=1)
                threading.Thread(target=self._gpu_reader, daemon=True).start()
            except Exception:
                self._proc = None

        if self._psutil is not None:
            self._psutil.cpu_percent(None)   # 预热,让第一次采样有意义
            threading.Thread(target=self._sys_reader, daemon=True).start()

    @staticmethod
    def _f(s, default):
        try:
            return float(s)
        except (TypeError, ValueError):
            return default

    def _gpu_reader(self):
        while not self._stop.is_set():
            line = self._proc.stdout.readline()
            if not line:
                break
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 6:
                continue
            try:
                idx = int(parts[0])
            except ValueError:
                continue
            entry = {
                "util": self._f(parts[1], 0.0),
                "mem_used": self._f(parts[2], 0.0),    # MiB
                "mem_total": self._f(parts[3], 0.0),   # MiB
                "temp": self._f(parts[4], None),
                "power": self._f(parts[5], None),
            }
            with self._lock:
                self._gpu[idx] = entry
                self._peaks["gpu"] = max(self._peaks["gpu"], entry["util"])

    def _sys_reader(self):
        p = self._psutil
        while not self._stop.is_set():
            if self._stop.wait(1.0):
                break
            try:
                cpu = p.cpu_percent(None)
                ram = p.virtual_memory().percent
            except Exception:
                continue
            with self._lock:
                self._cpu, self._ram = cpu, ram
                self._peaks["cpu"] = max(self._peaks["cpu"], cpu)

    def sample(self):
        with self._lock:
            return {"gpu": dict(self._gpu), "cpu": self._cpu, "ram": self._ram,
                    "peaks": dict(self._peaks)}

    def reset_peaks(self):
        with self._lock:
            self._peaks = {"gpu": 0.0, "cpu": 0.0}

    def close(self):
        self._stop.set()
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
