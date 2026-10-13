"""GET /api/files/inspect 与 POST /api/files/upload 后端实现。

M2 界面一(选择文档)必需:
- inspect : 校验路径合法性 + 探测页数 + 成品是否存在(G1 确认框前置判断);
- upload  : 浏览器模式(开发态/无 pywebview)把本地文件上传到 work/inbox/,
            返回可被后端直接引用的绝对路径,与桌面壳「原位引用」通道统一。
"""
import os
import subprocess
import sys

from core.config import fixed_out_dir, is_source_mode, out_dir_for
from core.paths import WORK_ROOT
from core.progress import pdf_page_count

MAX_UPLOAD = 500 * 1024 * 1024     # 单文件上限 500MB(4x 超分的源 PDF 一般 < 200MB)
INBOX = os.path.join(WORK_ROOT, "inbox")


def _out_path_for(pdf):
    """推导成品路径,与 core/pipeline.process_one 的 out 计算保持一致(含输出模式)。"""
    base = os.path.splitext(os.path.basename(pdf))[0]
    return os.path.join(out_dir_for(pdf), base + "_clear.pdf")


def _batch_out_path():
    """批量(文件夹/多选)展示用的输出目录:source 模式下没有单一目录,返回 None。"""
    return None if is_source_mode() else fixed_out_dir()


def scan_pdfs(folder, recursive=False):
    """扫描文件夹内的 PDF,返回排序后的绝对路径列表(批量任务的文件清单)。

    - recursive=False: 只取该文件夹的直接子文件(不进子目录);
    - recursive=True : 递归所有子目录。
    注意成品统一平铺在 output/ 且按文件名命名,递归时不同子目录下的同名
    PDF 会指向同一成品路径(互相覆盖),由调用方/用户自行规避。
    """
    pdfs = []
    try:
        if recursive:
            for root, _dirs, names in os.walk(folder):
                for f in names:
                    if f.lower().endswith(".pdf"):
                        pdfs.append(os.path.join(root, f))
        else:
            for f in os.listdir(folder):
                p = os.path.join(folder, f)
                if f.lower().endswith(".pdf") and os.path.isfile(p):
                    pdfs.append(p)
    except OSError:
        return []
    pdfs.sort()
    return pdfs


def expand_inputs(paths, recursive=False):
    """把「拖入/选中的若干路径」展开成去重后的 PDF 清单。

    - PDF 文件 : 原样保留;
    - 文件夹   : 按 recursive 扫描其中的 PDF(scan_pdfs 内部已排序);
    - 其它     : 忽略(非 PDF、不存在、无权限)。
    去重按绝对路径,保留首次出现顺序(拖入顺序 / 文件夹内字典序)。
    返回 (pdfs, dir_count),dir_count 供前端决定是否展示「包含子文件夹」开关。
    """
    out, seen, dirs = [], set(), 0
    for p in paths or []:
        if not p or not isinstance(p, str):
            continue
        try:
            if os.path.isdir(p):
                dirs += 1
                found = scan_pdfs(p, recursive)
            elif os.path.isfile(p) and p.lower().endswith(".pdf"):
                found = [p]
            else:
                continue
        except OSError:
            continue
        for f in found:
            ap = os.path.abspath(f)
            if ap not in seen:
                seen.add(ap)
                out.append(ap)
    return out, dirs


def inspect_many(paths, recursive=False):
    """多选/多拖探测:展开后汇总 PDF 数量、总大小、已有成品数。

    形态与 inspect() 对齐(is_multi=True),out_path 取 output/ 根目录。
    """
    pdfs, dir_count = expand_inputs(paths, recursive)
    size = 0
    existing = 0
    for p in pdfs:
        try:
            size += os.path.getsize(p)
        except OSError:
            pass
        if os.path.isfile(_out_path_for(p)):
            existing += 1
    return {"exists": bool(pdfs), "is_dir": False, "is_multi": True,
            "name": f"已选 {len(pdfs)} 个 PDF", "size": size, "pages": None,
            "count": len(pdfs), "existing_count": existing,
            "dir_count": dir_count, "paths": pdfs,
            "out_path": _batch_out_path(), "out_exists": existing > 0}


def _inspect_dir(path, recursive):
    """文件夹探测:PDF 数量 / 总大小 / 已有成品数。

    不逐个打开 PDF 数页数(几十个文件会很慢),故 pages 恒为 None。
    out_path 取 output/ 根目录,使前端「在文件夹中显示」按钮可直接定位成品目录。
    """
    pdfs = scan_pdfs(path, recursive)
    size = 0
    existing = 0
    for p in pdfs:
        try:
            size += os.path.getsize(p)
        except OSError:
            pass
        if os.path.isfile(_out_path_for(p)):
            existing += 1
    return {"exists": True, "is_dir": True, "is_multi": False,
            "name": os.path.basename(os.path.normpath(path)) or path,
            "size": size, "pages": None,
            "count": len(pdfs), "existing_count": existing, "dir_count": 1,
            "out_path": _batch_out_path(), "out_exists": existing > 0}


def reveal(path):
    """在系统文件管理器中定位并选中文件/目录(浏览器模式下由本机服务执行)。

    与桌面壳 bridge.open_in_folder 行为一致,但不依赖 pywebview,故浏览器模式
    也能用(服务就跑在本机)。安全:仅允许定位 output/ 目录下的成品,防止被
    诱导打开任意路径;list 形式调用 subprocess(非 shell)避免注入。

    - macOS  : open -R <path>(定位并高亮)
    - Windows: explorer /select,<path>
    - Linux  : xdg-open 父目录(无「选中」语义,退而打开目录)
    返回 True/False;路径非法/越界/不存在/调用失败均返回 False。
    """
    if not path or not isinstance(path, str):
        return False
    try:
        ap = os.path.abspath(path)
        if not os.path.exists(ap):
            return False
        # 越界防护:放行「配置的输出目录之内」或「本程序生成的 *_clear.pdf」。
        # 后者是必要的:source 模式下成品就散落在各源 PDF 目录,不在任何固定根下。
        try:
            out_root = os.path.abspath(fixed_out_dir())
            inside = os.path.commonpath([ap, out_root]) == out_root
        except ValueError:            # Windows 跨盘符:不同根无公共前缀
            inside = False
        produced = os.path.isfile(ap) and ap.lower().endswith("_clear.pdf")
        if not (inside or produced):
            return False
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", ap])
        elif sys.platform == "win32":
            subprocess.Popen(["explorer", f"/select,{ap}"])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(ap) or "."])
        return True
    except (OSError, ValueError):
        return False


def inspect(path, recursive=False):
    """选中项探测(PDF 文件或含 PDF 的文件夹)。非法路径返回 exists=False,不抛异常。

    返回: {exists, is_dir, name, size, pages, count, existing_count,
           out_path, out_exists}
    - 文件夹: is_dir=True,count=PDF 个数,pages=None(不逐个数页,太慢);
    - 文件  : is_dir=False,count 恒为 1,pages 为探测结果(失败 None);
    - out_path/out_exists: 供前端 G1「成品已存在」确认框与「在文件夹中显示」使用。
    """
    empty = {"exists": False, "is_dir": False, "is_multi": False, "name": None,
             "size": None, "pages": None, "count": 0, "existing_count": 0,
             "dir_count": 0, "out_path": None, "out_exists": False}
    if not path or not isinstance(path, str):
        return empty
    try:
        if os.path.isdir(path):
            return _inspect_dir(path, recursive)
        if not path.lower().endswith(".pdf") or not os.path.isfile(path):
            return empty
        out = _out_path_for(path)
        exists = os.path.isfile(out)
        return {"exists": True, "is_dir": False, "is_multi": False,
                "name": os.path.basename(path),
                "size": os.path.getsize(path),
                "pages": pdf_page_count(path),
                "count": 1, "existing_count": 1 if exists else 0,
                "dir_count": 0,
                "out_path": out,
                "out_exists": exists}
    except OSError:
        return empty
