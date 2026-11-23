# Engine tuning on two RTX 5070 Ti cards: vLLM, Strata and Basalt

This page records what was changed in three inference engines to make them serve long prompts well
on one machine with two RTX 5070 Ti 16 GB cards, what each change was measured to do, and which
upstream tickets came out of the work. It links the benchmark files published for each engine. See
[measurements](measurements.md) for how prefill, decode and time to first token are measured, and
[vLLM on two RTX 5070 Ti cards](vllm-two-5070-ti.md) for the earlier vLLM memory study.

## Machine and protocol

| Setting | Recorded condition |
|---|---|
| CPU, memory | Intel Core i7-5960X (8 cores, AVX2, no AVX-512), 64 GiB DDR4-2133 in four channels |
| GPUs | Two RTX 5070 Ti 16 GB (PNY and ASUS Prime), PCIe 3.0 x16 each, NVIDIA driver 617.42 |
| Storage | Samsung 870 EVO (SATA) and Samsung 990 PRO (NVMe) |
| System | Windows 10; vLLM runs in Docker Desktop on the WSL2 backend |
| Workload | Wattever speed protocol through each engine's OpenAI-compatible endpoint: 256 output tokens, temperature 0, reasoning off, warm-up on, no cached prompt tokens |

## Published benchmarks

| Engine | Model | Coverage | File |
|---|---|---|---|
| vLLM | Qwen3.8 27B NVFP4 | 512 to 100k tokens, 1 and 2 queries, 2 repeats, 188,032-token context | [20261123-145025](../results/runs/20261123-145025_rtx-5070-ti_c1f667eefc.json) |
| vLLM | Inkling 2 Mini | 512 to 260k tokens, 1 and 2 queries, 2 repeats | [20261123-140703](../results/runs/20261123-140703_rtx-5070-ti_c7f2953a3f.json) |
| Strata 0.1.52 | Qwen3.8 Flash-Next UD-IQ4_XS | 512 to 200k tokens, 1 query, 2 repeats, NVMe | [20261121-214021](../results/runs/20261121-214021_rtx-5070-ti_46aec758b2.json) |
| Basalt 0.5.0 | Qwen3.8 Flash-Next UD-IQ4_XS | 512 to 200k tokens, 1 query, 2 repeats, NVMe | [20261121-205631](../results/runs/20261121-205631_rtx-5070-ti_7a23bf4549.json) |
| LM Studio | Qwen3.8 27B Q4_K_M, Q3_K_XL and IQ4_XS | 512 to 40k tokens, 1 to 8 queries, MTP off and on | [20261119-185313](../results/runs/20261119-185313_rtx-5070-ti_4365041ded.json) |
| LM Studio and Strata | 15 models | 512 to 100k tokens, 1 and 2 queries | [20261118-022333](../results/runs/20261118-022333_rtx-5070-ti_45ef464dd3.json) |

The hosted [Results view](https://lpalbou.github.io/Wattever/#results) lists them under GeForce RTX
5070 Ti. The Strata and Basalt files are linked as one comparison: opening either offers the other.

## One query at 40k and 100k tokens

Medians of the repeats in the files above. The engines serve three different models, so only rows
of the same model compare engines.

| Engine and model | Prefill at 40k (tokens/s) | Decode at 40k (tokens/s) | First token at 40k (s) | Prefill at 100k (tokens/s) | Decode at 100k (tokens/s) | First token at 100k (s) |
|---|---|---|---|---|---|---|
| vLLM, Qwen3.8 27B NVFP4 | 3,096 | 65.2 | 12.6 | 2,427 | 60.3 | 41.4 |
| LM Studio, Qwen3.8 27B Q4_K_M, MTP off | 2,525 | 30.8 | 15.7 | 1,783 | 21.8 | 56.0 |
| LM Studio, Qwen3.8 27B Q4_K_M, MTP on | 2,003 | 46.0 | 19.8 | not measured | not measured | not measured |
| vLLM, Inkling 2 Mini | 4,982 | 104.9 | 7.9 | 4,749 | 101.5 | 21.2 |
| Strata, Flash-Next UD-IQ4_XS | 1,134 | 25.9 | 35.4 | 1,236 | 25.8 | 80.7 |
| Basalt, Flash-Next UD-IQ4_XS | 968 | 89.7 | 47.6 | 1,118 | 75.1 | 89.2 |

The LM Studio figures at 100k come from the 15-model file; the others from the three-quantization
file. Qwen3.8 27B fits in the two cards' memory. Flash-Next does not: its expert weights are about
50 GB, so Strata and Basalt keep part of them in system RAM and on the drive, which is why their
tuning below is about RAM and storage.

## vLLM

### Qwen3.8 27B NVFP4

The model is `nvidia/Qwen3.8-27B-NVFP4-QAD` with tensor parallelism across both cards. Prefix
caching, image input, tool calling and the reasoning parser all stay enabled.

| Change | Effect |
|---|---|
| Fixed KV cache size (`--kv-cache-memory-bytes`) instead of automatic sizing | Context from 82,960 to 188,032 tokens |
| BF16 linear-attention state (`--mamba-cache-dtype bfloat16 --mamba-ssm-cache-dtype bfloat16`); the default is FP32 | Part of the same gain: the fixed per-request state halves |
| `--prefix-cache-retention-interval 8320` | A prompt that shares only its beginning with an earlier one is reused; without it only exact continuations are |
| Tensor parallelism kept over pipeline parallelism | Pipeline doubles prefill (up to 6,000 tokens/s) but decodes two to three times slower at 45–60k context |
| Smaller FlashInfer scratch buffers (`VLLM_FLASHINFER_WORKSPACE_BUFFER_SIZE=167772160`), KV cache raised to 3,731 MiB, `--max-model-len 227136`, `--gpu-memory-utilization 0.90` | Context from 188,032 to 227,136 tokens; the two scratch buffers were 394 MiB each by default and the change frees 468 MiB per card |

The published file was recorded at the 188,032-token context, so its 200k and 260k prompts were
refused and are recorded as failed and skipped. The 227,136-token configuration was checked with
direct requests, not with a published Wattever run: 3,136 tokens/s prefill and 66 tokens/s decode
at 40k, 2,424 and 61 at 100k, 1,631 and 53 at 226.9k. Speed did not change with the larger context.

Limits found:

- The card that drives the display holds 0.85–1.1 GiB of desktop memory and peaks near 15.8 of
  16.3 GB. It caps both cards, because tensor parallelism needs the same allocation on each.
- With `--max-model-len -1` near the limit, a full-length request takes every cache block, is
  preempted several times and replayed, and prefill is about two times slower. A fixed length that
  leaves a few blocks spare avoids this.
- The model's full 262,144-token context needs about 0.5 GiB more KV cache than the display card
  has with image input enabled.

No ticket was opened for this model: every change is a launch setting.

### Inkling 2 Mini

Served as `thinkingmachines/Inkling-2-Mini` with tensor and expert parallelism, an FP8 KV cache and
`--kv-cache-memory-bytes 3900M`.

| Problem | Change | Effect |
|---|---|---|
| The launch image does not start with tensor parallelism on RTX 50 cards: its compiled extensions contain no `sm_120` kernels | The two extension libraries are replaced with those of a nightly build of the same week | The model loads and serves on both cards |
| Prefill stalls for seconds on almost every new prompt length: the mixture-of-experts kernels take a value derived from the exact token count as a compile-time constant, so they are recompiled | That value is rounded up to a power of two; the compiled kernels are kept in a persistent cache and compiled once at launch | 16 fresh prompts of random sizes: 213.3 s before, 120.4 s after (1.77×), with bit-identical outputs |
| Context limited to about 64K tokens with the default 2,048-token prefill step: most attention caches are 1,024-token sliding windows that each reserve the window plus one step | `--max-num-batched-tokens 1024` | 262K-token context. A 512-token step reaches 380K but halves prefill |

Wattever sweeps before and after, one query: prefill was about 1,100–1,300 tokens/s at 5k and 20k
and prompts of 80k and more were refused; the published file has 4,375 tokens/s at 10k, 4,982 at
40k, 4,749 at 100k, 3,894 at 200k and 3,520 at 260k, with decode between 87 and 107 tokens/s. The
earlier sweep is not published.

One limit remains: when the display card runs out of memory, Windows moves the overflow to shared
memory and prefill becomes about three times slower until the server is restarted.

Tickets:

- [vllm-project/vllm#69288](https://github.com/vllm-project/vllm/pull/69288): the pull request that
  rounds the kernel planning value. Open at the time of writing.
- [vllm-project/vllm#69081, comment](https://github.com/vllm-project/vllm/issues/69081#issuecomment-7086772457):
  the missing `sm_120` kernels in the launch image under tensor parallelism, with the two
  workarounds.

## Strata

Strata 0.1.52 serving the Unsloth UD-IQ4_XS build of Qwen3.8 Flash-Next, 262,144-token context,
int8 KV cache, automatic layer split across both cards, MTP drafting with a window of 4.

| Change | Effect |
|---|---|
| Model files on NVMe instead of SATA | Prefill four to five times faster (230–254 to 1,134–1,365 tokens/s at 40k–200k), decode +35% (18–20 to 24–26 tokens/s), load time 221 s to 112 s. About 3,090 expert slots lent to the prompt path have no RAM copy and are read from the drive on every 8,192-token chunk |
| Prefix cache off (it was 8 GiB) and image input off | The automatic RAM copy of the experts grows from 36.9 to 45.7 GiB. Prefill 1,375–1,691 tokens/s at 40k–200k (+21% to +27%), decode 30–33 tokens/s |
| Prefill chunk 16,384 | No change: the engine still planned 8,192-token chunks |
| GPU share of cache misses forced to 0.75 (automatic measured 0.38) | No change |
| Expert RAM limited to 26% so that it can be page-locked (14.6 GiB) | Much worse: prefill 495–564 tokens/s, decode 15 tokens/s. The lost RAM capacity costs more than pinning gains |
| MTP window 6 and 8 | Worse: decode 24–27 tokens/s against 29–32. More tokens are accepted per step, but each step takes about 105 ms instead of 74 ms |

The published file is the configuration with image input on and the 8 GiB prefix cache. The faster
configuration without them was measured with the same sweep but is not published, because it ran
without image input. The configuration now used by default keeps image input on and the prefix
cache off; it has not been measured. The prefix cache trades cold-prompt speed for reuse between
turns: with it off, a follow-up turn that does not continue the live session is read again in full.

Engine limit found: on Windows, Strata's RAM copy of the experts is page-locked only when it is at
most about 15 GiB, because it is one allocation. Every larger load logs that page-locking was
refused and runs with pageable memory.

Tickets: none was opened for Strata. The engine's own warning about lent expert slots read from
the drive refers to [Niko1221/Strata#669](https://github.com/Niko1221/Strata/issues/669), an
existing upstream ticket about prefill chunking.

## Basalt

Basalt 0.5.0 serving its own pack of the same UD-IQ4_XS weights
([jesdga/Qwen3.8-Flash-Next-Basalt](https://huggingface.co/jesdga/Qwen3.8-Flash-Next-Basalt)), int8
KV cache, MTP drafting at the engine's default depth of 8.

| Change | Effect at one query |
|---|---|
| Untuned defaults | Prefill 40–174 tokens/s from 5k to 100k, CPU at 100%. Pinning the RAM copy of the experts failed at about 28 of 37 GiB, so only 22% of it was pinned and the CPU copied the rest |
| `resident-budget-gib 26` | The whole RAM copy (27.1 GiB) is pinned. Prefill at 40k from 134 to 847 tokens/s |
| `prefill-cpu-share 0.3` (0, 0.3 and 0.5 were screened) | Best of the three: 1,095 tokens/s at 40k, against 847 and 988 |
| `peer-exclusive true` | Required: the automatic plan stopped at start with "no memory for the file read staging" |
| Context raised to 262,144 tokens | Prompts of 135k and 242k tokens pass. Final SATA sweep: 1,197 tokens/s prefill at 40k, 1,312 at 100k, decode 61–91 tokens/s |
| Pack on NVMe instead of SATA | Prefill unchanged within noise, because only 1–8% of expert reads reach the drive. Decode about +40% and load time 152–192 s to 83 s |

Prompts under about 6,000 tokens stay on a slower CPU path (about 180–390 tokens/s). Prefill at 40k
and above varies by 15–30% between boots, which is more than the storage effect. Image input was
enabled in the configuration after the published file was recorded and its cost was not measured.

Tickets: none was opened for Basalt.

## Strata against Basalt on the same weights

Medians of two repeats from the two published files, one query, model files on NVMe.

| Prompt | Prefill, Strata (tokens/s) | Prefill, Basalt (tokens/s) | Decode, Strata (tokens/s) | Decode, Basalt (tokens/s) | First token, Strata (s) | First token, Basalt (s) |
|---|---|---|---|---|---|---|
| 512 | 90 | 184 | 21.2 | 45.5 | 5.8 | 3.0 |
| 5k | 438 | 389 | 24.4 | 70.4 | 10.9 | 16.9 |
| 10k | 515 | 742 | 24.3 | 89.7 | 18.7 | 14.1 |
| 20k | 816 | 476 | 25.6 | 84.6 | 23.9 | 41.5 |
| 40k | 1,134 | 968 | 25.9 | 89.7 | 35.4 | 47.6 |
| 80k | 1,276 | 1,006 | 25.4 | 73.4 | 63.4 | 82.1 |
| 100k | 1,236 | 1,118 | 25.8 | 75.1 | 80.7 | 89.2 |
| 150k | 1,335 | 1,115 | 26.3 | 64.5 | 112.5 | 134.8 |
| 200k | 1,365 | 918 | 24.6 | 58.7 | 146.5 | 218.0 |

- Strata reads prompts faster at 5k and from 20k up, by 11% to 72%. Basalt is faster at 512 and
  10k. Basalt's two repeats differ widely at 10k (946 and 537 tokens/s) and 20k (419 and 532).
- Basalt decodes 2.1 to 3.7 times faster at every length.
- The answer length at which both take the same total time is about 440 tokens at a 40k prompt and
  about 330 at 100k. Shorter answers finish sooner on Strata, longer ones on Basalt.

## Limits of this evidence

- One or two repeats per test. Prefill is measured on the client, as prompt tokens divided by time
  to first token.
- The engines' current default configurations differ from the published Strata and Basalt files in
  the ways stated above.
- The tuning screens and the "before" sweeps cited here are kept locally and are not in the shared
  catalog.
