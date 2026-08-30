"""流水线调度:run_with_progress / upscale / process_one。

相对旧 run.py 的改动:
- 三个函数均新增可选回调参数,CLI 传 None 时行为与旧版完全一致;
- run_with_progress 新增 phase(规范阶段键)参数,用于回调携带稳定标识;
- process_one 新增 on_stage / on_progress / on_done 三个回调。
"""
import os
import shlex
import signal
import subprocess
import sys
import time

from core.paths import WORK_ROOT, OUT_ROOT, MODEL_DIR, EXTRACT, REBUILD
from core.gpu import find_engine
from core.progress import count_png, count_tmp_jpg, pdf_page_count


def run_with_progress(cmd, log_path, count_fn, total, stage, monitor=None,
                      bar=None, phase=None, on_progress=None, should_stop=None):
    """后台跑子进程(输出进日志),状态栏按 count_fn 轮询展示进度与系统指标。

    - bar 为 None 时跳过终端状态栏渲染(GUI 模式);
    - on_progress(phase, cur, total) 供 GUI 写全局状态字典;
    - should_stop(): 每 0.4s 检查,返回 True 表示已请求取消 → 终止子进程树并返回 None。
    """
    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
    t0 = time.time()
    canceled = False
    while proc.poll() is None:
        if should_stop is not None and should_stop():
            canceled = True
            _kill_tree(proc)          # terminate → 3s 未退 → taskkill /T /F
            proc.wait(timeout=10)
            break
        cur = count_fn()
        if bar is not None and monitor is not None:
            bar.render(stage, cur, total, t0, monitor.sample())
        if on_progress is not None:
            on_progress(phase or stage, cur, total)
        time.sleep(0.4)
    cur = count_fn()
    if bar is not None and monitor is not None:
        bar.render(stage, cur, total, t0, monitor.sample())
        bar.newline()
    if on_progress is not None:
        on_progress(phase or stage, cur, total)
    return None if canceled else proc.returncode


def _kill_tree(proc):
    """终止子进程树:terminate() 只杀主进程,extract_pages --workers 8 的
    进程池/rebuild 子进程需连根杀。Windows 用 taskkill /T /F;
    POSIX(macOS/Linux)用 killpg 杀整个进程组。"""
    proc.terminate()
    try:
        proc.wait(timeout=3)
        return
    except subprocess.TimeoutExpired:
        pass
    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, timeout=10)
        except OSError:
            pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass


def upscale(src, up, log_path, model, scale, gpu_ids, extra_args, monitor=None,
            bar=None, phase="upscale", on_progress=None, should_stop=None):
    cmd = [find_engine(), "-i", src, "-o", up, "-n", model,
           "-s", str(scale), "-m", MODEL_DIR, "-g", gpu_ids, "-f", "png"]
    if extra_args:
        cmd += shlex.split(extra_args)
    print("    " + " ".join(cmd))
    total = count_png(src)
    rc = run_with_progress(cmd, log_path, lambda: count_png(up), total,
                           f"超分 x{scale}", monitor, bar, phase, on_progress,
                           should_stop)
    if rc is None:                     # 取消
        return None
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
                skip_existing=True, extract_workers=1, monitor=None, bar=None,
                on_stage=None, on_progress=None, on_done=None,
                should_stop=None, on_cancel=None):
    """处理单个 PDF,返回 bool。

    回调约定(均为可选,None 时不影响任何原有打印/行为):
    - on_stage(phase, label) : 阶段切换,phase ∈ {extract, upscale, rebuild}
    - on_progress(phase, cur, total) : 每次轮询(约 0.4s)
    - on_done(ok, out_path, elapsed, per_page) : 任务结束(含 SKIP 分支)
    - should_stop() : 返回 True 表示已请求取消(每阶段轮询与阶段间隙均检查);
    - on_cancel() : 取消落定后调用(不再调 on_done),由调用方写终态。
    """
    tag = f"[{idx}/{total}] " if idx else ""
    base = os.path.splitext(os.path.basename(pdf))[0]
    work = os.path.join(WORK_ROOT, base)
    src = os.path.join(work, "src")
    up = os.path.join(work, "up")
    out = os.path.join(OUT_ROOT, base + "_clear.pdf")

    if skip_existing and os.path.isfile(out):
        print(f"==> {tag}SKIP (output exists): {out}")
        if on_done is not None:
            on_done(True, out, 0.0, "skip")
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

    if monitor is not None:
        monitor.reset_peaks()
    t_start = time.time()
    pages = pdf_page_count(pdf)                # 可能为 None

    def canceled():
        return should_stop is not None and should_stop()

    def stage(phase, label):
        if on_stage is not None:
            on_stage(phase, label)

    if canceled():                      # 开工前取消兜底
        if on_cancel is not None:
            on_cancel()
        return False

    stage("extract", "提取")
    print(f"  [1/3] 提取页面 (zoom 2.0, {extract_workers} 进程)", flush=True)
    rc = run_with_progress([sys.executable, EXTRACT, pdf, src, "--zoom", "2.0",
                            "--workers", str(extract_workers)],
                           os.path.join(work, "extract.log"),
                           lambda: count_png(src), pages, "提取", monitor, bar,
                           "extract", on_progress, should_stop)
    if rc is None:
        if on_cancel is not None:
            on_cancel()
        return False
    if rc != 0:
        print(f"  !! 提取失败 (exit {rc}, 见 {os.path.join(work, 'extract.log')})")
        if on_done is not None:
            on_done(False, None, time.time() - t_start, None)
        return False

    if canceled():                      # 阶段间隙取消兜底
        if on_cancel is not None:
            on_cancel()
        return False

    stage("upscale", f"超分 x{scale}")
    print(f"  [2/3] 超分 x{scale} ({model}, GPU: {gpu_ids})", flush=True)
    rc = upscale(src, up, os.path.join(work, "upscale.log"), model, scale,
                 gpu_ids, extra_args, monitor, bar, "upscale", on_progress,
                 should_stop)
    if rc is None:
        if on_cancel is not None:
            on_cancel()
        return False
    if rc != 0:
        print(f"  !! 超分失败 (exit {rc}, 见 {os.path.join(work, 'upscale.log')})")
        if on_done is not None:
            on_done(False, None, time.time() - t_start, None)
        return False

    if canceled():
        if on_cancel is not None:
            on_cancel()
        return False

    stage("rebuild", "重建")
    print(f"  [3/3] 重建 PDF (目标宽度 {target_w}px)", flush=True)
    rc = run_with_progress([sys.executable, REBUILD, up, out, "--ref-pdf", pdf,
                            "--target-width", str(target_w), "--quality", "90"],
                           os.path.join(work, "rebuild.log"),
                           lambda: count_tmp_jpg(OUT_ROOT), pages, "重建", monitor,
                           bar, "rebuild", on_progress, should_stop)
    if rc is None:
        if on_cancel is not None:
            on_cancel()
        return False
    if rc != 0:
        print(f"  !! 重建失败 (exit {rc}, 见 {os.path.join(work, 'rebuild.log')})")
        if on_done is not None:
            on_done(False, None, time.time() - t_start, None)
        return False

    dt = time.time() - t_start
    peaks = monitor.sample()["peaks"] if monitor is not None else None
    per_page = f"{dt / pages:.1f}s" if pages else "n/a"
    print(f"  -> 完成: {out}")
    if peaks is not None:
        print(f"     用时 {dt:.1f}s | 每页 {per_page} | "
              f"峰值 GPU {peaks['gpu']:.0f}% / CPU {peaks['cpu']:.0f}%")
    else:
        print(f"     用时 {dt:.1f}s | 每页 {per_page}")
    if on_done is not None:
        on_done(True, out, dt, per_page)
    return True
