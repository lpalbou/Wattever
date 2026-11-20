# vLLM on two RTX 5070 Ti cards: NVFP4 serving, context capacity and concurrency

This study serves NVIDIA's Blackwell-optimized `nvidia/Qwen3.8-27B-NVFP4-QAD` with vLLM on two RTX
5070 Ti 16 GB cards and compares it with LM Studio serving `qwen/qwen3.8-27b` (Q4_K_M GGUF) on the
same machine. It reports the memory budget that sets vLLM's context pool, the settings that enlarge
it without disabling features, partial prefix reuse, tensor versus pipeline parallelism, and the
energy each engine needs for the same batch. See [concurrency comparisons](concurrency.md) for how
batch, per-request and end-to-end rates are defined, and [measurements](measurements.md) for metric
sources.

[Wattever run, 82,960-token pool](../results/runs/20261120-180306_rtx-5070-ti_f7bfac8a77.json)
· [Wattever run, 188,832-token pool](../results/runs/20261120-191559_rtx-5070-ti_b78375c125.json)
· [LM Studio baseline](../results/runs/20261119-185313_rtx-5070-ti_4365041ded.json)
· [Long-prompt sweep](../results/aggregates/vllm-two-5070-ti-long-prompts.csv)
· [Prefix reuse probe](../results/aggregates/vllm-two-5070-ti-prefix-reuse.csv)
· [Energy per batch](../results/aggregates/vllm-two-5070-ti-energy.csv)

## Summary

- With fixed cache memory and BF16 linear-attention state, vLLM holds a **188,832-token** context
  pool with prefix caching, image input, tool calling and the reasoning parser enabled. Automatic
  sizing with the same features gives 82,960 tokens.
- At 40k-token prompts and two to five simultaneous queries, vLLM completes each batch
  **1.55–1.65× faster** than LM Studio and uses **33–46% less GPU energy** for 20k and 40k batches.
- Both engines draw 110–135 W per card during these batches. vLLM keeps both cards busy 81–94% of
  the time; LM Studio's layer split keeps each card busy 30–39% of the time.
- Prompt processing dominates 30–60k-token requests. With tensor parallelism, combined prefill
  peaks near 3,450 tokens/s; with pipeline parallelism it reaches 6,000 tokens/s, but decoding at
  45–60k context is two to three times slower.
- Exact prompt continuations are reused at once (44k tokens in 0.4–0.6 s). Reuse of a prompt that
  shares only its beginning requires `--prefix-cache-retention-interval`.

## Hardware and software

| Setting | Recorded condition |
|---|---|
| CPU, memory | Intel Core i7-5960X (8 cores, 16 threads), 64 GiB DDR4 |
| GPUs | PNY RTX 5070 Ti 16 GB and ASUS Prime RTX 5070 Ti 16 GB, NVIDIA driver 617.42 |
| Interconnect | PCIe 3.0 x16 per card through a PCIe bridge; GeForce peer-to-peer transfers unavailable, so cross-card traffic is staged through host memory |
| vLLM | `vllm/vllm-openai:nightly` (0.34.0rc1.dev1014), Docker Desktop with the WSL2 backend on Windows 10 |
| LM Studio baseline | LM Studio managed by Wattever, `qwen/qwen3.8-27b` Q4_K_M, both cards, automatic layer split |
| Workload | Wattever speed protocol: 512 to 80k-token prompts, 1 to 5 simultaneous queries, 256 output tokens, reasoning off |

The NVFP4 checkpoint is mixed precision: MLPs, most linear-attention projections and the output
head are NVFP4; attention projections and ten linear-attention layers are FP8; embeddings (248,320
× 5,120) and the vision tower are BF16. Its weights total 19.9 GB, or 9.17 GiB per card under
tensor parallelism. vLLM in this image has no GGUF loader, so the two engines serve different
quantizations of the same model.

## Memory budget per card

Qwen3.8-27B has 64 layers: 16 full-attention layers whose KV cache grows with context and 48 Gated
DeltaNet (linear-attention) layers that keep a fixed-size state per request. With an FP8 KV cache,
the attention layers cost about 16 KB per token per card.

| Memory on each 15.89 GiB card | Automatic sizing | Fixed KV cache |
|---|---|---|
| Held by the CUDA/WSL runtime before vLLM loads | 1.50 GiB | 1.50 GiB |
| Weights | 9.17 GiB | 9.17 GiB |
| Activation reserve and CUDA graphs | 1.63 GiB | used from the remaining memory |
| KV cache | 1.49 GiB | 3.00 GiB |
| **Context pool shared by all requests** | **82,960 tokens** | **188,832 tokens** |

The fixed-cache configuration uses these settings, with every serving feature kept:

```bash
vllm serve nvidia/Qwen3.8-27B-NVFP4-QAD --quantization modelopt \
  --tensor-parallel-size 2 --max-model-len -1 --max-num-seqs 8 \
  --kv-cache-memory-bytes 3G --gpu-memory-utilization 0.90 \
  --kv-cache-dtype fp8 --mamba-cache-dtype bfloat16 --mamba-ssm-cache-dtype bfloat16 \
  --prefix-cache-retention-interval 8320 \
  --reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser qwen3_coder
```

- `--kv-cache-memory-bytes` sets the cache size directly instead of deriving it from warmup
  estimates. Under WSL each card reports 14.39 GiB free at startup, so `--gpu-memory-utilization`
  must stay at or below 0.90 to pass vLLM's startup check.
- The model stores its linear-attention state in FP32 by default. vLLM accepts FP32, FP16 or BF16
  for this state; BF16 halves the state per request and per prefix-cache checkpoint, and sets an
  832-token cache block. The quality effect of BF16 state was not measured in this study.
- `--max-model-len -1` sets the longest request that fits the pool (188,032 tokens here).

## Wattever results: pool size and concurrency

Batch wall time in seconds for the same Wattever workload. Both vLLM runs keep prefix caching and
image input enabled.

| Prompt × queries | vLLM, 82,960-token pool | vLLM, 188,832-token pool | LM Studio Q4_K_M |
|---|---|---|---|
| 40k × 1 | 16.9 | 17.3 | 24.0 |
| 40k × 2 | 45.1 | 30.2 | 46.9 |
| 40k × 3 | 71.6 | 43.4 | 69.3 |
| 40k × 5 | 125.1 | 70.6 | 116.6 |
| 80k × 1 | 36.1 | 36.0 | not run |
| 80k × 2 | 130.7 | 66.7 | not run |
| 80k × 5 | 325.4 | 168.1 | not run |

When the requests in flight need more than the pool, vLLM preempts one: it releases that request's
cache and recomputes its prompt later. With the 82,960-token pool, each additional 80k query added
about 65 s, close to two prompt passes. With the 188,832-token pool, two 80k queries run together
and each additional query adds about 33 s, one prompt pass. The vLLM server log records the
preemption count for each interval.

End-to-end output rate with the 188,832-token pool (tokens/s, 1 → 5 queries):

| Prompt | vLLM NVFP4 | LM Studio Q4_K_M |
|---|---|---|
| 512 | 59.7 → 197.9 | 36.7 → 82.1 |
| 5k | 32.1 → 94.4 | 29.0 → 46.4 |
| 20k | 20.7 → 33.7 | 18.1 → 20.7 |

## Long prompts: tensor versus pipeline parallelism

A separate sweep sent one batch of N simultaneous requests with random prompts (no prefix-cache
hits) and 256 output tokens, using `vllm bench serve`. Pipeline parallelism places half the layers
on each card instead of splitting every layer across both.

| Prompt × requests | Tensor parallel: wall / mean TTFT (s) | Pipeline parallel: wall / mean TTFT (s) |
|---|---|---|
| 10k × 4 | 16.7 / 7.3 | 12.7 / 3.5 |
| 30k × 1 | 14.0 / 9.8 | 10.5 / 4.0 |
| 30k × 4 | 43.2 / 23.6 | 30.3 / 13.7 |
| 45k × 1 | 19.8 / 15.5 | 17.4–24.4 / 10.1–10.7 |
| 45k × 3 | 50.8 / 30.3 | 73.3 / 26.7 |
| 60k × 1 | 26.5 / 22.0 | 32.5 / 18.8 |
| 60k × 3 | 91.3 / 50.4 | 96.3 / 40.9 |

Pipeline parallelism shortens time to first token at every size, and combined prompt processing
reaches 6,000 tokens/s against about 3,450 with tensor parallelism. Its decode rate at 45–60k
context is two to three times lower and varied between repeats, so tensor parallelism completes
45–60k batches with answers sooner. A single 180k-token prompt prefilled at 1,777 tokens/s with
tensor parallelism.

## Prefix reuse

vLLM's automatic prefix caching reuses cached blocks for any later request that starts with the
same tokens, including simultaneous requests from different clients. Clients keep reuse high by
sending a stable prompt head: system prompt, tool definitions and documents first, in the same
order, with changing text after them.

For the linear-attention layers, a request can resume only where a state checkpoint exists. With
the default `--prefix-cache-retention-interval 0`, vLLM keeps the state at the end of each cached
prompt and at junctions where cached requests diverged. A longer prompt that continues a cached one
is reused; a prompt that shares only part of a cached prompt is recomputed. A positive interval adds
a checkpoint every N tokens (a multiple of the 832-token block).

| 44k-token request (TTFT, s) | Interval 0 | Interval 8,320 |
|---|---|---|
| Cold prompt | 14.9–15.1 | 16.6 |
| Shares its first 22k tokens with a cached prompt | 14.6–15.2 | **9.8** |
| Cold reference | 14.6–14.8 | 14.4 |
| Exact repeat of a cached prompt | 0.40–0.45 | 0.58 |

With an 8,320-token interval, the partially shared request reused 16,640 tokens, the last
checkpoint before the prompts diverged. Checkpoints occupy pool blocks only while they are cached
and are evicted like other cached blocks.

## Energy and card utilization

GPU power comes from the Wattever helper's one-second telemetry log for each batch window.

| Batch | LM Studio: W per card / busy / energy | vLLM: W per card / busy / energy |
|---|---|---|
| 20k × 5 | 133 + 129 W / 34–39% / 15.1 kJ | 127 + 123 W / 90–92% / 8.2 kJ |
| 40k × 2 | 117 + 116 W / 31–35% / 10.9 kJ | 123 + 120 W / 81% / 7.3 kJ |
| 40k × 4 | 118 + 114 W / 33–36% / 21.7 kJ | 132 + 128 W / 93–94% / 14.5 kJ |
| 40k × 5 | 131 + 127 W / 37–38% / 30.1 kJ | 130 + 127 W / 88–90% / 18.1 kJ |

LM Studio's layer split runs the two cards in turn, which matches its 30–39% busy time. vLLM's
tensor parallelism runs both cards on every layer and exchanges partial results between them twice
per layer; on this host that exchange is staged through host memory over PCIe 3.0. Busy time counts
communication kernels, so high busy time at 120–135 W, well below the cards' 300 W limit, indicates
time spent in cross-card exchange rather than in compute. The higher pipeline-parallel prefill rate,
which exchanges data once per step, is consistent with this.

## Choosing a configuration on this hardware

- For 30–60k-token prompts with answers, use tensor parallelism with the fixed 3 GiB KV cache and
  two to four simultaneous requests whose prompts and outputs fit in 188,832 tokens.
- For prompt-heavy work with short answers around 10–30k tokens, pipeline parallelism gives the
  lowest time to first token.
- Agents that resend a long, stable prefix benefit most from prefix caching; set a retention
  interval when prompts change partway through.
- LM Studio holds longer single contexts on these cards, because its Q4_K_M weights are smaller and
  it reserves less memory for batching.
