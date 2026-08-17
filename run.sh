#!/bin/bash
# PDFClarity — make low-resolution / blank-in-Skim scanned PDFs sharper.
#
# Usage:
#   ./run.sh "/path/to/input.pdf"            # one PDF
#   ./run.sh "/path/to/folder"               # every *.pdf in the folder (batch)
#   ./run.sh <input> 4 4320                   # input  model-scale  target-width(px)
#
# Pipeline (per PDF): extract pages -> Real-ESRGAN upscale -> rebuild a clean PDF.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"
REALESRGAN="$HERE/bin/realesrgan/realesrgan-ncnn-vulkan"
MODEL_DIR="$HERE/bin/realesrgan/models"

INPUT="${1:?Usage: ./run.sh <input.pdf | folder> [model-scale] [target-width]}"
SCALE="${2:-4}"            # Real-ESRGAN upscale factor (2 or 4)
TARGET_W="${3:-4320}"      # final page width in px (0 = keep full size)
MODEL="${MODEL:-realesrgan-x4plus}"
RECURSIVE="${RECURSIVE:-0}"   # set RECURSIVE=1 to also scan sub-folders
SKIP_EXISTING="${SKIP_EXISTING:-1}"  # 1 = skip PDFs whose output already exists

[ -x "$REALESRGAN" ] || { echo "Upscaler missing/not executable: $REALESRGAN"; exit 1; }

# ---- a simple terminal progress bar ---------------------------------------
progress_bar() {                          # current total label
    local cur="$1" total="$2" label="${3:-}" width=32
    local pct=0 filled i bar=""
    [ "$total" -gt 0 ] && pct=$(( cur * 100 / total ))
    [ "$pct" -gt 100 ] && pct=100
    filled=$(( pct * width / 100 ))
    for ((i=0; i<width; i++)); do
        if [ "$i" -lt "$filled" ]; then bar+="█"; else bar+="░"; fi
    done
    printf "\r  %-9s [%s] %3d%% (%d/%d)" "$label" "$bar" "$pct" "$cur" "$total"
}

count_png() { find "$1" -maxdepth 1 -name '*.png' 2>/dev/null | wc -l | tr -d ' '; }

# ---- process a single PDF -------------------------------------------------
process_one() {
    local input="$1" idx="${2:-}" total="${3:-}"
    local base work src up out tag
    base="$(basename "${input%.*}")"
    work="$HERE/work/$base"; src="$work/src"; up="$work/up"
    out="$HERE/output/${base}_clear.pdf"
    tag=""; [ -n "$idx" ] && tag="[$idx/$total] "

    if [ "$SKIP_EXISTING" = "1" ] && [ -f "$out" ]; then
        echo "==> ${tag}SKIP (output exists): $out"
        return 0
    fi

    echo "==================================================================="
    echo "==> ${tag}Processing: $input"
    mkdir -p "$src" "$up" "$HERE/output"

    echo "  [1/3] Extracting pages"
    "$PY" "$HERE/scripts/extract_pages.py" "$input" "$src" --zoom 2.0

    echo "  [2/3] Upscaling x$SCALE with $MODEL (GPU)"
    xattr -dr com.apple.quarantine "$HERE/bin/realesrgan" 2>/dev/null || true
    local total_pages upid
    total_pages="$(count_png "$src")"
    # run the upscaler in the background; its own logs go to a file so they
    # don't clobber the progress bar
    "$REALESRGAN" -i "$src" -o "$up" -n "$MODEL" -s "$SCALE" -m "$MODEL_DIR" -f png \
        >"$work/upscale.log" 2>&1 &
    upid=$!
    while kill -0 "$upid" 2>/dev/null; do
        progress_bar "$(count_png "$up")" "$total_pages" "upscale"
        sleep 0.5
    done
    if ! wait "$upid"; then
        echo; echo "  !! upscale failed (see $work/upscale.log)"; return 1
    fi
    progress_bar "$total_pages" "$total_pages" "upscale"; echo

    echo "  [3/3] Rebuilding clean PDF (target width ${TARGET_W}px)"
    "$PY" "$HERE/scripts/rebuild_pdf.py" "$up" "$out" \
          --ref-pdf "$input" --target-width "$TARGET_W" --quality 90

    echo "  -> Done: $out"
}

# ---- single file or batch over a folder -----------------------------------
if [ -f "$INPUT" ]; then
    process_one "$INPUT"
    echo; echo "All done. Output in: $HERE/output"
elif [ -d "$INPUT" ]; then
    # collect *.pdf (case-insensitive); top-level only unless RECURSIVE=1
    pdfs=()
    if [ "$RECURSIVE" = "1" ]; then
        while IFS= read -r -d '' f; do pdfs+=("$f"); done \
            < <(find "$INPUT" -type f -iname '*.pdf' -print0 | sort -z)
    else
        while IFS= read -r -d '' f; do pdfs+=("$f"); done \
            < <(find "$INPUT" -maxdepth 1 -type f -iname '*.pdf' -print0 | sort -z)
    fi

    count="${#pdfs[@]}"
    [ "$count" -gt 0 ] || { echo "No PDF files found in: $INPUT"; exit 1; }
    echo "Found $count PDF(s) in: $INPUT"
    [ "$RECURSIVE" = "1" ] && echo "(recursive scan on)"

    i=0; ok=0; fail=0; failed=()
    for f in "${pdfs[@]}"; do
        i=$((i+1))
        if process_one "$f" "$i" "$count"; then
            ok=$((ok+1))
        else
            fail=$((fail+1)); failed+=("$f")
            echo "  !! FAILED: $f (continuing)"
        fi
    done

    echo
    echo "==================================================================="
    echo "Batch finished: $ok succeeded, $fail failed (of $count)."
    if [ "$fail" -gt 0 ]; then
        echo "Failed files:"; printf '  - %s\n' "${failed[@]}"
    fi
    echo "Output in: $HERE/output"
else
    echo "Input not found (not a file or folder): $INPUT"; exit 1
fi

echo "Intermediate files kept under: $HERE/work  (safe to delete to free space)"