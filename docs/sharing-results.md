# Sharing and continuing benchmark results

The hosted Results view includes a shared catalog built from benchmark JSON files in this repository, alongside results stored in your browser. You can browse shared results without connecting a model provider or entering an API key. Shared entries are read-only; deleting one in the hosted view hides it in that browser and does not delete it from GitHub. Local browser results can be renamed and deleted locally.

## Contribute a benchmark

1. Run a benchmark using the [full helper](getting-started.md) or [browser mode](browser.md). Partial sessions and failures are useful contributions too.
2. In Results, download the benchmark's **JSON**. Review hardware information and all free text, including notes, model names, labels, reproduction fields and compute sources. Public outputs exclude endpoint URLs, credentials and credential environment-variable names; that filtering cannot decide whether your annotations are confidential. Browser results identify their separate client-timed protocol; helper results include sensor/session settings.
3. Sign in to GitHub and open [upload files to results/runs](https://github.com/lpalbou/Wattever/upload/main/results/runs). Upload the single JSON file, named after its `id` with a `.json` extension. If you do not have write access, follow GitHub's fork prompt and propose the change from your fork.
4. Open the proposed pull request. The repository's validation checks run on the contribution. Once merged and the site deployment completes, the shared catalog updates automatically.

For a command-line contribution, fork and clone the repository, create a branch, and copy the single result file to `results/runs/`. Run `python tools/validate_results.py results/runs/<id>.json`, then `git add results/runs/<id>.json`, commit, push your branch and open a pull request. Stage the specific result file so unrelated local files and deletions are excluded.

## Add missing tests later

Same-file continuation applies to managed LM Studio runs. Generic endpoint and browser experiments require a new run; connection details are not stored in public result files.

A benchmark does not need every planned case before you contribute it. To complete or extend a managed LM Studio benchmark later, download its JSON and put it in the local helper's `results/runs/` directory using its `id` as the filename. In Results select it and choose **Add / rerun tests**. Existing models are preselected; select the models, sizes and query counts you want, review the preview, and choose **Add missing tests**. Existing terminal cases are preserved. Increase **Total repeats per test** to append repeats. To replace one specific measurement, choose its entry in **Tests to update**; previous attempts remain in history. Select **Retry failed tests** explicitly if you also want to retry failures and skipped cases.

The helper checks hardware, selected GPU and driver, corpus and LM Studio version for compatibility. Start a separate benchmark when those differ or cannot be verified. The same file records the added session and the helper saves a backup before modifying it. To update a contribution, submit the updated file with the same ID in a pull request; the saved cases travel with it. Do not manually edit measurements or replace failures with zeroes.

Browser mode can import and display JSON but cannot continue an existing benchmark. Its provider controls model execution and context. See [measurements and sessions](measurements.md) for retry, replacement and comparison rules.

## Reproducibility metadata

Hardware inventory describes the benchmark client. For remote inference, the result retains client inventory separately and does not claim it describes the server. Include public engine/version, model revision, quantization, actual context/KV settings, MTP draft count and runtime placement when known. Optional reproduction fields are marked `user_reported`; they do not configure the engine.

Only add compute claims with a source, arithmetic precision, execution path, dense/sparse distinction and peak/measured/estimate classification. INT precision uses TOPS; floating precision uses TFLOP/s. A claim is not a Wattever measurement. PCIe encoding ceilings are not measured DMA bandwidth, and idle link state can differ from loaded state. Compare client timings, provider timings and cache usage using their separate provenance; unknown cache usage is not zero. See [measurement definitions](measurements.md).

## Capability results

Use **Capabilities → Export JSON** for one or more capability runs. These use `benchmark_kind: capability`, their own schema 1 and `results/capabilities/<id>.json`; keep throughput files in `results/runs/`. A `capability_runs` bundle is convenient for helper import/export; contribute individual run files under their IDs. The hosted catalog supports browsing/export/comparison; tests and validated imports require the helper. The hosted site does not write to GitHub.

Review generated visible answers, prompts, expected answers and source citations, as well as labels/notes and reported settings. Public audit material preserves exact content for dataset/protocol fingerprints and independent rescoring. Privacy-redacted responses are marked and cannot be re-imported as full, independently scored audit answers. Keep a reviewed original if needed.

Import re-scores answers and checks configuration/suite/protocol/subset fingerprints. These establish consistency, not authentication or proof that a model ran. Compare matching evaluation keys with scored/error/truncation counts and requested/reported/unknown effective settings. Automatically resolved opaque IDs and display names identify routes without sharing or hashing connection URLs. Keep the private helper/browser URL-to-ID registry out of contributions. See [capability evaluation](capabilities.md).
