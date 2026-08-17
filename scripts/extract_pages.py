#!/usr/bin/env python3

import argparse
import os
import sys
from concurrent.futures import ProcessPoolExecutor

try:
    import pymupdf as fitz          # pymupdf>=1.24 起推荐,免 fitz 弃用告警
except ImportError:
    import fitz

try:
    from tqdm import tqdm
except ImportError:                      # graceful fallback if tqdm missing
    def tqdm(it, **kw):
        return it


def _worker_init():
    """每个 worker 进程启动时执行:静默 mupdf 对坏批注等的报错输出。"""
    try:
        fitz.TOOLS.mupdf_display_errors(False)
    except Exception:
        pass


def _extract_chunk(job):
    """渲染并保存一批页面(独立打开文档,进程内实例,线程安全)。

    返回该块成功处理的页数;任一页失败直接抛异常,由主进程统一报错退出。
    """
    pdf, outdir, zoom, indices = job
    mat = fitz.Matrix(zoom, zoom)
    doc = fitz.open(pdf)
    try:
        for i in indices:
            pix = doc.load_page(i).get_pixmap(matrix=mat)   # flat RGB on white
            pix.save(os.path.join(outdir, f"page_{i:04d}.png"))
    finally:
        doc.close()
    return len(indices)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("outdir")
    ap.add_argument("--zoom", type=float, default=2.0,
                    help="render zoom vs 72dpi base (2.0 = 144dpi, matches the "
                         "native image resolution of these scans)")
    ap.add_argument("--workers", type=int, default=1,
                    help="并行提取的进程数 (默认 1 = 单进程;多页 PDF 建议 4~8)")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    fitz.TOOLS.mupdf_display_errors(False)
    doc = fitz.open(args.pdf)
    n = doc.page_count
    doc.close()
    if n == 0:
        print(f"!! PDF 无页面: {args.pdf}")
        sys.exit(1)

    if args.workers <= 1 or n <= 1:
        # 顺序路径:保持原行为(单进程单线程)
        mat = fitz.Matrix(args.zoom, args.zoom)
        doc = fitz.open(args.pdf)
        try:
            for i in tqdm(range(n), desc="    extract", unit="pg", ncols=72):
                pix = doc.load_page(i).get_pixmap(matrix=mat)
                pix.save(os.path.join(args.outdir, f"page_{i:04d}.png"))
        finally:
            doc.close()
        return

    # 并行路径:按页号 stride 分块(块间无依赖,文件名与页号一一对应,页序不变)
    w = min(args.workers, n)
    chunks = [c for c in ([list(range(k, n, w)) for k in range(w)]) if c]
    ok = 0
    try:
        with ProcessPoolExecutor(max_workers=len(chunks),
                                 initializer=_worker_init) as ex:
            jobs = [(args.pdf, args.outdir, args.zoom, c) for c in chunks]
            for done in tqdm(ex.map(_extract_chunk, jobs),
                             total=n, desc="    extract", unit="pg", ncols=72):
                ok += done
    except Exception:
        print("!! 并行提取失败(部分页面可能未写出),"
              "见上方错误;可降低 --workers 或重试")
        sys.exit(1)
    if ok != n:
        print(f"!! 提取不完整: 成功 {ok}/{n} 页")
        sys.exit(1)


if __name__ == "__main__":
    main()
