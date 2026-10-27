# Getting started

For hosted use without installing Wattever, see [Browser mode](browser.md). The instructions below cover the full local helper.

Install Python 3.9 or newer and [LM Studio](https://lmstudio.ai/), download the models you want to measure, and run `lms bootstrap` to make the LM Studio CLI available. Wattever does not need Python packages or a JavaScript build. The LM Studio API defaults to `http://localhost:1234`; set `LMS_BASE_URL` before starting Wattever if needed.

Download and extract the [release source archive](https://github.com/lpalbou/Wattever/releases/tag/v2.4.1), or clone the repository. Start `Wattever.bat` on Windows, `Wattever.command` on macOS, or `sh Wattever.sh` on Linux. You can also run `python -m wattever.server` in the repository. The server listens on loopback and prints its URL; keep its window open. `GPU_BENCH_PORT` sets the starting port (default 8713); `GPU_BENCH_NO_BROWSER=1` suppresses automatic browser opening.

On **Run**, select installed models, prompt sizes, query counts and settings. Choose a GPU sensor and, for multiple GPUs, describe the LM Studio placement in GPU setup notes. Start the benchmark and view live results. Model loads and inference can take substantial time and change LM Studio's loaded models. The macOS launcher offers optional administrator authorization for `powermetrics`; speed measurements still work without it.

Use **Results** to select and compare benchmark files, download JSON, or copy tables. See [measurements](measurements.md) before comparing and [troubleshooting](troubleshooting.md) if setup fails. Return to [overview](../README.md); see [API](api.md) for integration.
