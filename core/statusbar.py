"""终端配色 / TTY 判断 / 单行状态栏。内容与原 run.py 对应部分一致。"""
import os
import sys
import time

# ---- 终端配色(Windows 10+ conhost / Windows Terminal 支持 VT 转义) ----
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
IS_TTY = sys.stdout.isatty()


def enable_ansi():
    """激活 Windows 控制台的 VT 转义(无输出副作用),非 Windows 直接跳过。"""
    if os.name == "nt" and IS_TTY:
        try:
            os.system("")
        except Exception:
            pass


class StatusBar:
    """阶段进度 + 系统指标 的单行状态栏。

    TTY 下用 ``\\r`` + ``\\033[K`` 原地刷新,不清屏不刷屏;
    输出被重定向(非 TTY)时退化为每 5% 一行的纯文本,便于写入日志。
    """

    def __init__(self, refresh=0.4):
        self.refresh = refresh
        self._last = 0.0
        self._last_pct = -1

    @staticmethod
    def _bar(cur, total, width=24):
        frac = cur / total if total else 0
        filled = int(round(frac * width))
        return "[" + "█" * filled + "░" * (width - filled) + "]"

    @staticmethod
    def _num(s):
        try:
            return float(s)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _fmt_time(sec):
        sec = int(max(0, sec))
        return f"{sec // 60:02d}:{sec % 60:02d}"

    def _fmt_gpu(self, stats):
        gpu = stats.get("gpu") or {}
        if not gpu:
            return f"{DIM}GPU n/a{RESET}"
        segs = []
        multi = len(gpu) > 1
        for idx in sorted(gpu):
            e = gpu[idx]
            u = e.get("util") or 0.0
            col = GREEN if u >= 60 else (YELLOW if u >= 30 else DIM)
            label = f"GPU{idx}" if multi else "GPU"
            s = f"{col}{label} {u:3.0f}%{RESET}"
            if e.get("mem_total"):
                s += f" {e['mem_used'] / 1024:.1f}/{e['mem_total'] / 1024:.0f}G"
            temp, power = self._num(e.get("temp")), self._num(e.get("power"))
            if temp is not None:
                s += f" {temp:.0f}°C"
            if power is not None:
                s += f" {power:.0f}W"
            segs.append(s)
        return " · ".join(segs)

    def _fmt_sys(self, stats):
        cpu, ram = stats.get("cpu"), stats.get("ram")
        if cpu is None:
            cpu_s = f"{DIM}CPU n/a{RESET}"
        else:
            col = GREEN if cpu >= 60 else (YELLOW if cpu >= 30 else DIM)
            cpu_s = f"CPU {col}{cpu:3.0f}%{RESET}"
        ram_s = f"{ram:3.0f}%" if ram is not None else "n/a"
        return f"{cpu_s} · RAM {ram_s}"

    def render(self, stage, cur, total, t0, stats):
        now = time.time()
        pct = int(cur * 100 / total) if total else 0

        if not IS_TTY:                          # 重定向输出:稀疏纯文本行
            if (total is not None and cur >= total) or pct >= self._last_pct + 5:
                self._last_pct = pct
                print(f"    {stage}  {cur}/{total or '?'} ({pct}%)")
            return

        if now - self._last < self.refresh and (total is None or cur < total):
            return
        self._last = now

        line = (f"  {BOLD}{CYAN}{stage}{RESET}  {self._bar(cur, total)} "
                f"{cur}/{total or '?'} ({pct:3d}%)  "
                f"{self._fmt_gpu(stats)}  {self._fmt_sys(stats)}  "
                f"{DIM}{self._fmt_time(now - t0)}{RESET}")
        sys.stdout.write("\r" + line + "\033[K")
        sys.stdout.flush()

    def newline(self):
        if IS_TTY:
            sys.stdout.write("\r\033[K\n")
        else:
            self._last_pct = -1
        sys.stdout.flush()
