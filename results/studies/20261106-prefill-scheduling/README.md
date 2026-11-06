# Prefill scheduling diagnostic evidence

This study is separate from the normal Wattever machine benchmark library.
Read the [engineering report](../../../docs/prefill-scheduling-and-concurrency.md)
for matched comparisons, context capacity, telemetry and interpretation limits.

- `manifest.json`: protocol, hardware, weight/runtime hashes and artifact checksums.
- `summary.csv`: the 19 validated primary performance conditions and aggregate observations.
- `cases.json.gz`: request counts/answers, original client timings, group-relative dispatch/output times, observed stream-chunk arrivals, slot progress, actual slot capacities, power samples and diagnostic counters.
- `prompts.json.gz`: deduplicated exact canonical prompts, hashes, corpus identity and tokenizer observations.
- `sanity.json`: separate natural-stopping sequence-isolation checks; 15/15 unique-code answers, not a model quality benchmark.
- `figures/`: standalone SVG and PNG charts.
- `patches/`: optional llama.cpp scheduler changes against MIT-licensed [llama.cpp b12058](https://github.com/ggml-org/llama.cpp/tree/b12058). Performance captures use v1; the v2 parent/child guard has separate sanity validation.

Decompress the JSON files using Python's standard library:

```python
import gzip, json
from pathlib import Path
folder = Path("results/studies/20261106-prefill-scheduling")
cases = json.loads(gzip.decompress((folder / "cases.json.gz").read_bytes()))
```

Raw request times share the recorded monotonic group origin. Power samples retain
their own sampler elapsed clock. Server prompt timings are elapsed durations, not
kernel-only compute measurements. Failed/nonmatching-control captures stay outside
paired performance tables. Only complete matching conditions have one measured
performance group; warmups and sanity checks are separate. Prompts are decoded as
UTF-8 and verified against their canonical checksums before dispatch. Noncanonical
pilot captures remain in the private diagnostic archive and are excluded here.

## Reproduce the scheduler condition

Check out llama.cpp b12058 and apply the selected patch. Build using the upstream
instructions for your platform; the captured MSVC build reused the same-tag
official CUDA backend DLL, as identified in the manifest. A different compiler or
backend is a separate build condition. The patch is opt-in and defaults to stock
scheduling when the environment variable is absent or zero.

For the 20k five-request comparison, use the same Q4_K_M file, matched prompts,
sampling, context and output stopping policy on both sides. For example, in
PowerShell with your compiled binary/model paths:

```powershell
$env:WATTEVER_FAIR_PREFILL_TOKENS = '512' # use '0' for the paired greedy control
llama-server --model Qwen3.8-27B-Q4_K_M.gguf --host 127.0.0.1 --port 8091 `
  --device CUDA0,CUDA1 --split-mode layer --tensor-split 1,1 --main-gpu 0 `
  --gpu-layers all --fit off --parallel 5 --ctx-size 110080 `
  --kv-unified --kv-unified-per-slot 22016 --cache-ram 0 --no-cache-idle-slots `
  --flash-attn on --cache-type-k f16 --cache-type-v f16 `
  --batch-size 2048 --ubatch-size 512 --threads 8 --threads-batch 8 `
  --spec-type none --reasoning off --no-cache-prompt --no-context-shift `
  --no-mmproj --ignore-eos --slots --offline --verbose --perf
```

Choose an unused port; this command does not authorize replacing an existing
listener. Submit the five supplied prompts simultaneously using streaming Chat
Completions, temperature 0, top-p 1, seed 73129 and max_tokens 256. Preserve actual
usage counts. Each owned engine uses one separately recorded eight-token
warmup before its measured group (two warmups for replicas). Remove `--ignore-eos` for ordinary interactive
use; that changes the output-stop condition and must be recorded.

For replicas, run one owned server per card, each with one slot/context 22016 and
CUDA visibility restricted to its physical card. Dispatch clients 0/2/4 to the
primary and 1/3 to the secondary. Capture one all-card power window over the entire
five-job cohort. Never add client decode rates or overlapping request energies.

The captured fair policy is deliberately narrow: ordinary splittable text
completion, no speculation, LoRA, embeddings or multimodal inputs. V2 additionally
excludes shared parent/child tasks. Do not infer universal model support, quality
equivalence, bit-identical outputs or improved first-token latency from this study.
