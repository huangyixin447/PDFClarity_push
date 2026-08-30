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

from core.paths import HERE
from server import files, jobs, logs


@asynccontextmanager
async def lifespan(_app):
    jobs.init()
    yield
    jobs.shutdown()


app = FastAPI(title="PDFClarity", version="0.4.0", lifespan=lifespan)


class JobRequest(BaseModel):
    pdf: str
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
    """创建超分任务,后台线程执行。成功返回 job_id,冲突返回 409。"""
    st = jobs.snapshot()
    if st["running"]:
        raise HTTPException(409, "已有任务在运行")
    if not st["engine_ok"]:
        raise HTTPException(400, "超分引擎缺失,无法开始(检查 bin/realesrgan/)")
    if not os.path.isfile(req.pdf) or not req.pdf.lower().endswith(".pdf"):
        raise HTTPException(400, "pdf 路径不存在或不是 PDF 文件")

    job_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    try:
        jobs.start_job(job_id, req.model_dump())
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"job_id": job_id, "status": "started"}


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
def inspect_file(path: str = Query(...)):
    """选中文件探测:存在性 / 大小 / 页数 / 成品是否已存在。"""
    return files.inspect(path)


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


# ---------- 前端静态托管(存在 ui/dist 时挂载,必须在 API 路由之后) ----------

_UI_DIST = os.path.join(HERE, "ui", "dist")
if os.path.isdir(_UI_DIST):
    app.mount("/", StaticFiles(directory=_UI_DIST, html=True), name="ui")
