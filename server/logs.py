"""GET /api/logs 后端实现:按字节偏移增量读取当前任务的日志文件。

设计要点(M1 方案 §3.1):
- 游标单位是字节偏移,由调用方(前端)持有并传回,后端无状态;
- 二进制读取,next_offset = 旧 offset + 本次实际读到的字节数(精确);
- file 参数只允许三个白名单键,映射到固定文件名,杜绝路径穿越;
- 日志被新任务以 "w" 模式重建(文件变小)时,旧 offset 自动归零。
"""
import os

from core.paths import WORK_ROOT

# 白名单:逻辑键 -> 日志文件名。禁止把任意文件名/路径直接拼进路径。
LOG_FILES = {
    "extract": "extract.log",
    "upscale": "upscale.log",
    "rebuild": "rebuild.log",
}

_MAX_LINES = 1000          # 单次返回上限(防御异常增长)
_READ_CHUNK = 256 * 1024   # 单次最多读 256KB,防止日志爆炸时一次读入过大


def _log_path(pdf, file):
    """由输入 PDF 推导日志路径,与 core.pipeline 的 work/<书名>/<file> 保持一致。"""
    base = os.path.splitext(os.path.basename(pdf))[0]
    return os.path.join(WORK_ROOT, base, LOG_FILES[file])


def read(pdf, file, offset=0):
    """增量读取日志文件。

    参数:
        pdf   : STATE["pdf"],即输入 PDF 的绝对路径(可能为 None)
        file  : LOG_FILES 的白名单键("extract"/"upscale"/"rebuild")
        offset: 调用方持有的字节偏移(必须 >= 0)

    返回:
        {"file", "exists", "offset", "next_offset", "lines"}
    - pdf 为空 / 文件不存在 : exists=False, lines=[], next_offset=0;
    - offset 超过文件大小   : 视为日志被重写,offset 归零后返回全文;
    - 正常                 : 从 offset 读到文件尾,lines 为该区间按行切分的结果。
    """
    if not pdf:
        return {"file": file, "exists": False, "offset": offset,
                "next_offset": 0, "lines": []}

    path = _log_path(pdf, file)
    try:
        size = os.path.getsize(path)
    except OSError:
        return {"file": file, "exists": False, "offset": offset,
                "next_offset": 0, "lines": []}

    if offset > size:                     # 文件被重建/截断,游标失效
        offset = 0
    if offset < 0:
        offset = 0

    try:
        with open(path, "rb") as f:
            f.seek(offset)
            raw = f.read(_READ_CHUNK)
    except OSError:
        return {"file": file, "exists": True, "offset": offset,
                "next_offset": offset, "lines": []}

    next_offset = offset + len(raw)       # 字节精确,不受多字节字符影响
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if len(lines) > _MAX_LINES:
        lines = lines[-_MAX_LINES:]
    return {"file": file, "exists": True, "offset": offset,
            "next_offset": next_offset, "lines": lines}
