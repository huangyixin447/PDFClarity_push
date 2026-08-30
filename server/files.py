"""GET /api/files/inspect 与 POST /api/files/upload 后端实现。

M2 界面一(选择文档)必需:
- inspect : 校验路径合法性 + 探测页数 + 成品是否存在(G1 确认框前置判断);
- upload  : 浏览器模式(开发态/无 pywebview)把本地文件上传到 work/inbox/,
            返回可被后端直接引用的绝对路径,与桌面壳「原位引用」通道统一。
"""
import os

from core.paths import WORK_ROOT, OUT_ROOT
from core.progress import pdf_page_count

MAX_UPLOAD = 500 * 1024 * 1024     # 单文件上限 500MB(4x 超分的源 PDF 一般 < 200MB)
INBOX = os.path.join(WORK_ROOT, "inbox")


def _out_path_for(pdf):
    """推导成品路径,与 core/pipeline.process_one 的 out 计算保持一致。"""
    base = os.path.splitext(os.path.basename(pdf))[0]
    return os.path.join(OUT_ROOT, base + "_clear.pdf")


def inspect(path):
    """选中文件探测。路径非法时返回 exists=False,不抛异常(前端直接展示)。

    返回: {exists, name, size, pages, out_path, out_exists}
    - pages: pdf_page_count 结果,探测失败为 None;
    - out_path/out_exists: 供前端 G1「成品已存在」确认框做前置判断。
    """
    empty = {"exists": False, "name": None, "size": None, "pages": None,
             "out_path": None, "out_exists": False}
    if not path or not isinstance(path, str) or not path.lower().endswith(".pdf"):
        return empty
    try:
        if not os.path.isfile(path):
            return empty
        out = _out_path_for(path)
        return {"exists": True, "name": os.path.basename(path),
                "size": os.path.getsize(path),
                "pages": pdf_page_count(path),
                "out_path": out,
                "out_exists": os.path.isfile(out)}
    except OSError:
        return empty
