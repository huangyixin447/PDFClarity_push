#!/bin/bash
# PDFClarity - compatibility forwarder.
# The pipeline logic now lives in run.py (pure Python, no bash dependency),
# so macOS / Git Bash keep working unchanged while Windows can use run.bat.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -n "${PYTHON:-}" ]; then PY="$PYTHON"
elif command -v python3 >/dev/null 2>&1; then PY=python3
else PY=python; fi
exec "$PY" "$HERE/run.py" "$@"
