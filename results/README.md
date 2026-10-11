# Results

Every benchmark run is one JSON file in `runs/`, named
`<timestamp>_<gpu-slug>.json`. They are committed deliberately: a benchmark is
only worth anything next to other people's numbers, and the point of this repo
is to accumulate a picture of what real cards actually do with local models.

**Please send yours.** A 1070 Ti and an M4 Max are equally interesting — the low
end is usually under-reported, and "it did not fit" is a result too.

## Adding your run

1. Run a benchmark. Give it a label that names the card, e.g. `RTX 5070 Ti`.
2. Your run appears in `results/runs/`.
3. Open a pull request with that file. Nothing else needs changing.

That is the whole process. The file is self-describing: it carries the GPU, the
driver, the host CPU and RAM, the exact config, and every measurement.

### What makes a run useful

- **Say which card it is** in the run label. `GTX 1070 Ti` beats `my pc`.
- **Note anything unusual** in the notes field — a power limit, an undervolt, a
  riser, a laptop on battery, an unusual ambient temperature.
- **Leave the defaults alone** unless you mean to change them. Reasoning off and
  MTP off are what make runs comparable; a run with reasoning on measures how
  fast the model thinks, not how fast it answers.
- **Failures are data.** A model that would not load at 40k context tells us
  exactly where that card stops. Do not delete those rows.

### What is in the file

Each run records the GPU (name, VRAM, driver, UUID), the host (CPU, RAM, OS),
the config, and per test: prefill tk/s, decode tk/s, time to first token, actual
prompt tokens, GPU watts, estimated system watts, joules per 1k prefill tokens
and per output token, peak VRAM, the context length it was loaded at, the GPU
offload placement that worked, and the model's own output sample.

If you would rather not publish your machine's details, edit the `host` and
`gpu` blocks before opening the PR — keep `name` and `vram_total_mb` so the
numbers mean something, and drop whatever else you like. The GPU `uuid` in
particular is a unique hardware identifier and nothing depends on it.

## Comparing

Clone the repo and launch the app: every run in `runs/` shows up in the
**Summary** and **Compare** tabs automatically, including the ones other people
contributed. You do not need to own a card to look at its numbers.
