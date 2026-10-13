"""FastAPI 应用:FastAPI 骨架 + POST /api/jobs + GET /api/state + GET /api/logs。

M0 交付基础接口;M1 新增日志轮询接口与 ui/dist 静态托管;M2 新增文件探测/上传。
"""
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core import config
from core.paths import HERE, OUT_ROOT
from server import files, jobs, logs


@asynccontextmanager
async def lifespan(_app):
    jobs.init()
    yield
    jobs.shutdown()


app = FastAPI(title="PDFClarity", version="0.5.0", lifespan=lifespan)


class JobRequest(BaseModel):
    pdf: str | None = None                     # PDF 文件,或含 PDF 的文件夹(批量)
    pdfs: list[str] | None = None              # 多选/多拖:文件与文件夹混合,后端展开
    recursive: bool = False                    # 文件夹展开时:是否递归子目录
    scale: int = Field(4, ge=2, le=4)          # 2 或 4
    target_width: int = Field(4320, ge=0)       # 0 = 保留完整尺寸
    model: str = "realesrgan-x4plus"
    gpu_ids: str | None = None                 # None = 用自检自动选择结果
    extra_args: str = ""
    extract_workers: int = Field(1, ge=1)
    skip_existing: bool = True


@app.get("/api/state")
def get_state():
    """返回全局状态快照(任务进度 + 系统指标 + 自检结果)。"""
    return jobs.snapshot()


@app.post("/api/jobs")
def create_job(req: JobRequest):
    """创建超分任务(单文件或整个文件夹),后台线程执行。

    pdf 为文件夹时扫描其中的 PDF 组成批量清单(recursive 决定是否进子目录);
    为单个 PDF 时即长度 1 的批量。成功返回 job_id + 文件数,冲突返回 409。
    """
    st = jobs.snapshot()
    if st["running"]:
        raise HTTPException(409, "已有任务在运行")
    if not st["engine_ok"]:
        raise HTTPException(400, "超分引擎缺失,无法开始(检查 bin/realesrgan/)")

    if req.pdfs:                       # 多选/多拖:文件与文件夹混合,统一展开去重
        pdfs, _dirs = files.expand_inputs(req.pdfs, req.recursive)
        if not pdfs:
            raise HTTPException(400, "所选项目中没有 PDF 文件")
        input_path = None              # 多选无单一输入路径,前端据此不显示来源名
    elif req.pdf:
        input_path = req.pdf
        if os.path.isdir(req.pdf):
            pdfs = files.scan_pdfs(req.pdf, req.recursive)
            if not pdfs:
                raise HTTPException(
                    400, "该文件夹内没有 PDF 文件" + ("" if req.recursive else "(可开启「包含子文件夹」)"))
        elif os.path.isfile(req.pdf) and req.pdf.lower().endswith(".pdf"):
            pdfs = [req.pdf]
        else:
            raise HTTPException(400, "路径不存在,或不是 PDF 文件/文件夹")
    else:
        raise HTTPException(400, "未指定输入(pdf 或 pdfs 至少给一个)")

    job_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    params = req.model_dump()
    params["pdfs"] = pdfs
    params["input_path"] = input_path
    try:
        jobs.start_job(job_id, params)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"job_id": job_id, "status": "started", "count": len(pdfs)}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    """[M3] 取消任务:校验后置位 cancel_requested,子进程在下一个 0.4s 轮询内被终止。"""
    status, msg = jobs.request_cancel(job_id)
    if status != 200:
        raise HTTPException(status, msg)
    return {"detail": msg}


@app.get("/api/logs")
def get_logs(
    file: Literal["extract", "upscale", "rebuild"] = "extract",
    offset: int = Query(0, ge=0),
):
    """轮询日志:按字节偏移增量读取当前任务的日志文件。

    - file 用 Literal 限定为三个白名单键(其余值 FastAPI 自动回 422);
    - 无任务时返回空,offset 由前端持有并传回。
    """
    return logs.read(jobs.snapshot()["pdf"], file, offset)


@app.get("/api/files/inspect")
def inspect_file(path: str = Query(...), recursive: bool = Query(False)):
    """选中项探测:存在性 / 大小 / 页数 / 成品是否已存在。

    path 为文件夹时返回其中的 PDF 数量与已有成品数(recursive 决定是否进子目录)。
    """
    return files.inspect(path, recursive)


@app.post("/api/files/upload")
async def upload_file(file: UploadFile = File(...)):
    """浏览器模式上传 PDF 到 work/inbox/,返回可处理路径。

    - 仅接受 .pdf;流式写入并限制 500MB(超限删除已写部分后返回 413);
    - 重名覆盖(副本语义);桌面壳模式下本接口不使用(直接给绝对路径)。
    """
    name = os.path.basename(file.filename or "")
    if not name.lower().endswith(".pdf"):
        raise HTTPException(400, "仅支持 PDF 文件")
    os.makedirs(files.INBOX, exist_ok=True)
    dst = os.path.join(files.INBOX, name)
    size = 0
    try:
        with open(dst, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > files.MAX_UPLOAD:
                    raise HTTPException(413, "文件超过 500MB 上限")
                out.write(chunk)
    except HTTPException:
        try:
            os.remove(dst)            # 超限时删除半成品
        except OSError:
            pass
        raise
    return {"path": dst, "name": name, "size": size}


class ConfigRequest(BaseModel):
    out_mode: Literal["fixed", "source"] | None = None
    out_dir: str | None = None


@app.get("/api/config")
def get_config():
    """读取用户配置(输出位置),附带默认目录供界面「恢复默认」。"""
    cfg = config.load()
    return {**cfg, "default_out_dir": OUT_ROOT}


@app.post("/api/config")
def set_config(req: ConfigRequest):
    """更新输出位置。校验失败(目录不可创建/不可写等)返回 400。

    任务运行中不允许改,避免同一批任务的成品被写到两个目录。
    """
    if jobs.snapshot()["running"]:
        raise HTTPException(409, "任务运行中,无法修改输出位置")
    try:
        cfg = config.save(out_mode=req.out_mode, out_dir=req.out_dir)
    except ValueError as e:
        raise HTTPException(400, str(e))
    jobs.refresh_config()
    return {**cfg, "default_out_dir": OUT_ROOT}


class InspectManyRequest(BaseModel):
    paths: list[str]
    recursive: bool = False


@app.post("/api/files/inspect_many")
def inspect_many_files(req: InspectManyRequest):
    """多选/多拖探测:展开文件与文件夹后汇总 PDF 数量 / 大小 / 已有成品数。

    用 POST 而非 GET:路径可能很多且含中文,塞进 query string 易超长。
    """
    return files.inspect_many(req.paths, req.recursive)


class RevealRequest(BaseModel):
    path: str


@app.post("/api/files/reveal")
def reveal_file(req: RevealRequest):
    """在系统文件管理器中定位成品文件(浏览器模式下由本机服务执行)。

    桌面壳模式前端会直接走 pywebview bridge;本接口是浏览器模式的等价能力。
    files.reveal 内部限定仅 output/ 下的成品可被定位,越界/不存在返回 400。
    """
    if not files.reveal(req.path):
        raise HTTPException(400, "路径不存在或无法打开(仅允许 output/ 下的成品)")
    return {"detail": "ok"}


# ---------- 前端静态托管(存在 ui/dist 时挂载,必须在 API 路由之后) ----------

_UI_DIST = os.path.join(HERE, "ui", "dist")
if os.path.isdir(_UI_DIST):
    app.mount("/", StaticFiles(directory=_UI_DIST, html=True), name="ui")
