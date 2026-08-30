"""任务调度:全局状态字典 + 后台任务线程。

线程模型(设计文档 4.3 S3):
- 任务线程(daemon) 跑 process_one,通过回调写 STATE;
- 指标采样线程 每 1s 把 SysMonitor.sample() 快照写入 STATE;
- HTTP 处理器只读 snapshot()(深拷贝),写读均由 threading.Lock 保护。
"""
import copy
import glob
import os
import shutil
import threading
import time

from core import SysMonitor, find_engine, probe_gpus, pick_gpu, process_one
from core.paths import OUT_ROOT

_lock = threading.Lock()
STATE = {
    "running": False,          # 是否有任务在运行
    "job_id": None,
    "pdf": None,
    "phase": "idle",           # idle | extract | upscale | rebuild | done | error
    "stage_label": "",         # 供界面显示的中文阶段名
    "page": 0,                 # 当前完成页数
    "total": None,             # 总页数(探测失败为 None)
    "percent": 0,
    "message": "",
    "output_path": None,
    "error": None,
    "started_at": None,
    "ended_at": None,                    # [M3 新增] done/error/canceled 时写入,前端算任务总用时
    "cancel_requested": False,           # [M3 新增] 取消路由置位,任务线程 0.4s 内检出
    "cpu_count": os.cpu_count() or 4,        # [M2 新增] 前端参数区 extract_workers 校验上限
    # 系统指标快照(由 _metrics_loop 周期写入)
    "metrics": {"gpu": {}, "cpu": None, "ram": None,
                "peaks": {"gpu": 0.0, "cpu": 0.0}},
    # 自检结果(S7)
    "gpus": [],                # [(idx, name), ...]
    "gpu_ids": None,           # 自动选择结果;POST /api/jobs 未指定时使用
    "engine_ok": False,
    "nvidia_smi": False,
}

monitor = None                 # SysMonitor 单例,init() 中创建


# —— M3 新增:三大项目加权(用户指定 20/70/10,进度条仅作心理安慰) ——
# 总进度 = 前序阶段已完成部分(_STAGE_BASE) + 本阶段权重(_STAGE_W) × 本阶段完成度
_STAGE_W = {"extract": 0.20, "upscale": 0.70, "rebuild": 0.10}
_STAGE_BASE = {"extract": 0.00, "upscale": 0.20, "rebuild": 0.90}
_STAGE_TAG = {"extract": "提取", "upscale": "超分", "rebuild": "重建"}


def _weighted_percent(phase, cur, total):
    """按 20/70/10 加权换算全局进度百分比(0~100)。total 探测失败按 0。"""
    w = _STAGE_W.get(phase, 0.0)
    base = _STAGE_BASE.get(phase, 0.0)
    frac = (cur / total) if total else 0.0
    return int(round((base + w * frac) * 100))


def _stage_message(phase, cur, total):
    """进度条中间文字: `[类型] 正在xxx第 cur/total 页`。
    多进程提取时 on_progress 由单线程轮询 count_fn 汇总推进(天然顺序覆盖,即「随便选一条」)。"""
    tag = _STAGE_TAG.get(phase, phase)
    if total:
        return f"[{tag}] 正在{tag}第 {cur}/{total} 页"
    return f"[{tag}] 正在{tag}…(页数未知,按推进估算)"


def _update(**kw):
    with _lock:
        STATE.update(kw)


def snapshot():
    """返回 STATE 的深拷贝,供 HTTP 层返回。"""
    with _lock:
        return copy.deepcopy(STATE)


def init():
    """应用启动时调用一次:统一子进程编码 + 创建监控实例 + 自检 + 指标线程。

    同步执行自检(而非丢进后台线程):保证服务开始接收请求前 STATE 已就绪,
    避免出现「请求先到、engine_ok 还是 False」的竞态。
    """
    global monitor
    # 子进程(extract_pages.py / rebuild_pdf.py)继承本环境:
    # 若不设置,Windows 中文系统下子进程在管道模式按 GBK 写 stdout,
    # 进入 UTF-8 日志文件后变成 \ufffd 乱码(CLI 的 run.py 已设置,此处补齐)。
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    monitor = SysMonitor()
    self_check()
    threading.Thread(target=_metrics_loop, daemon=True).start()


def shutdown():
    """应用退出时调用:释放 SysMonitor 的 nvidia-smi 子进程。"""
    global monitor
    if monitor is not None:
        monitor.close()
        monitor = None


def self_check():
    """S7:引擎存在性 / GPU 枚举与自动选择 / nvidia-smi 可用性。"""
    engine = find_engine()
    ok = engine is not None
    gpus = probe_gpus(engine) if ok else []
    gpu_ids = str(pick_gpu(gpus)) if gpus else ("0" if ok else None)
    _update(engine_ok=ok, gpus=gpus, gpu_ids=gpu_ids,
            nvidia_smi=shutil.which("nvidia-smi") is not None)


def _metrics_loop():
    while True:
        time.sleep(1.0)
        if monitor is None:
            continue
        _update(metrics=monitor.sample())


def start_job(job_id, params):
    """启动任务线程。调用前需已通过 snapshot() 校验未在运行。

    关键点:必须在持锁时同步置 running=True,再 spawn 线程。
    若留到子线程里再置位,两次 POST 在「spawn 后、子线程首行代码前」
    的窗口内都会通过校验,导致两个任务线程同时处理同一份 work/ 目录。
    """
    with _lock:
        if STATE["running"]:
            raise RuntimeError("已有任务在运行")
        STATE.update(
            running=True, job_id=job_id, pdf=params["pdf"],
            phase="extract", stage_label="提取", page=0, total=None, percent=0,
            message="开始处理", error=None, output_path=None,
            started_at=time.time(), ended_at=None, cancel_requested=False)
    threading.Thread(target=_job_thread, args=(job_id, params), daemon=True).start()


def request_cancel(job_id):
    """[M3] POST /api/jobs/{id}/cancel 的入口。校验后置位,由任务线程 0.4s 内检出。"""
    with _lock:
        if not STATE["running"]:
            return 409, "当前没有正在运行的任务"
        if STATE["job_id"] != job_id:
            return 404, f"任务 {job_id} 不存在或已结束"
        STATE["cancel_requested"] = True
    return 200, "已请求取消,正在终止…"


def _cleanup_jpg_tmp():
    """[M3] 取消后清理 rebuild 中断残留的 output/_jpg_tmp_* 目录(幂等)。"""
    for d in glob.glob(os.path.join(OUT_ROOT, "_jpg_tmp_*")):
        shutil.rmtree(d, ignore_errors=True)


def _job_thread(job_id, params):
    pdf = params["pdf"]

    def on_stage(phase, label):
        _update(phase=phase, stage_label=label,
                message=f"[{_STAGE_TAG.get(phase, label)}] 开始{_STAGE_TAG.get(phase, label)}…",
                error=None)

    def on_progress(phase, cur, total):
        _update(phase=phase, page=cur, total=total,
                percent=_weighted_percent(phase, cur, total),
                message=_stage_message(phase, cur, total))

    def on_done(ok, out_path, elapsed, per_page):
        _update(running=False,
                phase="done" if ok else "error",
                output_path=out_path,
                percent=100 if ok else STATE["percent"],   # done 满格/error 定格
                message="完成" if ok else "失败",
                error=None if ok else "阶段失败,详见 work/<书名>/*.log",
                ended_at=time.time())

    def on_cancel():
        # [M3] 取消:先清理 rebuild 中断可能残留的临时 jpg(幂等),再落终态
        _cleanup_jpg_tmp()
        _update(running=False, phase="canceled", stage_label="已取消",
                message="已取消", error=None, ended_at=time.time())

    def should_stop():
        with _lock:
            return STATE["cancel_requested"]

    try:
        gpu_ids = params.get("gpu_ids")
        if not gpu_ids:
            with _lock:
                gpu_ids = STATE["gpu_ids"] or "0"
        process_one(
            pdf,
            scale=params.get("scale", 4),
            target_w=params.get("target_width", 4320),
            model=params.get("model", "realesrgan-x4plus"),
            gpu_ids=gpu_ids,
            extra_args=params.get("extra_args", ""),
            skip_existing=params.get("skip_existing", True),
            extract_workers=params.get("extract_workers", 1),
            monitor=monitor,
            bar=None,                          # GUI 无终端状态栏
            on_stage=on_stage,
            on_progress=on_progress,
            on_done=on_done,
            should_stop=should_stop,        # [M3]
            on_cancel=on_cancel,            # [M3]
        )
    except Exception as e:
        _update(running=False, phase="error",
                error=str(e), message=f"任务异常: {e}")
