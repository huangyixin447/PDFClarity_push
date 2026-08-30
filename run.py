#!/usr/bin/env python3
"""PDFClarity — make low-resolution / blank-in-Skim scanned PDFs sharper.

CLI 入口。核心逻辑已抽入 core/ 模块,本文件只保留参数解析与批处理编排,
行为与旧版完全一致(含单行状态栏与退出码)。

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
import shutil
import subprocess
import sys

from core import (find_engine, probe_gpus, pick_gpu, prepare_engine, SysMonitor,
                  StatusBar, enable_ansi, process_one, OUT_ROOT)


def env_bool(name, default):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


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
    prepare_engine(engine)          # macOS: 清除 quarantine + 确保可执行(替代原 run.sh)

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
