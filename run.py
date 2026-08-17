#!/usr/bin/env python3
"""PDFClarity — make low-resolution / blank-in-Skim scanned PDFs sharper.

New (v3) runner: the whole pipeline lives in pure Python, so Windows no
longer needs Git Bash.  Each PDF goes through:

    [1/3] extract_pages.py          PDF -> per-page PNGs (removes JPEG2000)
    [2/3] realesrgan-ncnn-vulkan    AI upscale on GPU (Vulkan)
    [3/3] rebuild_pdf.py            upscaled PNGs -> clean PDF

Usage:
  python run.py <input.pdf|folder> [model-scale] [target-width]

Environment overrides (names kept from the old run.sh for continuity):
  MODEL          upscale model name              (default: realesrgan-x4plus)
  RECURSIVE      "1" = also scan sub-folders in batch mode  (default: 0)
  SKIP_EXISTING  "1" = skip PDFs whose output exists        (default: 1)
  GPU_IDS        ncnn gpu id(s), e.g. "0" or "0,1"          (default: auto-pick 独显)
  EXTRA_ARGS     extra ncnn args, e.g. "-j 1:2,2:2"
  EXTRACT_WORKERS 提取页面并行进程数 (默认 min(8, cpu_count), 1 = 单进程)

输出说明:
  用单行实时状态栏显示进度,并叠加实时系统指标:
      进度条 页码(百分比) | GPU 占用·显存·温度·功耗 | CPU/内存 | 阶段用时
  GPU 数据来自 nvidia-smi(仅 NVIDIA;AMD/纯集显显示 n/a),CPU/内存来自 psutil。
  每个 PDF 结束打印 总用时、每页耗时 与 GPU/CPU 峰值,便于排查 GPU 占用率问题。
"""

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE_EXE = os.path.join(HERE, "bin", "realesrgan", "realesrgan-ncnn-vulkan.exe")
ENGINE_MAC = os.path.join(HERE, "bin", "realesrgan", "realesrgan-ncnn-vulkan")
MODEL_DIR = os.path.join(HERE, "bin", "realesrgan", "models")
EXTRACT = os.path.join(HERE, "scripts", "extract_pages.py")
REBUILD = os.path.join(HERE, "scripts", "rebuild_pdf.py")
WORK_ROOT = os.path.join(HERE, "work")
OUT_ROOT = os.path.join(HERE, "output")

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


def env_bool(name, default):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def find_engine():
    if os.path.isfile(ENGINE_EXE):
        return ENGINE_EXE
    if os.path.isfile(ENGINE_MAC):
        return ENGINE_MAC
    return None


GPU_RE = re.compile(r"^\s*\[\s*(\d+)\s+(.*?)\]\s+queue", re.MULTILINE)


def probe_gpus(engine):
    """Run the engine once on a tiny image and read its Vulkan device list.

    The engine prints ``[<id> <name>]  queue...`` for *every* device on any
    real run, so one cheap probe is enough to enumerate them (Windows laptops
    often enumerate the slow iGPU first, e.g. AMD Radeon before an NVIDIA GPU).
    """
    probe_in = os.path.join(HERE, "work", ".gpu_probe_src")
    probe_out = os.path.join(HERE, "work", ".gpu_probe_out")
    os.makedirs(probe_in, exist_ok=True)
    os.makedirs(probe_out, exist_ok=True)
    px = os.path.join(probe_in, "p.png")
    if not os.path.isfile(px):
        try:
            from PIL import Image
            Image.new("RGB", (4, 4), (128, 128, 128)).save(px)
        except Exception:
            return None
    cmd = [engine, "-i", probe_in, "-o", probe_out, "-n", "realesrgan-x4plus",
           "-s", "4", "-m", MODEL_DIR, "-g", "0", "-f", "png"]
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=120)
        text = r.stdout.decode("utf-8", errors="replace")
    except Exception:
        return None
    return [(int(m.group(1)), m.group(2).strip()) for m in GPU_RE.finditer(text)]


def pick_gpu(gpus):
    """Prefer a discrete NVIDIA GPU over integrated graphics."""

    def score(name):
        n = name.lower()
        if "nvidia" in n or "geforce" in n or "rtx" in n or "gtx" in n:
            return 4
        if "radeon" in n and "graphics" not in n:
            return 3
        if "graphics" not in n and "uhd" not in n and "iris" not in n:
            return 2
        return 1

    return max(gpus, key=lambda g: (score(g[1]), -g[0]))[0]


def count_png(d):
    if not os.path.isdir(d):
        return 0
    return len([f for f in os.listdir(d) if f.lower().endswith(".png")])


def count_tmp_jpg(out_root):
    """rebuild_pdf.py 边写页边在 output/_jpg_tmp_<pid>/ 生成临时 jpg,统计其数量即进度。"""
    n = 0
    try:
        for d in os.listdir(out_root):
            if d.startswith("_jpg_tmp_"):
                p = os.path.join(out_root, d)
                if os.path.isdir(p):
                    n += len([f for f in os.listdir(p) if f.lower().endswith(".jpg")])
    except OSError:
        pass
    return n


def pdf_page_count(pdf):
    """读取 PDF 页数用于进度百分比;失败返回 None(状态栏退化为只显示页码)。"""
    try:
        import pymupdf as fitz          # pymupdf>=1.24 起推荐,免 fitz 弃用告警
    except ImportError:
        try:
            import fitz
        except ImportError:
            return None
    try:
        d = fitz.open(pdf)
        n = d.page_count
        d.close()
        return n
    except Exception:
        return None


# ---- 实时系统监控(GPU / CPU / 内存) ---------------------------------------
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


# ---- 单行实时状态栏 -------------------------------------------------------
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


def run_with_progress(cmd, log_path, count_fn, total, stage, monitor, bar):
    """后台跑子进程(输出进日志),状态栏按 count_fn 轮询展示进度与系统指标。"""
    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
    t0 = time.time()
    while proc.poll() is None:
        bar.render(stage, count_fn(), total, t0, monitor.sample())
        time.sleep(0.4)
    bar.render(stage, count_fn(), total, t0, monitor.sample())
    bar.newline()
    return proc.returncode


def upscale(src, up, log_path, model, scale, gpu_ids, extra_args, monitor, bar):
    cmd = [find_engine(), "-i", src, "-o", up, "-n", model,
           "-s", str(scale), "-m", MODEL_DIR, "-g", gpu_ids, "-f", "png"]
    if extra_args:
        cmd += shlex.split(extra_args)
    print("    " + " ".join(cmd))
    total = count_png(src)
    rc = run_with_progress(cmd, log_path, lambda: count_png(up), total,
                           f"超分 x{scale}", monitor, bar)
    if rc != 0:
        # 某些环境(杀软/驱动/沙箱)可能在页面全部写完后仍让进程非零退出;
        # 只要页面齐全就按成功处理,否则才是真正的失败。
        if count_png(up) == total and total > 0:
            print("    !! WARN: 超分进程退出码非 0,但全部页面已生成,继续")
        else:
            return rc
    # page-count consistency check: 防止缺页成品
    up_count = count_png(up)
    if up_count != total:
        print(f"    !! WARN: 超分输出 {up_count} 页 != 源 {total} 页,成品可能缺页")
    return 0


def process_one(pdf, idx=None, total=None, scale=4, target_w=4320,
                model="realesrgan-x4plus", gpu_ids="0", extra_args="",
                skip_existing=True, extract_workers=1, monitor=None, bar=None):
    tag = f"[{idx}/{total}] " if idx else ""
    base = os.path.splitext(os.path.basename(pdf))[0]
    work = os.path.join(WORK_ROOT, base)
    src = os.path.join(work, "src")
    up = os.path.join(work, "up")
    out = os.path.join(OUT_ROOT, base + "_clear.pdf")

    if skip_existing and os.path.isfile(out):
        print(f"==> {tag}SKIP (output exists): {out}")
        return True

    print("=" * 72)
    print(f"==> {tag}Processing: {pdf}", flush=True)
    os.makedirs(src, exist_ok=True)
    os.makedirs(up, exist_ok=True)
    os.makedirs(OUT_ROOT, exist_ok=True)

    # 清理上次运行可能残留的中间页(中断/重跑时旧页会混进成品,导致错图/多页)
    for d in (src, up):
        for f in os.listdir(d):
            if f.lower().endswith((".png", ".jpg", ".jpeg")):
                try:
                    os.remove(os.path.join(d, f))
                except OSError:
                    pass

    monitor.reset_peaks()
    t_start = time.time()
    pages = pdf_page_count(pdf)                # 可能为 None

    print(f"  [1/3] 提取页面 (zoom 2.0, {extract_workers} 进程)", flush=True)
    rc = run_with_progress([sys.executable, EXTRACT, pdf, src, "--zoom", "2.0",
                            "--workers", str(extract_workers)],
                           os.path.join(work, "extract.log"),
                           lambda: count_png(src), pages, "提取", monitor, bar)
    if rc != 0:
        print(f"  !! 提取失败 (exit {rc}, 见 {os.path.join(work, 'extract.log')})")
        return False

    print(f"  [2/3] 超分 x{scale} ({model}, GPU: {gpu_ids})", flush=True)
    rc = upscale(src, up, os.path.join(work, "upscale.log"), model, scale,
                 gpu_ids, extra_args, monitor, bar)
    if rc != 0:
        print(f"  !! 超分失败 (exit {rc}, 见 {os.path.join(work, 'upscale.log')})")
        return False

    print(f"  [3/3] 重建 PDF (目标宽度 {target_w}px)", flush=True)
    rc = run_with_progress([sys.executable, REBUILD, up, out, "--ref-pdf", pdf,
                            "--target-width", str(target_w), "--quality", "90"],
                           os.path.join(work, "rebuild.log"),
                           lambda: count_tmp_jpg(OUT_ROOT), pages, "重建", monitor, bar)
    if rc != 0:
        print(f"  !! 重建失败 (exit {rc}, 见 {os.path.join(work, 'rebuild.log')})")
        return False

    dt = time.time() - t_start
    peaks = monitor.sample()["peaks"]
    per_page = f"{dt / pages:.1f}s" if pages else "n/a"
    print(f"  -> 完成: {out}")
    print(f"     用时 {dt:.1f}s | 每页 {per_page} | "
          f"峰值 GPU {peaks['gpu']:.0f}% / CPU {peaks['cpu']:.0f}%")
    return True


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="input PDF file, or folder of PDFs (batch)")
    ap.add_argument("scale", nargs="?", type=int, default=4,
                    help="upscale factor (2 or 4, default 4)")
    ap.add_argument("target_width", nargs="?", type=int, default=4320,
                    help="final page width in px; 0 = keep full upscaled size (default 4320)")
    args = ap.parse_args()

    model = os.environ.get("MODEL", "realesrgan-x4plus")
    recursive = env_bool("RECURSIVE", False)
    skip_existing = env_bool("SKIP_EXISTING", True)
    gpu_ids = os.environ.get("GPU_IDS")
    extra_args = os.environ.get("EXTRA_ARGS", "")
    try:
        extract_workers = int(os.environ.get(
            "EXTRACT_WORKERS", str(min(8, os.cpu_count() or 4))))
    except ValueError:
        extract_workers = 1
    extract_workers = max(1, extract_workers)

    # Windows console / child python: 统一 UTF-8,避免中文按 GBK 输出
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    enable_ansi()

    engine = find_engine()
    if engine is None:
        print("!! 找不到超分引擎:")
        print("   - Windows: 下载官方 Windows 版并放入 bin/realesrgan/:")
        print("     https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-windows.zip")
        print("   - macOS: 确认 bin/realesrgan/realesrgan-ncnn-vulkan 存在且可执行 (chmod +x)")
        sys.exit(1)

    # GPU 自动选择:未显式设置 GPU_IDS 时,枚举 Vulkan 设备并优先选独显(NVIDIA)
    if gpu_ids is None:
        gpus = probe_gpus(engine)
        if gpus:
            picked = str(pick_gpu(gpus))
            names = ", ".join(f"[{i} {n}]" for i, n in gpus)
            print(f"==> 检测到 GPU: {names}")
            if picked != "0":
                print(f"==> 自动选择 GPU {picked} (默认 0 通常是集显;可用 GPU_IDS 覆盖)")
            gpu_ids = picked
        else:
            print("!! 未枚举到 Vulkan 设备(探测失败),回退 GPU 0;"
                  "若占用率上不去,请检查显卡驱动 / 用 GPU_IDS 指定")
            gpu_ids = "0"

    # GPU/驱动预检(警示级,不阻断;权威判断交给引擎自身报错)
    if shutil.which("nvidia-smi"):
        print("==> NVIDIA GPU:", flush=True)
        try:
            subprocess.run(["nvidia-smi", "-L"], check=True)
        except Exception:
            pass
    else:
        print("!! 未检测到 nvidia-smi(仅警示,不阻断;AMD/Intel 走 Vulkan 可忽略,macOS 可忽略)")

    monitor = SysMonitor()
    bar = StatusBar()
    try:
        if os.path.isfile(args.input):
            ok = process_one(args.input, scale=args.scale, target_w=args.target_width,
                             model=model, gpu_ids=gpu_ids, extra_args=extra_args,
                             skip_existing=skip_existing,
                             extract_workers=extract_workers,
                             monitor=monitor, bar=bar)
            print()
            print("All done. Output in:", OUT_ROOT)
            sys.exit(0 if ok else 1)

        if os.path.isdir(args.input):
            pdfs = []
            for root, dirs, files in os.walk(args.input):
                if not recursive and root != args.input:
                    dirs[:] = []          # top-level only unless RECURSIVE=1
                for f in files:
                    if f.lower().endswith(".pdf"):
                        pdfs.append(os.path.join(root, f))
            pdfs.sort()
            if not pdfs:
                print("No PDF files found in:", args.input)
                sys.exit(1)
            print(f"Found {len(pdfs)} PDF(s) in: {args.input}")
            if recursive:
                print("(recursive scan on)")

            ok = fail = 0
            failed = []
            for i, f in enumerate(pdfs, 1):
                try:
                    if process_one(f, idx=i, total=len(pdfs), scale=args.scale,
                                   target_w=args.target_width, model=model,
                                   gpu_ids=gpu_ids, extra_args=extra_args,
                                   skip_existing=skip_existing,
                                   extract_workers=extract_workers,
                                   monitor=monitor, bar=bar):
                        ok += 1
                    else:
                        fail += 1
                        failed.append(f)
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    fail += 1
                    failed.append(f)
                    print(f"  !! FAILED: {f} ({e})")

            print()
            print("=" * 72)
            print(f"Batch finished: {ok} succeeded, {fail} failed (of {len(pdfs)}).")
            if failed:
                print("Failed files:")
                for f in failed:
                    print("  -", f)
            print("Output in:", OUT_ROOT)
            sys.exit(0 if fail == 0 else 1)

        print("Input not found (not a file or folder):", args.input)
        sys.exit(1)
    finally:
        monitor.close()


if __name__ == "__main__":
    main()
