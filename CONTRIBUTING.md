# Contributing

Two kinds of contribution, and the first one matters more.

## 1. Send your results

This is the main ask. Run the benchmark on whatever card you have, and open a
pull request with the JSON file it drops in `results/runs/`. That is the entire
process — the file is self-describing and nothing else needs editing.

Old cards are as welcome as new ones. A GTX 1070 Ti struggling with a 27B model
is more informative than another 4090 result, and "it would not load at 40k
context" is a measurement, not a failure to report.

See [`results/README.md`](results/README.md) for what makes a run useful and
what the file contains, including how to strip your machine's details if you
would rather not publish them.

## 2. Code

Keep in mind what the tool is for: numbers that stay comparable across machines
and across time. A change that improves a measurement is welcome; a change that
silently alters what is being measured breaks every result already in the repo.

House rules:

- **Standard library only.** No dependencies, no build step, and the web UI
  loads nothing from a CDN — a benchmark box is often offline, and a tool that
  needs `pip install` before it can measure anything is one more variable.
- **Do not change measurement semantics without saying so.** Prefill tk/s is
  `prompt_tokens ÷ time-to-first-token`; decode is timed from the first token
  onward; reasoning is off; every prompt opens with a nonce so prefix caching
  cannot falsify a repeat. If you need to change one of these, add a field
  rather than redefining an existing one, so old runs stay readable.
- **Add fields, never remove or repurpose them.** Every JSON file in
  `results/runs/` is data someone measured once and cannot easily remeasure.
- **Measured and estimated stay separate.** GPU watts are measured; whole-system
  watts are an estimate. Anything new that is an estimate must say so in its
  name and in the UI.
- **Platform differences live in `wattever/platform_util.py`.** Windows and
  macOS already diverge in three places (subprocess flags, machine identity,
  power sensor); keep new ones there rather than scattering `if` statements.

### Running from source

```
git clone https://github.com/lpalbou/Wattever
cd Wattever
python tools/build_corpus.py      # only needed if corpora/ is missing
python -m wattever.server
```

Then open the URL it prints. The launchers (`Wattever.bat`,
`Wattever.command`) do the same thing with a friendlier front door.

### Before opening a PR

There is no test suite — the thing under test is a GPU. What is expected
instead:

- run a real benchmark end to end and check the numbers are still sane
- confirm an existing run file still loads in the Results and Compare tabs
- if you touched the macOS path, say whether you ran it on a Mac or not
