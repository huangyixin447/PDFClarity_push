#!/usr/bin/env python3

import sys, os, argparse
import fitz  # PyMuPDF

try:
    from tqdm import tqdm
except ImportError:                      # graceful fallback if tqdm missing
    def tqdm(it, **kw):
        return it


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("outdir")
    ap.add_argument("--zoom", type=float, default=2.0,
                    help="render zoom vs 72dpi base (2.0 = 144dpi, matches the "
                         "native image resolution of these scans)")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    fitz.TOOLS.mupdf_display_errors(False)
    doc = fitz.open(args.pdf)
    mat = fitz.Matrix(args.zoom, args.zoom)
    n = doc.page_count
    for i in tqdm(range(n), desc="    extract", unit="pg", ncols=72):
        page = doc.load_page(i)
        pix = page.get_pixmap(matrix=mat)          # flat RGB on white
        out = os.path.join(args.outdir, f"page_{i:04d}.png")
        pix.save(out)
    doc.close()


if __name__ == "__main__":
    main()
