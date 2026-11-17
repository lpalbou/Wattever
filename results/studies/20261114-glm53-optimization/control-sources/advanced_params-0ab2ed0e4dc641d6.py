"""Tunable launch parameters for Control's Advanced panel, with plain-language effects."""
from __future__ import annotations


# Each entry: key, label, values (or None for free text), default, effect text.
QWEN_ADVANCED = (
    {
        'key': 'ram_use_percent',
        'label': 'Expert RAM budget (%)',
        'values': ('auto', '0', '25', '50', '75', '90', '95', '99.9'),
        'editable': True,
        'default': 'auto',
        'effect': 'This limits the explicit expert RAM copy, not total system RAM. Auto preserves headroom. A percentage uses fresh available RAM after essential buffers. 0 uses file-backed experts; Windows may still cache most free RAM. High values can cause paging.',
    },
    {
        'key': 'prefill',
        'label': 'Prefill chunk',
        'values': ('auto', '4096', '8192', '16384', '32768'),
        'default': 'auto',
        'effect': 'How many prompt tokens are processed per chunk. Larger is often faster on long prompts but needs more VRAM peak.',
    },
    {
        'key': 'vram_reserve_mib',
        'label': 'VRAM reserve (MiB)',
        'values': ('auto', '1000', '1500', '2000', '3000', '4000'),
        'default': 'auto',
        'effect': 'Memory left free on each GPU. Raise this if load fails with OOM; lower it to fit more experts/context.',
    },
    {
        'key': 'expert_cache',
        'label': 'Expert cache',
        'values': ('auto', 'off'),
        'default': 'auto',
        'effect': 'Keeps MoE experts on the GPU. Auto fills the usable GPU cache; off reduces GPU residency and usually slows inference. RAM cache use is controlled separately.',
    },
    {
        'key': 'conversation_cache_mib',
        'label': 'Conversation cache (MiB)',
        'values': ('0', '512', '1024', '2048', '4096'),
        'default': '0',
        'effect': 'Host cache for reused prompt prefixes. Larger helps multi-turn chats; 0 disables that cache.',
    },
    {
        'key': 'mtp_depth', 'label': 'MTP verify window',
        'values': ('2', '3', '4'), 'default': '4',
        'effect': 'Tokens checked together when MTP is enabled. The model default is 4; a smaller window can help workloads with frequent draft rejection.',
    },
    {
        'key': 'mtp_min_p', 'label': 'Draft confidence',
        'values': ('0.3', '0.5', '0.7', '0.9'), 'default': '0.5',
        'effect': 'Confidence required to extend an MTP draft. Higher values make shorter, more cautious drafts. The best value depends on the text being generated.',
    },
    {
        'key': 'cpu_workers', 'label': 'CPU expert workers',
        'values': ('auto', '1', '2', '4', '6', '8'), 'editable': True, 'default': 'auto',
        'effect': 'Auto uses the detected CPU cores. A manual count must fit this CPU and can leave room for other applications; more workers are not always faster.',
    },
    {
        'key': 'pcie_fraction', 'label': 'GPU share of cache misses',
        'values': ('auto', '0', '0.2', '0.35', '0.5', '0.75', '1'), 'default': 'auto',
        'effect': 'Auto measures the GPU links. 0 computes cache misses on the CPU; 1 transfers them all to the GPU. Resident GPU experts remain enabled either way. Manual overrides apply the same share to every card.',
    },
)

GLM_ADVANCED = (
    {
        'key': 'prefill', 'label': 'Prefill chunk',
        'values': ('auto', '1024', '2048', '4096', '8192', '16384', '32768'),
        'default': 'auto',
        'effect': 'Auto lets Maya size prompt chunks. Larger chunks can improve long-prompt speed but need more staging memory. The native runtime records the actual chunk size.',
    },
    {
        'key': 'read_chunks', 'label': 'Expert read pieces',
        'values': ('auto', '1', '2', '4', '8', '16'), 'default': 'auto',
        'effect': 'How many parallel pieces each expert-file read uses. Auto keeps native calibration; the best count depends on storage. Applies at startup to prefill and decode.',
    },
    {
        'key': 'cpu_workers', 'label': 'CPU expert threads',
        'values': ('auto', '0', '2', '4', '8', '16'), 'editable': True, 'default': 'auto',
        'effect': 'Auto sizes and calibrates CPU expert work for the installed equipment. 0 disables the compute lane. A manual count sets the native pool size; layouts with separate pools can oversubscribe the CPU.',
    },
    {
        'key': 'pcie_fraction', 'label': 'GPU share of RAM misses',
        'values': ('auto', '0', '0.25', '0.5', '0.75', '1'), 'default': 'auto',
        'effect': 'Auto compares CPU work and PCIe transfers. 1 sends RAM-tier misses to the GPU; 0 favors CPU computation. GPU-resident experts remain enabled. Client request overrides can supersede this default.',
    },
    {
        'key': 'gpus',
        'label': 'GPU order',
        'values': ('auto',),
        'default': 'auto',
        'editable': True,
        'effect': 'Auto discovers installed GPUs at each load. Enter comma-separated GPU indices to select and order cards explicitly.',
    },
    {
        'key': 'parallel',
        'label': 'Request slots',
        'values': ('1', '2'),
        'default': '1',
        'effect': 'How many conversations can run at once. 2 needs more KV/RAM and can lower per-chat speed.',
    },
    {
        'key': 'kv',
        'label': 'KV cache precision',
        'values': ('int8', 'fp16'),
        'default': 'int8',
        'effect': 'int8 uses less memory for long context; fp16 is more precise and uses more VRAM/RAM.',
    },
    {
        'key': 'kv_placement',
        'label': 'KV placement',
        'values': ('RAM + VRAM', 'VRAM only'),
        'default': 'RAM + VRAM',
        'effect': 'RAM + VRAM streams long KV between host and GPU. VRAM only is simpler but needs more GPU memory.',
    },
    {
        'key': 'split',
        'label': 'Layer split',
        'values': ('auto', '21', '22', '23', '24', '25', '26'),
        'default': 'auto',
        'editable': True,
        'effect': 'Auto partitions all selected GPUs. A manual split needs one increasing boundary per GPU transition: for example 24 for two cards or 15,30 for three. Boundaries must fit the prepared model.',
    },
    {
        'key': 'vram_reserve_mib',
        'label': 'VRAM reserve (MiB)',
        'values': ('auto',),
        'default': 'auto',
        'editable': True,
        'effect': 'Auto keeps the reviewed reserve. An explicit nonnegative MiB reserve is checked against every selected GPU.',
    },
    {
        'key': 'ram_tier',
        'label': 'Expert RAM (GiB / %)',
        'values': ('auto', '0%', '25%', '50%', '75%', '90%', '99.9%'),
        'default': 'auto',
        'editable': True,
        'effect': 'Auto lets Maya budget RAM. A percentage uses fresh available physical/commit RAM after 12 GiB headroom; plain numbers remain GiB. Native pinning/DXGI limits may reduce this upper budget. 0% retains essential buffers and OS file cache.',
    },
    {
        'key': 'reasoning',
        'label': 'Reasoning effort',
        'values': ('none', 'low', 'high', 'max'),
        'default': 'low',
        'effect': 'Low is the recommended GLM starting point for answer quality. None requests thinking off but can reduce accuracy; higher levels spend more tokens before the final reply. Allow enough output tokens for thinking and the answer.',
    },
)


def defaults_for(engine):
    table = definitions_for(engine)
    return {item['key']: item['default'] for item in table}


def definitions_for(engine):
    if engine == 'maya':
        return GLM_ADVANCED
    if engine == 'qwen36':
        return tuple(
            dict(item, values=('auto', '512', '1024', '2048', '4096')) if item['key'] == 'prefill'
            else dict(item, values=('auto', '25', '50', '75', '90', '95', '99.9'),
                      effect='Auto preserves headroom; a percentage uses fresh available RAM after essential buffers. This engine requires an expert RAM cache, so 0% is unavailable. A small budget can fail its memory preflight.')
            if item['key'] == 'ram_use_percent' else item
            for item in QWEN_ADVANCED if item['key'] in ('ram_use_percent', 'prefill', 'vram_reserve_mib'))
    return QWEN_ADVANCED
