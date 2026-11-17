# Upgrade options for two RTX 5070 Ti cards

**Interim update, 14 November 2026: the campaign is paused at the user's request.**
The newer priority is minimum cost and useful completed answers. The ten-option
list below is an earlier research draft, not a validated shopping recommendation.
In particular, its 3090/5090/professional-GPU options are deferred: no measurement
yet justifies replacing the existing cards or buying another SSD.

Native Linux is the first feasibility investigation, with its performance benefit
**unproven on this machine**. Maya's documentation identifies Linux as its main
development/measurement environment; NVIDIA documents pinned-memory limitations
under WSL. Neither establishes how much more memory this particular dual-5070-Ti
machine can pin under native Linux, or its resulting inference speed. A matched
test would need the same weights, prompts, runtime revision and settings. No OS
installation, boot trial or repartitioning has been performed.
[Maya Windows guidance](https://github.com/mw00/project-maya/blob/main/docs/WINDOWS.md),
[NVIDIA WSL limitations](https://docs.nvidia.com/cuda/wsl-user-guide/index.html).

Cheap search results require stock and complete-configuration checks. The
[EUR 570 Z840/128 GB/1125 W](https://www.quantelectronic.de/en/Server-Workstations/Workstation/HP-68282.html)
and [EUR 769 T7910/128 GB/1300 W](https://isistemi.si/products/dell-precision-t7910-xeon-8-core-e5-2667-v4-3-2ghz-128gb-ram-1300w-psu)
were sold out when checked. They are excluded as available offers. Private
Threadripper/EPYC leads below lack a verified delivered price and complete
dual-card fit/power validation. No complete delivered 128–256 GB offer has yet
passed every check. Shipping destination, exact GPU dimensions, current case
and PSU details remain necessary; resale proceeds are assumed zero until verified.

The read-only local check also found that D: is exFAT with 1 MiB allocation units
and Windows reports `Full Repair Needed`, while the physical Samsung 990 PRO
reports Healthy/OK. No repair or formatting was attempted. This is a separate
storage-health qualification, not evidence that a replacement SSD would improve
inference. See the saved
[pause and storage evidence](../results/studies/20261114-quality-versus-waiting/README.md).

For the measured GLM expert-streaming workload, the first substantial hardware
priority is more host memory on a supported platform while keeping both GPUs.
The Samsung 990 PRO already serves real expert reads, both GPU links reach about
13.8 GB/s, and the Windows driver caps the pinned expert tier well below total
physical RAM. A GPU purchase alone may leave those constraints in place.
See the [GLM results](glm53-flash-comparison.md) for measured speed, correctness
and memory evidence, and the [Flash-Next results](two-5070-ti-comparison.md) for
the separate Qwen models.

These ten options run from inexpensive checks toward larger purchases. They
are alternatives and dependent steps, rather than a shopping list to buy in
order. No speed multiplier has been measured on replacement equipment. Prices
are France/EU research observations from 14 November 2026, before resale of
existing parts; shipping, stock and exact compatibility need a fresh quote.
Used listings are asking prices, not verified offers or tested machines.

| Priority | Change | Indicative cost / quote status | Expected benefit for this workload |
|---:|---|---|---|
| 1 | Fix the negotiated 100 Mbps Ethernet path | Free cable/port swap; roughly EUR 3-20 for a cable | Faster model downloads if the local link is the bottleneck; local inference speed is unchanged |
| 2 | Trial native Linux on a separate boot installation | Free software plus setup time | Test whether more host experts can be pinned and SSD misses reduced with the existing RAM |
| 3 | Move both GPUs into a validated used workstation with 128 GB RAM | Approximately EUR 900 private Z840 lead; complete professional quote required | Best first hardware capacity target: more resident Flash experts and room for higher quants |
| 4 | Expand that supported workstation to 256 GB ECC RAM | Matching memory quote required; RAM type and population matter | More room for larger quants/models, long KV caches and OS headroom |
| 5 | Replace one or both cards with tested used RTX 3090 24 GB cards | Observed private asks EUR 1,300-1,500 each; seek better verified deals | 40 GB total VRAM with one replacement, 48 GB with two; capacity gain requires a new speed comparison |
| 6 | Ryzen 9000 + verified CPU-connected x8/x8 board + 128 GB DDR5 | Rough component budget EUR 3,400-4,500+ under observed RAM pricing | Newer CPU instructions and faster PCIe, plus host capacity; only two RAM channels |
| 7 | Single-socket EPYC 7003 platform with 256-512 GB DDR4 | Observed EUR 3,750 basis / EUR 4,200 system leads need memory-population and PSU changes; obtain a suitable complete quote | Eight memory channels when populated correctly, many PCIe 4.0 lanes, large resident model pool |
| 8 | Used Threadripper PRO / WRX80 workstation with 256-512 GB | Observed private EUR 3,850 system lead with 256 GB, excluding GPU/disks | Workstation form factor with eight-channel memory and many PCIe lanes |
| 9 | Replace one 5070 Ti with an RTX 5090 32 GB | Observed retail examples roughly EUR 9,250-9,900; poor value at those asks | 48 GB aggregate VRAM, newer GPU throughput; compare against a RAM/platform migration first |
| 10 | Professional GPU with 48-96 GB VRAM | Observed 48 GB category example EUR 10,850; 96 GB example about EUR 19,370 | Largest GPU-resident capacity, at a high capital cost |

## Compatibility and decision notes

**1. Ethernet.** The active Intel I218-V adapter negotiates 100 Mbps despite
Auto Negotiation. Its controller supports gigabit Ethernet. Try a known-good
Cat5e/Cat6 cable and gigabit router/switch port, then verify the negotiated link
and download rate. A 94.7 GB file set has a theoretical minimum of about 2.1
hours over 100 Mbps before overhead. WAN/CDN limits may remain after a local
fix. No adapter setting was changed during this investigation.
[Intel controller information](https://www.intel.com/content/www/us/en/products/details/ethernet/gigabit-controllers/i218-connections/resource.html),
[example cable](https://www.ldlc.com/fiche/PB00726438.html).

**2. Native Linux.** This is a reversible experiment to investigate an observed
Windows pinned-memory limit, not a promised speed upgrade. Keep Windows intact
and use a supported SM120 driver/build. WSL2 retains limitations on pinned
system memory and is not equivalent to a native Linux test. An installation
has not been performed.
[NVIDIA pinned-memory discussion](https://forums.developer.nvidia.com/t/change-limit-of-50-for-cudahostalloc-pinned-memory-on-windows-10-11/228235/8),
[Microsoft graphics-memory accounting](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/calculating-graphics-memory),
[CUDA on WSL limitations](https://docs.nvidia.com/cuda/wsl-user-guide/index.html).

**3. A 128 GB workstation.** The existing EVGA X99 Micro2 has four occupied
DIMM slots and an official 64 GB maximum; the i7-5960X also specifies 64 GB.
Buying four 32 GB DIMMs or ECC RDIMMs for this board is not a supported upgrade.
A different supported platform is required.
[EVGA manual](https://www.evga.com/support/Manuals/files/131-HE-E095_Part1_v2.pdf),
[Intel CPU specifications](https://www.intel.com/content/www/us/en/products/sku/82930/intel-core-i75960x-processor-extreme-edition-20m-cache-up-to-3-50-ghz/specifications.html).

A Z840 with the 1125 W supply supports two 300 W GPUs electrically, but two
large consumer 5070 Ti coolers still require a physical fit, airflow and cable
check. The 850 W version is a different configuration. Prefer enough CPU cores
for expert work and validated RAM population over the cheapest four-core base.
HP recommends slots 6 and 2 for compute cards on CPU0; host-memory locality still
needs measurement. A roughly EUR 900 French private listing is a lead, not a
compatibility guarantee. Attractive EUR 400-490 T7910 retailer examples found
in this research were sold out and are not available purchasing recommendations.
[HP service manual](https://h10032.www1.hp.com/ctg/Manual/c04823811.pdf),
[private Z840 listings](https://www.leboncoin.fr/ck/ordinateurs/hp-z840).

**4. 256 GB ECC.** Do this only after choosing a platform that validates the
exact modules, ranks and population. RDIMM and LRDIMM must not be mixed. Extra
capacity can remove file fetches for a larger expert working set, but does not
directly raise GPU arithmetic throughput. Match modules across all relevant
channels and consider NUMA placement before accepting a two-socket bargain.
[HP memory/platform guidance](https://h10032.www1.hp.com/ctg/Manual/c04823811.pdf).

**5. Used 3090 cards.** The gain is VRAM capacity, with a change of GPU
generation and power/cooling requirements. The existing SM120-only benchmark
binary needs a compatible build for Ampere. Do not assume that replacing a
5070 Ti with a 3090 improves every quantized kernel or that the additional
VRAM eliminates expert streaming. Test the actual cards and keep resale
proceeds separate from gross purchase cost.
[Current private asking examples](https://www.leboncoin.fr/ck/accessoires_informatique/rtx-3090/p-2),
[Maya supported runtime information](https://github.com/mw00/project-maya).

**6. Modern AM5.** Choose a board whose second long slot connects to the CPU
at useful bandwidth. The ProArt X870E-Creator supports x8/x8 with appropriate
Ryzen 7000/9000 CPUs, but M.2 sharing can reduce the second GPU link. It supports
up to 256 GB; four DIMMs can lower supported RAM speed. Confirm QVL, BIOS,
DIMM arrangement and cooler clearance. The research budget reflects unusually
high observed DDR5 prices and is not a verified complete-system offer.
[ASUS expansion and memory specifications](https://www.asus.com/motherboards-components/motherboards/proart/proart-x870e-creator-wifi/techspec/).

**7. EPYC.** This is the strongest large-memory platform route to quote before
buying an expensive GPU. For example, H12SSL-i supports single-socket EPYC
7002/7003, eight DDR4 DIMMs and multiple PCIe 4.0 x16 slots. Budget a complete
working system, not just a cheap used CPU. Boot firmware, GPU cooler spacing,
server-board form factor, noise and power all affect the final choice.
[Supermicro H12SSL-i specifications](https://www.supermicro.com/en/products/motherboard/H12SSL-i).

One observed [EPYC 7543 / ROMED8-2T basis](https://www.marktplaats.nl/v/computers-en-software/moederborden/m2462564465-epyc-bundel-romed8-2t-epyc-7543-256gb-rdimm)
asks EUR 3,750; the corresponding
[EUR 4,200 complete system](https://www.marktplaats.nl/v/computers-en-software/servers/m2463831567-epyc-7543-server-workstation-compleet-256gb-ram-4u-rack)
has an 850 W supply. Its 256 GB uses four 64 GB DIMMs, populating only four
memory channels. The quoted CPU is 225 W and the two GPUs are nominally 600 W
together, leaving inadequate system headroom on that supply at simultaneous
rated loads. Quote eight-channel memory population and an appropriate PSU/cable
configuration before comparing its delivered cost. These are private pickup
leads in Amsterdam, without independent inspection or contact.

**8. Threadripper PRO.** A validated WRX80 workstation provides another
eight-channel DDR4 route. Compare its complete delivered price, warranty,
noise and GPU fit with EPYC. A
[private EUR 3,850 example](https://www.kleinanzeigen.de/s-anzeige/workstation-server-threadripper-pro-3975wx-256gb-ecc-ram/3566216224-228-701)
lists a 3975WX, ASUS WRX80E-SAGE SE WIFI, eight 32 GB DDR4-2933 ECC DIMMs,
1200 W supply, case and cooling, excluding GPUs and disks. It requires pickup
in Kiel and independent condition/card-fit/cable checks. A cheaper Swiss
256 GB/3090 result found in the search was already sold and is excluded as
an available offer. No inference result on either replacement system is claimed.
[ASUS WRX80 specifications](https://dlcdnets.asus.com/pub/ASUS/mb/SocketTRX4/Pro_WS_WRX80E-SAGE_SE_WIFI/PRO_WS_WRX80E-SAGE_SE_WIFI_SPEC.pdf).

**9. 5090.** Replacing one 16 GB card with a 32 GB card increases this system's
aggregate VRAM from 32 GB to 48 GB. It does not turn the two devices into a
single unified allocation. At the observed retail asking prices, RAM/platform
options deserve a quote first. Verify supply, cables and chassis space.
[Retail price observations](https://www.ldlc.com/informatique/pieces-informatique/carte-graphique-interne/c4684/%2Bfi131-l32h32.html).

**10. Professional VRAM.** A 48-96 GB card can retain substantially more model
data on a GPU and simplify some placement decisions, but capacity alone does
not specify the resulting tokens per second. Treat this as a budget-led
workstation decision after cheaper capacity options have been measured.
[Professional GPU price observations](https://www.ldlc.com/informatique/pieces-informatique/carte-professionnelle/c4685/%2Bfv1316-7316.html).

Another SSD, fans or a larger PSU are conditional maintenance choices. The
existing NVMe has ample free space, the measured GPU links are healthy for
Gen3 x16, and no measured thermal limitation justifies an unconditional cooling
purchase. Buy those parts only for a demonstrated constraint or a chosen new
platform's compatibility requirements.
