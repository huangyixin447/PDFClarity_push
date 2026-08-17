#!/usr/bin/env python3

import sys, os, argparse, glob
import fitz
from PIL import Image

try:
    from tqdm import tqdm
except ImportError:                      # graceful fallback if tqdm missing
    def tqdm(it, **kw):
        return it


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("imgdir", help="folder of upscaled page PNGs")
    ap.add_argument("out_pdf")
    ap.add_argument("--ref-pdf", default=None,
                    help="original PDF, used to copy exact page sizes")
    ap.add_argument("--target-width", type=int, default=4320,
                    help="downscale pages wider than this (px). 4320 ~= 288dpi. "
                         "0 = keep full upscaled size")
    ap.add_argument("--quality", type=int, default=90)
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.imgdir, "*.png")) +glob.glob(os.path.join(args.imgdir, "*.jpg")))
    if not files:
        sys.exit(f"No images found in {args.imgdir}")

    # page sizes from the reference PDF (keeps original aspect/size)
    ref_sizes = None
    if args.ref_pdf and os.path.exists(args.ref_pdf):
        fitz.TOOLS.mupdf_display_errors(False)
        rd = fitz.open(args.ref_pdf)
        ref_sizes = [(p.rect.width, p.rect.height) for p in rd]
        rd.close()

    tmp_dir = os.path.join(os.path.dirname(args.out_pdf) or ".", "_jpg_tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    new = fitz.open()
    n = len(files)
    for i, f in enumerate(tqdm(files, desc="    rebuild", unit="pg", ncols=72)):
        im = Image.open(f).convert("RGB")
        if args.target_width and im.width > args.target_width:
            h = round(im.height * args.target_width / im.width)
            im = im.resize((args.target_width, h), Image.LANCZOS)
        jpg = os.path.join(tmp_dir, f"p_{i:04d}.jpg")
        im.save(jpg, "JPEG", quality=args.quality)

        if ref_sizes and i < len(ref_sizes):
            w_pt, h_pt = ref_sizes[i]
        else:                                   # fall back to 72dpi of the image
            w_pt, h_pt = im.width * 72 / 144, im.height * 72 / 144
        pg = new.new_page(width=w_pt, height=h_pt)
        pg.insert_image(pg.rect, filename=jpg)

    new.save(args.out_pdf, deflate=True, garbage=4)
    new.close()
    

    # cleanup temp jpgs
    for f in glob.glob(os.path.join(tmp_dir, "*.jpg")):
        os.remove(f)
    os.rmdir(tmp_dir)
    mb = os.path.getsize(args.out_pdf) / 1e6
    print(f"Done: {args.out_pdf}  ({mb:.1f} MB, {n} pages)")


if __name__ == "__main__":
    main()
