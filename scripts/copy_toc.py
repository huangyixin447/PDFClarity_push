#!/usr/bin/env python3
"""把原 PDF 的导航信息(目录/书签、页码标签、元数据、页面模式)复制到成品 PDF。

rebuild_pdf.py 重建时会自动调用 copy_navigation();本脚本也可单独运行,
用来修复此前生成的、目录丢失的成品:

  python scripts/copy_toc.py <原PDF> <成品PDF>            # 原地修复(先写临时文件、校验后替换)
  python scripts/copy_toc.py <原PDF> <成品PDF> -o 新.pdf   # 另存

要求两者页数一致(成品页序即原 PDF 页序);页数不一致时按较小页数截断,
指向越界页的书签退化为无跳转条目并给出警告。
"""

import argparse
import os
import sys

try:
    import pymupdf as fitz
except ImportError:
    import fitz


def _normalize_toc(toc, page_count):
    """把 get_toc(simple=False) 的结果整理成 set_toc 可稳妥接受的形式。

    - 只保留 kind=GOTO 的目标位置(to/zoom);命名目标(kind=4)等其它类型
      只按页号重建为普通页内跳转,避免 set_toc 处理不了的字段;
    - 页号越界的条目改为无跳转(page=-1),但保留标题层级不丢;
    - 层级不连续(子级比父级深超过 1)时 set_toc 会报错,这里逐条拉平。
    """
    out = []
    bad = 0
    prev_lvl = 0
    for item in toc:
        lvl, title, page = item[0], item[1], item[2]
        dest = item[3] if len(item) > 3 and isinstance(item[3], dict) else {}
        lvl = max(1, min(int(lvl), prev_lvl + 1))
        prev_lvl = lvl
        try:
            page = int(page)
        except (TypeError, ValueError):
            page = -1
        if page < 1 or page > page_count:
            bad += 1
            out.append([lvl, title, -1])
            continue
        new_dest = {"kind": fitz.LINK_GOTO, "page": page - 1}
        if dest.get("kind") == fitz.LINK_GOTO:
            to = dest.get("to")
            if to is not None:
                new_dest["to"] = fitz.Point(to)
            if dest.get("zoom"):
                new_dest["zoom"] = dest["zoom"]
        if "collapse" in dest:
            new_dest["collapse"] = bool(dest["collapse"])
        out.append([lvl, title, page, new_dest])
    return out, bad


def copy_navigation(ref, new, quiet=False):
    """把 ref(原 PDF)的目录 / 页码标签 / 元数据 / 页面模式复制到 new。

    返回复制的目录条目数。ref、new 均为已打开的 fitz.Document。
    """
    n_toc = 0
    try:
        toc = ref.get_toc(simple=False)
    except Exception as e:                       # 坏目录树:不影响成品生成
        if not quiet:
            print(f"    !! WARN: 读取原 PDF 目录失败,成品将无目录: {e}")
        toc = []
    if toc:
        norm, bad = _normalize_toc(toc, new.page_count)
        if bad and not quiet:
            print(f"    !! WARN: {bad} 条书签指向不存在的页(原 {ref.page_count} 页 / "
                  f"成品 {new.page_count} 页),已保留标题但去掉跳转")
        try:
            new.set_toc(norm)
            n_toc = len(norm)
        except Exception as e:
            # 兜底:去掉目标位置细节,只按页号重建
            simple = [[t[0], t[1], t[2]] for t in norm]
            try:
                new.set_toc(simple)
                n_toc = len(simple)
            except Exception as e2:
                if not quiet:
                    print(f"    !! WARN: 写入目录失败,成品将无目录: {e} / {e2}")

    try:
        labels = ref.get_page_labels()
        if labels:
            new.set_page_labels(labels)
    except Exception:
        pass

    try:
        meta = {k: v for k, v in (ref.metadata or {}).items()
                if v and k in ("title", "author", "subject", "keywords")}
        if meta:
            cur = new.metadata or {}
            cur.update(meta)
            new.set_metadata(cur)
    except Exception:
        pass

    try:
        if n_toc and ref.pagemode == "UseOutlines":
            new.set_pagemode("UseOutlines")
    except Exception:
        pass
    return n_toc


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ref_pdf", help="原 PDF(目录来源)")
    ap.add_argument("target_pdf", help="成品 PDF(目录丢失的文件)")
    ap.add_argument("-o", "--out", default=None,
                    help="输出路径;缺省为原地修复(先写同目录临时文件,校验后替换)")
    args = ap.parse_args()

    fitz.TOOLS.mupdf_display_errors(False)
    ref = fitz.open(args.ref_pdf)
    new = fitz.open(args.target_pdf)
    if ref.page_count != new.page_count:
        print(f"!! WARN: 页数不一致: 原 {ref.page_count} 页, 成品 {new.page_count} 页")
    n = copy_navigation(ref, new)
    ref.close()

    in_place = args.out is None
    out = args.target_pdf if in_place else args.out
    tmp = os.path.join(os.path.dirname(os.path.abspath(out)) or ".",
                       f".{os.path.basename(out)}.toc_tmp_{os.getpid()}")
    try:
        # 全量保存(不重压缩图片流,只是重写对象),避免增量保存在个别阅读器上的兼容问题
        new.save(tmp, garbage=1)
        new.close()
        chk = fitz.open(tmp)
        ok = chk.page_count == fitz.open(args.target_pdf).page_count and len(chk.get_toc()) == n
        chk.close()
        if not ok:
            raise RuntimeError("校验失败:临时文件页数/目录条目数不符")
        os.replace(tmp, out)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    mb = os.path.getsize(out) / 1e6
    print(f"Done: {out}  (目录 {n} 条, {mb:.1f} MB)")


if __name__ == "__main__":
    main()
