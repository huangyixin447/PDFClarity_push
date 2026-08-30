"""进度计数与页数探测(全部为纯函数,无状态)。"""
import os


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
