# Sharing and continuing benchmark results

The hosted Results view includes a shared catalog built from benchmark JSON files in this repository, alongside results stored in your browser. You can browse shared results without connecting a model provider or entering an API key. Shared entries are read-only; deleting one in the hosted view hides it in that browser and does not delete it from GitHub. Local browser results can be renamed and deleted locally.

## Contribute a benchmark

1. Run a benchmark using the [full helper](getting-started.md) or [browser mode](browser.md). Partial sessions and failures are useful contributions too.
2. In Results, download the benchmark's **JSON**. Review the hardware information, notes and model names before sharing. Browser results identify their separate client-timed protocol; helper results include the sensor and session settings.
3. Sign in to GitHub and open [upload files to results/runs](https://github.com/lpalbou/Wattever/upload/main/results/runs). Upload the single JSON file, named after its `id` with a `.json` extension. If you do not have write access, follow GitHub's fork prompt and propose the change from your fork.
4. Open the proposed pull request. The repository's validation checks run on the contribution. Once merged and the site deployment completes, the shared catalog updates automatically.

For a command-line contribution, fork and clone the repository, create a branch, and copy the single result file to `results/runs/`. Run `python tools/validate_results.py results/runs/<id>.json`, then `git add results/runs/<id>.json`, commit, push your branch and open a pull request. Stage the specific result file so unrelated local files and deletions are excluded.

## Add missing tests later

A benchmark does not need every planned case before you contribute it. To complete or extend a helper benchmark later, download its JSON and put it in the local helper's `results/runs/` directory using its `id` as the filename. In Results select it and choose **Add / rerun tests**. Existing models are preselected; select the models, sizes and query counts you want, review the preview, and choose **Add missing tests**. Existing terminal cases are preserved. Select **Retry failed tests** explicitly if you also want to retry failures and skipped cases.

The helper checks hardware, selected GPU and driver, corpus and LM Studio version for compatibility. Start a separate benchmark when those differ or cannot be verified. The same file records the added session and the helper saves a backup before modifying it. To update a contribution, submit the updated file with the same ID in a pull request; the saved cases travel with it. Do not manually edit measurements or replace failures with zeroes.

Browser mode can import and display JSON but cannot continue an existing benchmark. Its provider controls model execution and context. See [measurements and sessions](measurements.md) for retry, replacement and comparison rules.
