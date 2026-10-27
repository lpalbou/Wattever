#!/bin/sh
# Portable Linux/POSIX launcher; run with `sh Wattever.sh`.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
for wattever_python in python3 python; do
    if command -v "$wattever_python" >/dev/null 2>&1 &&
       "$wattever_python" -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then
        if [ ! -f corpora/agentic-ai-oct2026.full.md ]; then
            "$wattever_python" tools/build_corpus.py
        fi
        exec "$wattever_python" -m wattever.server
    fi
done
echo 'Wattever needs Python 3.9 or newer.' >&2
exit 1
