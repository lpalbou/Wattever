# Contributing

Share benchmark results by opening a pull request with one JSON file in `results/runs/`. The [sharing guide](docs/sharing-results.md) explains the GitHub upload workflow and how to add missing tests later. Review the file for information you do not want to publish and follow [results guidance](results/README.md). Record model builds, placement and relevant settings so readers can interpret results.

Capability contributions use individual `results/capabilities/<id>.json` files. Preserve full public audit answers, scorer decisions, suite/protocol/subset fingerprints and public configuration identities. Review prompts/expected answers and generated text before sharing. The helper's import independently re-scores and checks consistency; fingerprints do not authenticate execution. Keep dataset licenses/attribution and distinguish Wattever chat adaptations from official benchmark harnesses. See [capability evaluation](docs/capabilities.md).

For code changes, preserve old result readability, distinguish measured values from estimates, and document changes in measurement semantics. Wattever uses the Python standard library and local browser assets. See [architecture](docs/architecture.md), [API](docs/api.md), [changelog](CHANGELOG.md), and [security](SECURITY.md).

Run the software checks applicable to your change:

```sh
python -m unittest discover -s tests
node --check web/app.js
node tests/test_ui.js
node tests/test_endpoint_ui.js
node tests/test_browser_backend.js
node tests/test_capabilities_ui.js
node tests/test_tables.js
python tools/validate_results.py
python tools/update_docs.py --check
```

The optional browser test is `node tests/test_ui_browser.cjs`. State which platforms and real hardware you exercised; software tests cannot establish GPU performance. Follow the [code of conduct](CODE_OF_CONDUCT.md).
