#!/bin/bash
# Print a Python >= 3.12 executable (Ubuntu 26 may only ship 3.14 in apt; miniconda is fine).
set -euo pipefail
candidates=(
    "${PYTHON:-}"
    /home/pyl/miniconda3/bin/python3.12
    /home/pyl/miniconda3/bin/python3
    python3.12
    python3
)
for c in "${candidates[@]}"; do
    [[ -z "$c" || ! -x "$c" ]] && continue
    if "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
        echo "$c"
        exit 0
    fi
done
echo "No Python >= 3.12 found. Install Miniconda or python3.12." >&2
exit 1
