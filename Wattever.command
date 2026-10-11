#!/bin/bash
# Double-clickable launcher for macOS. Same app as the Windows .bat; the only
# extra step is the optional admin prompt, because reading power on Apple
# Silicon means running `powermetrics`, which requires root.
#
# If this will not open from Finder, make it executable once:
#     chmod +x "Launch GPU Benchmark.command"

cd "$(dirname "$0")" || exit 1
printf '\033]0;LLM GPU Benchmark\007'

PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
      PY="$candidate"
      break
    fi
  fi
done

if [ -z "$PY" ]; then
  cat <<'MSG'

  Python 3.9+ was not found.

  macOS ships a usable python3 with the Xcode command line tools:
      xcode-select --install
  or install it from https://www.python.org/downloads/

  No other packages are needed.

MSG
  read -r -p "Press Return to close. " _
  exit 1
fi

if [ ! -f "corpora/agentic-ai-oct2026.full.md" ]; then
  echo "Building the benchmark corpus, one moment..."
  "$PY" tools/build_corpus.py || exit 1
fi

# ---- optional power measurement -------------------------------------------
# GPU/CPU watts come from `powermetrics`, which needs root. The benchmark runs
# fine without it: prefill and decode speed are measured either way, and only
# the wattage and energy columns stay blank. We cache the sudo credential up
# front and keep it warm for the length of the run, so nothing prompts for a
# password in the middle of a two-hour benchmark.
KEEPALIVE_PID=""
echo
echo "  Power measurement (GPU + CPU watts) needs administrator rights,"
echo "  because macOS only exposes power readings through powermetrics."
echo "  Speed measurements do not need it."
echo
read -r -p "  Measure power? [Y/n] " REPLY_POWER
case "$REPLY_POWER" in
  [Nn]*)
    echo "  Continuing without power measurement."
    ;;
  *)
    if sudo -v; then
      echo "  Power measurement enabled."
      # refresh the credential every 50s (the default timeout is 5 minutes)
      # and stop as soon as this launcher exits
      ( while kill -0 "$$" 2>/dev/null; do sudo -n -v 2>/dev/null; sleep 50; done ) &
      KEEPALIVE_PID=$!
      export GPU_BENCH_SUDO_POWER=1
    else
      echo "  Could not get administrator rights - continuing without power measurement."
    fi
    ;;
esac

cleanup() {
  [ -n "$KEEPALIVE_PID" ] && kill "$KEEPALIVE_PID" 2>/dev/null
  # drop the cached credential rather than leaving it warm after we exit
  sudo -k 2>/dev/null
}
trap cleanup EXIT INT TERM

echo
echo "  Starting the benchmark server. Your browser will open shortly."
echo "  Keep this window open while you benchmark; press Ctrl+C to stop."
echo
"$PY" -m wattever.server
STATUS=$?

if [ "$STATUS" -ne 0 ]; then
  echo
  echo "  The server exited with an error. The message above says why."
  read -r -p "  Press Return to close. " _
fi
exit "$STATUS"
