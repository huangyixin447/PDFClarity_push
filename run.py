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
"""

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE_EXE = os.path.join(HERE, "bin", "realesrgan", "realesrgan-ncnn-vulkan.exe")
ENGINE_MAC = os.path.join(HERE, "bin", "realesrgan", "realesrgan-ncnn-vulkan")
MODEL_DIR = os.path.join(HERE, "bin", "realesrgan", "models")
EXTRACT = os.path.join(HERE, "scripts", "extract_pages.py")
REBUILD = os.path.join(HERE, "scripts", "rebuild_pdf.py")
WORK_ROOT = os.path.join(HERE, "work")
OUT_ROOT = os.path.join(HERE, "output")


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


def run(cmd):
    print("    " + " ".join(cmd))
    return subprocess.run(cmd)


def upscale(src, up, log_path, model, scale, gpu_ids, extra_args):
    cmd = [find_engine(), "-i", src, "-o", up, "-n", model,
           "-s", str(scale), "-m", MODEL_DIR, "-g", gpu_ids, "-f", "png"]
    if extra_args:
        cmd += shlex.split(extra_args)
    print("    " + " ".join(cmd))
    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)

    total = count_png(src)
    done = 0
    pbar = None
    try:
        from tqdm import tqdm
        pbar = tqdm(total=total, desc="    upscale", unit="pg", ncols=72)
    except Exception:
        pbar = None

    try:
        while proc.poll() is None:
            n = count_png(up)
            if pbar is not None:
                pbar.update(max(0, n - done))
            done = n
            time.sleep(0.5)
        if pbar is not None:
            pbar.update(max(0, total - done))
            pbar.close()
    except KeyboardInterrupt:
        proc.terminate()
        raise

    if proc.returncode != 0:
        # 某些环境(杀软/驱动/沙箱)可能在页面全部写完后仍让进程非零退出;
        # 只要页面齐全就按成功处理,否则才是真正的失败。
        if count_png(up) == total and total > 0:
            print("    !! WARN: 超分进程退出码非 0,但全部页面已生成,继续")
        else:
            return proc.returncode

    # page-count consistency check: 防止缺页成品
    up_count = count_png(up)
    if up_count != total:
        print(f"    !! WARN: 超分输出 {up_count} 页 != 源 {total} 页,成品可能缺页")
    return 0


def process_one(pdf, idx=None, total=None, scale=4, target_w=4320,
                model="realesrgan-x4plus", gpu_ids="0", extra_args="",
                skip_existing=True):
    tag = f"[{idx}/{total}] " if idx else ""
    base = os.path.splitext(os.path.basename(pdf))[0]
    work = os.path.join(WORK_ROOT, base)
    src = os.path.join(work, "src")
    up = os.path.join(work, "up")
    out = os.path.join(OUT_ROOT, base + "_clear.pdf")

    if skip_existing and os.path.isfile(out):
        print(f"==> {tag}SKIP (output exists): {out}")
        return True

    print("=" * 67)
    print(f"==> {tag}Processing: {pdf}")
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

    print("  [1/3] Extracting pages")
    r = run([sys.executable, EXTRACT, pdf, src, "--zoom", "2.0"])
    if r.returncode != 0:
        print(f"  !! extract failed (exit {r.returncode})")
        return False

    print(f"  [2/3] Upscaling x{scale} with {model} (GPU: {gpu_ids})")
    log = os.path.join(work, "upscale.log")
    rc = upscale(src, up, log, model, scale, gpu_ids, extra_args)
    if rc != 0:
        print(f"  !! upscale failed (exit {rc}, see {log})")
        return False

    print(f"  [3/3] Rebuilding clean PDF (target width {target_w}px)")
    r = run([sys.executable, REBUILD, up, out, "--ref-pdf", pdf,
             "--target-width", str(target_w), "--quality", "90"])
    if r.returncode != 0:
        print(f"  !! rebuild failed (exit {r.returncode})")
        return False

    print(f"  -> Done: {out}")
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

    # Windows console / child python: 统一 UTF-8,避免中文按 GBK 输出
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

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

    if os.path.isfile(args.input):
        ok = process_one(args.input, scale=args.scale, target_w=args.target_width,
                         model=model, gpu_ids=gpu_ids, extra_args=extra_args,
                         skip_existing=skip_existing)
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
                               skip_existing=skip_existing):
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
        print("=" * 67)
        print(f"Batch finished: {ok} succeeded, {fail} failed (of {len(pdfs)}).")
        if failed:
            print("Failed files:")
            for f in failed:
                print("  -", f)
        print("Output in:", OUT_ROOT)
        sys.exit(0 if fail == 0 else 1)

    print("Input not found (not a file or folder):", args.input)
    sys.exit(1)


if __name__ == "__main__":
    main()
