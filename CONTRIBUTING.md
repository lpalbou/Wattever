# Contributing

Share benchmark results by opening a pull request with the JSON file from `results/runs/`. Review the file for information you do not want to publish and follow [results guidance](results/README.md). Record model builds, placement and relevant settings so readers can interpret results.

For code changes, preserve old result readability, distinguish measured values from estimates, and document changes in measurement semantics. Wattever uses the Python standard library and local browser assets. See [architecture](docs/architecture.md), [API](docs/api.md), [changelog](CHANGELOG.md), and [security](SECURITY.md).

Run the software checks applicable to your change:

```sh
python -m unittest discover -s tests
node --check web/app.js
node tests/test_ui.js
node tests/test_tables.js
python tools/validate_results.py
```

The optional browser test is `node tests/test_ui_browser.cjs`. State which platforms and real hardware you exercised; software tests cannot establish GPU performance. Follow the [code of conduct](CODE_OF_CONDUCT.md).
