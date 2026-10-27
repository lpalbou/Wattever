# Wattever

Wattever measures local LLM prefill and decode throughput and GPU power with LM Studio. It records successes and failures across prompt sizes, models and simultaneous query counts, and compares sessions in a browser UI. Use the hosted browser mode with an OpenAI-compatible endpoint, or the full local helper with Python 3.9+ and LM Studio. Wattever uses the Python standard library and local JavaScript without a package installation or CDN.

## Browser quick start

Open [Wattever on GitHub Pages](https://lpalbou.github.io/Wattever/), start an OpenAI-compatible server, enable its CORS support, and use **Connect** to select models and run tests. Browser and server permissions can affect access to a local endpoint. See [Browser mode](docs/browser.md) for settings, storage, privacy and measurement limits.

## Full local helper quick start

1. Install LM Studio, download the models you want to test, and run `lms bootstrap` once so the `lms` command is available.
2. Get the [v2.4.1 source archive](https://github.com/lpalbou/Wattever/releases/tag/v2.4.1) or clone the repository. The source archive includes historical tracked results.
3. On Windows run `Wattever.bat`; on macOS run `Wattever.command`; on Linux run `sh Wattever.sh`. Alternatively run `python -m wattever.server` from the repository.
4. Open the displayed local URL, select models and workloads on **Run**, and start. Leave the server running until the benchmark finishes. Results appear as cases complete.

See [Getting started](docs/getting-started.md) for setup and [Measurements and sessions](docs/measurements.md) for interpretation and extending a benchmark. GPU monitoring selects a sensor only: configure actual model placement in LM Studio and record GPU setup notes. Power is measured for the selected GPU, not every GPU.

**Results** provides Summary, Table and Compare views with common benchmark, model, measure and concurrency filters. Tables can be copied as tab-separated text. In helper mode, use **Add / rerun tests** to extend a benchmark or replace selected cases. One JSON file stores each benchmark; download it from Results or share it as described in [results guidance](results/README.md). Review its contents before publication.

The hosted Results view also includes a shared catalog that you can browse without connecting a model provider. See [sharing results](docs/sharing-results.md) to contribute your benchmark or add missing tests later.

## Documentation

[Documentation index](docs/README.md) | [Architecture](docs/architecture.md) | [API](docs/api.md) | [FAQ](docs/faq.md) | [Troubleshooting](docs/troubleshooting.md) | [Changelog](CHANGELOG.md) | [Contributing](CONTRIBUTING.md) | [Security](SECURITY.md)

## For AI agents

Register and use the [Wattever agent skill](https://github.com/lpalbou/Wattever/tree/v2.4.1/skills/wattever) to operate the local API safely. Copy `skills/wattever/` from the version you use into your agent's skills directory.

Wattever is [MIT licensed](LICENSE).
