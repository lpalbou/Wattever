# Wattever

Wattever measures local LLM prefill and decode throughput and GPU power with LM Studio. It records successes and failures across prompt sizes, models and simultaneous query counts, and compares sessions in a local browser UI. Python 3.9 or newer and LM Studio are required; Wattever uses the Python standard library and local JavaScript with no package installation or CDN.

## Quick start

1. Install LM Studio, download the models you want to test, and run `lms bootstrap` once so the `lms` command is available.
2. Get the [v2.4.0 source archive](https://github.com/lpalbou/Wattever/releases/tag/v2.4.0) or clone the repository. The source archive includes historical tracked results.
3. On Windows run `Wattever.bat`; on macOS run `Wattever.command`; on Linux run `sh Wattever.sh`. Alternatively run `python -m wattever.server` from the repository.
4. Open the displayed local URL, select models and workloads on **Run**, and start. Leave the server running until the benchmark finishes. Results appear as cases complete.

See [Getting started](docs/getting-started.md) for setup and [Measurements and sessions](docs/measurements.md) for interpretation and extending a benchmark. GPU monitoring selects a sensor only: configure actual model placement in LM Studio and record GPU setup notes. Power is measured for the selected GPU, not every GPU.

**Results** provides Summary, Table and Compare views with common benchmark, model, measure and concurrency filters. Tables can be copied as tab-separated text. Use **Add / rerun tests** to extend a benchmark or replace selected cases. One JSON file stores each benchmark; download it from Results or share it as described in [results guidance](results/README.md). Review its contents before publication.

## Documentation

[Documentation index](docs/README.md) | [Architecture](docs/architecture.md) | [API](docs/api.md) | [FAQ](docs/faq.md) | [Troubleshooting](docs/troubleshooting.md) | [Changelog](CHANGELOG.md) | [Contributing](CONTRIBUTING.md) | [Security](SECURITY.md)

## For AI agents

Register and use the [Wattever agent skill](https://github.com/lpalbou/Wattever/tree/v2.4.0/skills/wattever) to operate the local API safely. Copy `skills/wattever/` from the version you use into your agent's skills directory.

Wattever is [MIT licensed](LICENSE).
