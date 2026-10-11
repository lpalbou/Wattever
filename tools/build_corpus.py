"""Build the benchmark corpus.

The hand-written core report covers the 512 / 5k / 10k fixtures on its own. The
larger fixtures (20k / 40k / 80k) need roughly a quarter of a million more
characters than anyone wants to write by hand, so this script extends the report
with appendix material composed from structured data: platform profiles,
deployment case studies, incident post-mortems, eval suite specs, hardware notes,
a regulatory digest, a glossary, an FAQ and a bibliography.

Entries are generated from seeded combinations of real vocabulary, so the output
is topical, varied English rather than repeated text -- and it is deterministic,
so every card gets byte-identical prompts. Prefill cost is independent of whether
prose is factual, so this is sound for benchmarking; it is clearly labelled as
synthetic so nobody mistakes the appendices for reporting.

    python tools/build_corpus.py
"""
import os
import random
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "corpora", "agentic-ai-oct2026.core.md")
FULL = os.path.join(ROOT, "corpora", "agentic-ai-oct2026.full.md")
SIZES_DIR = os.path.join(ROOT, "corpora", "sizes")

# Characters per token for English prose with this tokeniser family. Measured
# against the models in use; the runner refines it per model at run time.
CHARS_PER_TOKEN = 4.6

TARGETS = [("512", 512), ("5k", 5000), ("10k", 10000),
           ("20k", 20000), ("40k", 40000), ("80k", 80000)]

rnd = random.Random(20261011)


def pick(seq, n=1):
    return rnd.sample(list(seq), n) if n > 1 else rnd.choice(list(seq))


# --- vocabulary -----------------------------------------------------------

ORG_A = ["Northwind", "Meridian", "Calder", "Brightline", "Halcyon", "Ironwood",
         "Westmark", "Tessera", "Lumen", "Arbiter", "Cobalt", "Pinnacle", "Rivet",
         "Solstice", "Thornbury", "Vantage", "Wavelength", "Yardstick", "Zenith",
         "Ashgrove", "Belmont", "Crestline", "Dunmore", "Eastgate", "Fairhaven",
         "Granite", "Harbour", "Inkwell", "Juniper", "Keystone", "Longford",
         "Marchmont", "Nightjar", "Oakfield", "Parkview", "Quarry", "Redstone",
         "Stonebridge", "Trellis", "Umbra", "Verity", "Wexford", "Yewtree"]
ORG_B = ["Systems", "Labs", "Analytics", "Health", "Financial", "Logistics",
         "Insurance", "Retail", "Energy", "Telecom", "Manufacturing", "Media",
         "Education", "Public Services", "Legal", "Biosciences", "Robotics",
         "Payments", "Infrastructure", "Software"]
SECTORS = ["retail banking", "property and casualty insurance", "hospital operations",
           "telecommunications support", "industrial maintenance", "freight logistics",
           "pharmaceutical regulatory affairs", "public-sector benefits administration",
           "higher education administration", "commercial legal services",
           "utility grid operations", "e-commerce merchandising",
           "payments risk and disputes", "clinical trial coordination",
           "media rights management", "semiconductor process engineering",
           "agricultural supply chain", "municipal permitting",
           "aviation maintenance planning", "capital markets research"]
TASKS = ["first-line support triage", "claims adjudication",
         "contract clause extraction", "incident post-mortem drafting",
         "vendor questionnaire completion", "code migration across a monorepo",
         "test suite repair", "financial reconciliation",
         "regulatory filing preparation", "clinical note summarisation",
         "procurement document review", "data quality remediation",
         "customer churn outreach drafting", "internal knowledge search",
         "release note generation", "log triage and alert enrichment",
         "warranty claim validation", "tariff classification",
         "candidate screening summaries", "field service scheduling"]
TIERS = ["assistive", "supervised agentic", "bounded autonomous"]
PATTERNS = ["a single tool-calling loop with a review gate",
            "a supervisor delegating to task-scoped sub-agents",
            "parallel fan-out over independent work items with a cheap aggregator",
            "a quarantined reader feeding a privileged executor structured data only",
            "a durable workflow with persisted checkpoints and human approval timers",
            "a router that triages locally and escalates hard steps to a frontier model",
            "an adversarial reviewer checking a producer agent's output",
            "a retrieval-first loop with grep-style navigation over an indexed corpus"]
WINS = ["reviewer throughput roughly doubled",
        "median handling time fell by about a third",
        "backlog age dropped from weeks to days",
        "escalation rate to specialists fell materially",
        "first-pass acceptance rose into the high eighties",
        "unit cost per processed item fell by over half",
        "coverage extended to a long tail nobody had staffed",
        "cycle time became predictable enough to publish an SLA"]
TROUBLES = ["the agent silently narrowed scope on ambiguous items",
            "step counts doubled after a model upgrade with no accuracy gain",
            "memory writes accumulated stale preferences and had to be garbage-collected",
            "tool descriptions drifted out of sync with the underlying API",
            "reviewers rubber-stamped output until a sampling audit was introduced",
            "prefix caching broke when a timestamp was injected into the system prompt",
            "long-context retrieval degraded in the middle of the window",
            "an upstream schema change produced confidently wrong extractions",
            "cost per run developed a long tail that medians hid",
            "the verification step was skipped whenever the model was confident"]
FIXES = ["an explicit verification step before the agent may finish",
         "duplicate-call detection with a hard step budget",
         "trajectory-level grading on a curated regression set",
         "identity propagation into the tool layer so authorisation happens there",
         "an egress allowlist and per-task credential scoping",
         "asymmetric compaction that preserves decisions and discards tool bodies",
         "idempotency keys on every mutating call",
         "a sampling audit with grader-human agreement tracked as a metric",
         "per-step thinking-budget ceilings",
         "schema validation of tool output before it re-enters the context"]
RATE_METRICS = ["trajectory pass rate", "verified-completion rate",
                "grader-human agreement", "first-pass acceptance",
                "tool selection accuracy", "context budget utilisation"]
METRICS = ["trajectory pass rate", "irreversible-action escalation rate",
           "steps per completed task", "verified-completion rate",
           "cost per resolved item", "grader-human agreement",
           "p95 end-to-end latency", "injection canary trip rate",
           "context budget utilisation", "tool selection error rate"]
JURISDICTIONS = ["the European Union", "the United Kingdom", "California",
                 "New York State", "Colorado", "Texas", "Canada", "Brazil",
                 "Japan", "South Korea", "Singapore", "Australia", "India",
                 "Switzerland", "Norway", "the United Arab Emirates", "Saudi Arabia",
                 "South Africa", "Mexico", "Chile", "Indonesia", "Nigeria",
                 "Kenya", "New Zealand"]
GLOSSARY = [
    ("Agentic loop", "the control cycle in which a model repeatedly chooses a tool call or a final answer"),
    ("Tool schema", "the machine-readable description of a callable operation exposed to a model"),
    ("Context engineering", "the discipline of deciding what occupies a model's finite window"),
    ("Compaction", "replacing older conversation turns with a lossy summary to reclaim context"),
    ("Asymmetric compaction", "compaction that preserves the instruction block and recent turns verbatim"),
    ("Working memory", "the live context window of an in-flight run"),
    ("Episodic memory", "the stored trajectory of past runs, available for retrieval"),
    ("Semantic memory", "a curated store of durable facts and preferences an agent may consult"),
    ("Write policy", "the rule determining which observations are committed to durable memory"),
    ("Sub-agent", "a child run whose purpose is to absorb search cost and return a conclusion"),
    ("Trajectory", "the full ordered record of context, tool calls and results for one run"),
    ("Trajectory grading", "evaluating how a task was done rather than only the final answer"),
    ("Step budget", "a hard cap on loop iterations, used to bound cost and detect thrash"),
    ("Thinking budget", "the number of tokens a model may spend reasoning before answering"),
    ("Inference-time compute", "quality purchased by spending more tokens at request time"),
    ("Router", "a component directing each step to the cheapest model that can handle it"),
    ("Model gateway", "the shared ingress handling routing, fallback, quotas and spend caps"),
    ("Tool registry", "a versioned catalogue of tools with permissions and ownership"),
    ("Durable execution", "modelling a run as a persisted, replayable workflow"),
    ("Checkpointing", "persisting intermediate state so a failed run resumes rather than restarts"),
    ("Compensating action", "an operation that undoes a partially applied sequence"),
    ("Idempotency key", "a caller-supplied token making a repeated mutating call safe"),
    ("Prompt injection", "untrusted content that the model treats as instruction"),
    ("Indirect injection", "injection delivered through content the agent retrieves rather than user input"),
    ("Lethal trifecta", "the dangerous conjunction of untrusted input, private data and an exfiltration path"),
    ("Quarantined agent", "a run that reads untrusted content and may only emit structured data"),
    ("Confused deputy", "a privileged component misused by a less privileged requester"),
    ("Identity propagation", "carrying the requester's identity to the tool layer for authorisation"),
    ("Egress allowlist", "an explicit list of network destinations a sandbox may reach"),
    ("Blast radius", "the worst-case extent of damage an autonomous run can cause"),
    ("Sandbox", "an isolated execution environment constraining what an agent can touch"),
    ("MicroVM", "a lightweight virtual machine used for stronger per-session isolation"),
    ("Capability failure", "a failure because the model cannot perform the task at all"),
    ("Grounding failure", "a failure caused by acting on a false belief about the environment"),
    ("Specification failure", "doing what was literally asked rather than what was meant"),
    ("Compounding failure", "an early error that invalidates every subsequent step"),
    ("Thrash", "repeating a failing call with cosmetic variations"),
    ("Premature completion", "declaring success without verification"),
    ("Scope narrowing", "silently reducing a task to the tractable part of it"),
    ("Verification step", "an explicit check that the work is done before the loop may exit"),
    ("Adversarial review", "a second agent, differently prompted, checking the first one's work"),
    ("Fan-out", "parallel dispatch of independent work items to separate runs"),
    ("Aggregator", "a cheap final step combining parallel sub-results"),
    ("Prefill", "ingesting the prompt to populate the attention cache before generation"),
    ("Decode", "generating output tokens one at a time after prefill"),
    ("Time to first token", "wall-clock latency from request to the first emitted token"),
    ("Prefix caching", "reusing the computed cache for an unchanged prompt prefix"),
    ("Key-value cache", "stored attention state whose size grows with context length"),
    ("Grouped-query attention", "sharing key-value heads across query heads to shrink the cache"),
    ("Multi-head latent attention", "compressing key-value state into a lower-dimensional latent"),
    ("Mixture of experts", "a sparse architecture activating a subset of parameters per token"),
    ("Active parameters", "the parameters actually used per token in a sparse model"),
    ("Quantisation", "storing weights at reduced precision to cut memory and bandwidth"),
    ("Weight-only quantisation", "quantising weights while keeping activations at higher precision"),
    ("Importance-matrix quantisation", "using calibration statistics to allocate bits where they matter"),
    ("Perplexity delta", "the degradation in predictive loss introduced by quantisation"),
    ("Offload", "placing weights or cache in system memory when device memory is exhausted"),
    ("Layer offload ratio", "the fraction of model layers resident on the accelerator"),
    ("Memory bandwidth bound", "a workload limited by memory throughput rather than arithmetic"),
    ("Compute bound", "a workload limited by arithmetic throughput"),
    ("Arithmetic intensity", "the ratio of arithmetic operations to bytes moved"),
    ("Batch invariance", "producing identical outputs regardless of how requests are batched"),
    ("Speculative decoding", "drafting tokens with a cheap model and verifying with the target"),
    ("Draft model", "the small model proposing tokens in speculative decoding"),
    ("Acceptance rate", "the fraction of speculatively drafted tokens the target model keeps"),
    ("Continuous batching", "admitting new requests into a running batch as slots free up"),
    ("Parallel slots", "concurrent request capacity that partitions the attention cache"),
    ("Context window", "the maximum tokens a model can attend over in one request"),
    ("Effective context", "the span over which a model actually retrieves reliably"),
    ("Needle retrieval", "a test of whether a fact buried in long context can be recovered"),
    ("Position extrapolation", "serving context beyond the length a model was trained on"),
    ("Tokeniser drift", "differing token counts for identical text across model families"),
    ("Energy per token", "joules consumed per generated or ingested token"),
    ("Power limit", "the board power ceiling enforced by firmware"),
    ("Thermal throttling", "clock reduction triggered by temperature limits"),
    ("Model-graded evaluation", "using a model as the grader of another model's output"),
    ("Grader calibration", "measuring and correcting grader agreement against human judgement"),
    ("Gold set", "a human-labelled reference set used to calibrate automated grading"),
    ("Held-out set", "evaluation data kept private to resist contamination"),
    ("Contamination", "benchmark data having leaked into training data"),
    ("Rolling refresh", "periodically replacing benchmark tasks to limit gaming"),
    ("Regression set", "a curated task set run on every change to detect degradation"),
    ("Acceptance criteria", "the explicit conditions under which output is considered correct"),
    ("Escalation path", "the defined route by which an agent hands a case to a human"),
    ("Review gate", "a mandatory human checkpoint before an action takes effect"),
    ("Human in the loop", "a design requiring human approval of consequential steps"),
    ("Human on the loop", "a design in which humans supervise and may intervene but do not approve each step"),
    ("Autonomy tier", "the classification of how much a system decides without review"),
    ("Spend cap", "a hard budget ceiling enforced before a request is served"),
    ("Token tail", "the long right tail of per-run token consumption"),
    ("Unit economics", "cost per resolved item, the figure deployments are judged on"),
    ("Barbell strategy", "concentrating work on very cheap and very capable models"),
    ("Model card", "structured documentation of a model's intended use and limitations"),
    ("Open-weight model", "a model whose parameters are published for local execution"),
    ("Local inference", "running a model on hardware the operator controls"),
    ("Air-gapped deployment", "operation with no network path off the machine"),
    ("Tool server", "a process exposing tools to a model host over a defined protocol"),
    ("Supply-chain review", "auditing third-party tool servers as privileged code"),
    ("Description-as-code", "treating tool descriptions as reviewed, versioned artefacts"),
    ("Computer use", "driving a graphical interface by screenshot and synthetic input"),
    ("Replay", "re-running a trajectory with its exact recorded context"),
    ("Run diff", "comparing two trajectories of the same task to localise a change"),
    ("Behavioural search", "querying stored trajectories by what the agent did"),
    ("Canary prompt", "a planted instruction used to detect injection in production"),
    ("Policy engine", "the component deciding whether a proposed action is permitted"),
    ("Dry run", "executing a plan with side effects suppressed for inspection"),
    ("Shadow mode", "running an agent alongside a human process without acting"),
    ("Backstop", "a deterministic rule overriding the model on critical conditions"),
    ("Kill switch", "a mechanism halting all in-flight autonomous runs immediately"),
]
QUESTIONS = [
    "When is a multi-agent architecture actually worth the coordination cost?",
    "How should a team choose between a local model and a hosted frontier model?",
    "What is the minimum viable evaluation setup for a first agentic deployment?",
    "How do you stop an agent from declaring success without checking its work?",
    "What context length does an agentic workload really need?",
    "Why does prefill throughput matter more than decode speed for agents?",
    "How much quality does four-bit quantisation actually cost?",
    "What belongs in durable memory and what should stay in the trajectory?",
    "How do you budget for runs whose token consumption has a long tail?",
    "What is the right granularity for tools exposed to a model?",
    "How should tool output be validated before it re-enters the context?",
    "When is computer use the right interface rather than an API?",
    "How do you measure whether a model upgrade improved an agent?",
    "What does an adequate audit trail for an autonomous action contain?",
    "How do you scope credentials for an agent acting on a user's behalf?",
    "Which defences against indirect prompt injection are worth implementing first?",
    "How do you detect that prefix caching has silently stopped working?",
    "What causes step counts to grow after an apparently better model lands?",
    "How should a review gate decide what to surface to the reviewer?",
    "When should an agent escalate rather than retry?",
    "How do you keep a memory store from poisoning itself over time?",
    "What is the practical ceiling on long-horizon autonomy today?",
    "How do you compare two accelerators fairly for local inference?",
    "What does energy per token tell you that throughput does not?",
    "How do you decide between a larger model quantised harder and a smaller one quantised lightly?",
    "Why does offloading to system memory collapse throughput so sharply?",
    "How many parallel request slots should a single-user local server use?",
    "What should a regression set contain for an agent in production?",
    "How do you grade a trajectory without reading every transcript?",
    "When does sub-agent delegation stop paying for itself?",
]
REFS = [
    "a widely cited analysis of tool-calling loop failure modes",
    "a practitioner survey of context compaction strategies",
    "an empirical study of indirect prompt injection in retrieval pipelines",
    "a technical report on grouped-query attention and cache sizing",
    "a comparative evaluation of four-bit quantisation schemes",
    "a field study of review-gate design in claims processing",
    "an architecture note on durable execution for long-running agents",
    "a measurement study of prefill scaling on consumer accelerators",
    "a critique of answer-level grading for agentic benchmarks",
    "a threat model for third-party tool servers",
    "an analysis of identity propagation patterns in agent platforms",
    "a longitudinal study of memory store degradation",
    "a benchmark of speculative decoding acceptance rates",
    "a report on energy efficiency across accelerator generations",
    "a study of effective versus nominal context length",
    "an evaluation of adversarial review against self-critique",
    "a cost model for inference-time compute under variable thinking budgets",
    "a survey of sandbox escape vectors in agent runtimes",
    "a case study of shadow-mode deployment in hospital operations",
    "an assessment of trajectory replay tooling",
]


def org():
    return "%s %s" % (pick(ORG_A), pick(ORG_B))


def pct(lo, hi):
    return "%d%%" % rnd.randint(lo, hi)


# --- appendix builders ----------------------------------------------------

def appendix_platforms(n=76):
    out = ["## Appendix A — Platform and vendor profiles",
           "",
           "Illustrative profiles of the agent-platform vendors and internal platform",
           "teams referenced throughout this report. Entries are synthetic composites.",
           ""]
    for i in range(n):
        name = org()
        out.append("### A.%d %s" % (i + 1, name))
        out.append("")
        out.append(
            "%s positions itself as %s for %s. The platform covers %s, with %s "
            "handled as a first-class concern rather than left to application teams. "
            "Deployments are typically %s, and the reference architecture is %s."
            % (name,
               pick(["a managed agent platform", "an agent runtime and gateway",
                     "a tool-governance layer", "an evaluation and observability plane",
                     "a durable-execution substrate for agents",
                     "a self-hosted inference and orchestration stack"]),
               pick(SECTORS),
               pick(["model routing and fallback", "tool registry and permissions",
                     "trajectory storage and replay", "sandbox provisioning",
                     "evaluation harnesses and regression sets",
                     "spend caps and quota enforcement"]),
               pick(["identity propagation", "egress control", "credential scoping",
                     "injection canary monitoring", "prefix-cache stability",
                     "per-step budget enforcement"]),
               pick(TIERS), pick(PATTERNS)))
        out.append("")
        out.append(
            "The documented differentiator is %s. Customers in %s report that %s, "
            "while the most common complaint is that %s. Pricing is structured around "
            "%s, which aligns incentives with %s but obscures %s."
            % (pick(["trajectory-level observability", "a strict policy engine",
                     "first-class replay and run diffing", "bring-your-own-model support",
                     "air-gapped deployment", "fine-grained tool permissions",
                     "native durable workflows", "an opinionated evaluation harness"]),
               pick(SECTORS), pick(WINS), pick(TROUBLES),
               pick(["seats plus metered tokens", "resolved items", "runtime hours",
                     "a flat platform fee with metered sandboxes", "tool-call volume"]),
               pick(METRICS), pick(["the token tail", "sandbox cost at peak",
                                    "evaluation overhead", "egress charges",
                                    "the cost of failed runs"])))
        out.append("")
        out.append(
            "Technical notes: context budgets default to %s tokens with %s; the "
            "supported autonomy ceiling is %s; and %s is required before any "
            "irreversible action. Observed %s sits around %s on the vendor's own "
            "published regression set, measured with %s."
            % (format(rnd.choice([16384, 32768, 65536, 131072, 262144]), ","),
               pick(["asymmetric compaction", "summary-on-overflow",
                     "retrieval-first assembly", "sub-agent offloading of search"]),
               pick(TIERS),
               pick(["human confirmation", "a policy-engine decision",
                     "a dry run with side effects suppressed",
                     "a second-agent review"]),
               pick(RATE_METRICS), pct(61, 96), pick(["trajectory grading",
                                                 "a calibrated model grader",
                                                 "a human gold set of 200 tasks",
                                                 "sampling audit at 5% of runs"])))
        out.append("")
    return out


def appendix_cases(n=56):
    out = ["## Appendix B — Deployment case studies", "",
           "Composite case studies illustrating the adoption progression described in",
           "section 13. Figures are illustrative.", ""]
    for i in range(n):
        name = org()
        sector = pick(SECTORS)
        task = pick(TASKS)
        out.append("### B.%d %s — %s" % (i + 1, name, task))
        out.append("")
        out.append(
            "%s operates in %s and introduced an agent for %s in %s of 2026. The "
            "system runs at the %s tier, implemented as %s. Scope was deliberately "
            "narrow at launch: %s items per week, one business unit, and a review "
            "gate on every output."
            % (name, sector, task,
               pick(["the first quarter", "the second quarter", "the third quarter",
                     "late 2025", "early 2026"]),
               pick(TIERS), pick(PATTERNS), format(rnd.randint(200, 40000), ",")))
        out.append("")
        out.append(
            "Within two quarters, %s. The team attributes this primarily to %s rather "
            "than to model capability, noting that the first model upgrade mid-project "
            "changed %s by less than the scaffolding work did. Unit cost settled at "
            "roughly %s per resolved item against a prior figure of %s."
            % (pick(WINS),
               pick(["tool consolidation", "better context assembly",
                     "an explicit verification step", "clearer acceptance criteria",
                     "reviewer interface design", "a curated regression set"]),
               pick(METRICS),
               "$%.2f" % (rnd.randint(2, 180) / 100.0),
               "$%.2f" % (rnd.randint(40, 1200) / 100.0)))
        out.append("")
        out.append(
            "Problems encountered: %s, and separately %s. Both were addressed by "
            "introducing %s. The team's stated lesson is that %s, and their current "
            "blocker on further autonomy is %s."
            % (pick(TROUBLES), pick(TROUBLES), pick(FIXES),
               pick(["evaluation is the product, not a phase",
                     "the organisation could not write down its own rules until the agent forced it to",
                     "reviewer trust had to be earned with a sampling audit",
                     "the integration and permission work dwarfed the model work",
                     "step count is as important a metric as accuracy",
                     "verification, not capability, is the binding constraint"]),
               pick(["the absence of a reliable verification signal",
                     "identity propagation in a legacy internal API",
                     "regulatory review of automated decisions",
                     "cost variance at the token tail",
                     "insufficient trajectory observability",
                     "a long tail of edge cases with no ground truth"])))
        out.append("")
        out.append(
            "Measured outcomes tracked: %s, %s and %s. Reporting cadence is %s, and "
            "the agent is halted automatically when %s."
            % (pick(METRICS), pick(METRICS), pick(METRICS),
               pick(["weekly", "fortnightly", "monthly", "per release"]),
               pick(["the injection canary trips",
                     "trajectory pass rate falls below the agreed floor",
                     "step count exceeds twice the rolling median",
                     "spend exceeds the daily cap",
                     "the verification step fails twice consecutively"])))
        out.append("")
    return out


def appendix_incidents(n=50):
    out = ["## Appendix C — Incident post-mortems", "",
           "Synthetic post-mortems in a consistent format, illustrating the failure",
           "taxonomy in section 7 and the security discussion in section 8.", ""]
    for i in range(n):
        name = org()
        out.append("### C.%d %s — %s" % (i + 1, name, pick([
            "unauthorised data egress via retrieved content",
            "duplicate mutating calls after a timeout",
            "silent scope narrowing on ambiguous cases",
            "cost spike from an unbounded thinking budget",
            "stale memory causing systematically wrong routing",
            "prefix cache invalidation and a tenfold latency regression",
            "a tool description change that broke selection",
            "sandbox egress misconfiguration",
            "confused-deputy access to another tenant's records",
            "premature completion on a multi-step migration",
            "offload-induced timeout cascade under long context",
            "schema drift producing confidently wrong extractions"])))
        out.append("")
        out.append(
            "**Impact.** %s affected approximately %s items over %s before detection. "
            "Severity was assessed as %s; %s."
            % (pick(["A production agent handling " + pick(TASKS),
                     "A supervised workflow in " + pick(SECTORS),
                     "An internal platform run supporting " + pick(SECTORS)]),
               format(rnd.randint(3, 9000), ","),
               pick(["four hours", "two days", "a week", "eleven days", "a single batch window"]),
               pick(["low", "moderate", "high", "critical"]),
               pick(["no customer data left the perimeter",
                     "a limited set of records was exposed to an internal audience",
                     "all affected items were reprocessed",
                     "manual remediation took two engineer-weeks",
                     "the run was halted by the step budget before completion"])))
        out.append("")
        out.append(
            "**Root cause.** %s. The contributing factor was that %s, and the "
            "condition went unnoticed because %s."
            % (pick(["Untrusted retrieved content was placed in the privileged context",
                     "A retried tool call lacked an idempotency key",
                     "The verification step was advisory rather than enforced",
                     "Authorisation happened at the application layer, not the tool layer",
                     "The context assembler injected a volatile timestamp into the prefix",
                     "The memory write policy had no expiry",
                     "Device memory was exhausted and the runtime silently offloaded",
                     "Tool output was trusted without schema validation"]),
               pick(TROUBLES),
               pick(["monitoring tracked accuracy but not step count",
                     "the dashboard aggregated over a window that hid the spike",
                     "trajectory retention was too short to replay the run",
                     "the canary prompt had been removed during a refactor",
                     "reviewers were sampling at a rate too low to catch it",
                     "alerting thresholds were set on medians"])))
        out.append("")
        out.append(
            "**Remediation.** Immediate: %s. Structural: %s, plus %s. Detection "
            "improved by adding %s to the standing dashboard and %s to the "
            "regression set."
            % (pick(["the agent was placed in shadow mode",
                     "autonomous execution was suspended and the queue drained manually",
                     "the offending tool server was pinned to a previous version",
                     "the egress allowlist was tightened to a single destination",
                     "the step budget was halved pending investigation"]),
               pick(FIXES), pick(FIXES), pick(METRICS),
               pick(["a reproduction of the exact trajectory",
                     "three adversarial injection cases",
                     "a long-context retrieval probe",
                     "a duplicate-call scenario",
                     "an offload-threshold latency case"])))
        out.append("")
    return out


def appendix_evals(n=34):
    out = ["## Appendix D — Evaluation suite specifications", "",
           "Specification sketches for the kind of curated, trajectory-graded suites",
           "described in section 6.", ""]
    for i in range(n):
        out.append("### D.%d %s suite" % (i + 1, pick(SECTORS).title()))
        out.append("")
        out.append(
            "**Scope.** %s tasks drawn from production traffic over %s, stratified by "
            "difficulty and refreshed %s. %s of tasks are held out privately to "
            "resist contamination."
            % (rnd.randint(40, 600),
               pick(["one month", "a quarter", "six months", "a full year"]),
               pick(["monthly", "quarterly", "per model upgrade"]), pct(15, 40)))
        out.append("")
        out.append(
            "**Grading.** Primary metric is %s, graded at the trajectory level. "
            "Secondary metrics: %s and %s. Grading uses %s, calibrated against a "
            "human gold set of %s tasks; grader-human agreement is tracked and the "
            "suite is considered invalid below %s."
            % (pick(METRICS), pick(METRICS), pick(METRICS),
               pick(["a model grader with a rubric", "deterministic assertions plus a model grader",
                     "a two-grader panel with disagreement escalation",
                     "execution-based checking where the task permits"]),
               rnd.randint(50, 400), pct(78, 93)))
        out.append("")
        out.append(
            "**Failure classes tracked.** %s, %s and %s, reported separately rather "
            "than folded into an aggregate score. A change that holds %s constant "
            "while increasing %s is treated as a regression."
            % (pick(["capability failures", "grounding failures",
                     "specification failures", "compounding failures"]),
               pick(["thrash", "premature completion", "scope narrowing"]),
               pick(["unescalated irreversible actions", "injection canary trips",
                     "budget overruns"]),
               pick(METRICS), pick(METRICS)))
        out.append("")
    return out


def appendix_hardware(n=46):
    out = ["## Appendix E — Local inference sizing notes", "",
           "Worked sizing notes of the kind section 10 argues for. Numbers are",
           "illustrative; the point of the accompanying benchmark tool is that you",
           "should measure your own.", ""]
    for i in range(n):
        vram = pick([6, 8, 12, 16, 20, 24, 32, 48])
        params = pick(["4B dense", "8B dense", "9B dense", "12B dense", "14B dense",
                       "20B dense", "27B dense", "32B dense",
                       "30B-A3B sparse", "35B-A3B sparse", "70B dense"])
        quant = pick(["Q4_K_M", "IQ4_XS", "Q5_K_M", "Q6_K", "Q8_0", "IQ3_XXS"])
        out.append("### E.%d %s at %s on a %d GB card" % (i + 1, params, quant, vram))
        out.append("")
        out.append(
            "Weights occupy roughly %.1f GB at this quantisation, leaving about "
            "%.1f GB for the key-value cache, runtime overhead and the desktop "
            "compositor. With %s the cache costs on the order of %.0f MB per "
            "thousand tokens, so the practical context ceiling before offload is "
            "somewhere near %s tokens."
            % (rnd.uniform(2.0, vram * 0.95), rnd.uniform(0.4, 4.0),
               pick(["grouped-query attention", "multi-head latent attention",
                     "eight key-value heads", "a compressed latent cache"]),
               rnd.uniform(8, 140), format(rnd.choice([4096, 8192, 16384, 32768, 65536]), ",")))
        out.append("")
        out.append(
            "Expected behaviour: decode is %s and holds roughly flat until offload "
            "begins, after which it falls %s. Prefill is %s and is where generational "
            "differences in arithmetic throughput show up most strongly. %s is the "
            "metric to watch, because %s."
            % (pick(["memory-bandwidth bound", "bandwidth bound with a small compute floor"]),
               pick(["by an order of magnitude", "five- to twentyfold",
                     "sharply and non-linearly", "to single-digit tokens per second"]),
               pick(["compute bound", "compute bound at long context",
                     "compute bound once the batch of prompt tokens is large"]),
               pick(["Energy per thousand prefill tokens", "Prefill tokens per second at 20k",
                     "Time to first token at the working context length",
                     "Joules per generated token"]),
               pick(["an agent re-ingests its context on every step",
                     "it is the figure that decides whether a loop feels usable",
                     "it is comparable across cards in a way that wall-clock is not",
                     "it exposes offload immediately"])))
        out.append("")
    return out


def appendix_regulatory(n=24):
    out = ["## Appendix F — Regulatory digest by jurisdiction", "",
           "Illustrative digest in a consistent shape, expanding on section 14.", ""]
    for i in range(min(n, len(JURISDICTIONS))):
        j = JURISDICTIONS[i]
        out.append("### F.%d %s" % (i + 1, j))
        out.append("")
        out.append(
            "Obligations in %s attach primarily to %s. Transparency duties cover %s, "
            "and are generally regarded as the settled part of the regime. Human "
            "oversight of consequential automated decisions is %s, which in practice "
            "means deploying organisations implement %s and document it."
            % (j,
               pick(["deployment context and risk classification",
                     "sectoral supervision of existing regulated activities",
                     "data protection law extended to automated decisions",
                     "consumer protection and unfair practices rules"]),
               pick(["disclosure that a user is interacting with an AI system",
                     "documentation of training data provenance",
                     "logging and retention of automated decisions",
                     "notice and an explanation right for affected individuals"]),
               pick(["explicitly required", "required for a defined high-risk subset",
                     "encouraged by guidance rather than mandated",
                     "required only in regulated sectors"]),
               pick(["a review gate", "a documented escalation path",
                     "a shadow-mode trial followed by staged rollout",
                     "a policy engine with an audited rule set"])))
        out.append("")
        out.append(
            "For agentic systems specifically, the unsettled questions are %s and %s. "
            "Supervisory attention has concentrated on %s. Enforcement to date is %s."
            % (pick(["allocation of liability across the vendor chain",
                     "what constitutes an adequate audit trail for an autonomous action",
                     "whether an agent can bind a principal contractually",
                     "the status of a model's reasoning trace as a record"]),
               pick(["cross-border data flows in tool calls",
                     "the treatment of tool servers as sub-processors",
                     "retention limits on trajectory logs",
                     "identity assurance for agents acting on a person's behalf"]),
               pick(SECTORS),
               pick(["limited and mostly advisory", "concentrated in a handful of cases",
                     "driven by complaints rather than proactive audit",
                     "focused on disclosure failures"])))
        out.append("")
    return out


def appendix_glossary():
    out = ["## Appendix G — Glossary", "",
           "Working definitions of the vocabulary used throughout this report.", ""]
    for term, definition in GLOSSARY:
        out.append("**%s.** %s. In practice the term is used when %s, and the "
                   "associated failure mode is %s."
                   % (term, definition[0].upper() + definition[1:],
                      pick(["discussing production deployments",
                            "comparing architectures", "reviewing an incident",
                            "sizing hardware for a workload",
                            "specifying an evaluation", "negotiating a security review",
                            "designing a review gate", "budgeting a deployment"]),
                      pick(["silent degradation that monitoring does not surface",
                            "cost growth without a quality gain",
                            "a false sense of safety from a measure that does not bind",
                            "an error that only appears at long context",
                            "over-trust in a self-reported signal",
                            "coordination overhead exceeding the benefit"])))
        out.append("")
    return out


def appendix_faq(n=30):
    out = ["## Appendix H — Practitioner questions", "",
           "Answers in the voice of the report, expanding points made in the main",
           "sections.", ""]
    for i, q in enumerate(QUESTIONS[:n]):
        out.append("### H.%d %s" % (i + 1, q))
        out.append("")
        out.append(
            "The short answer is that %s. The reasoning is that %s, which means %s "
            "in most deployments. Teams that get this wrong typically find that %s, "
            "and the usual remedy is %s."
            % (pick(["it depends on the verification signal available",
                     "the constraint is almost never model capability",
                     "you should measure rather than reason about it",
                     "the cheapest option that meets the quality bar wins",
                     "context discipline beats raw context length",
                     "the answer changes once you look at cost per resolved item"]),
               pick(["an agent re-ingests its context on every step",
                     "coordination overhead compounds faster than division of labour helps",
                     "self-reported completion is unreliable",
                     "attention cost grows with the square of context",
                     "the key-value cache, not the weights, is usually the binding constraint",
                     "graders are biased toward verbose, well-structured answers",
                     "irreversible actions dominate the risk profile"]),
               pick(["prefill throughput is the figure that matters",
                     "a single strong model with good tools is the baseline to beat",
                     "an explicit verification step is mandatory",
                     "the context should be engineered down rather than paid for",
                     "authorisation has to move into the tool layer",
                     "step count belongs on the dashboard next to accuracy"]),
               pick(TROUBLES), pick(FIXES)))
        out.append("")
    return out


def appendix_biblio(n=120):
    out = ["## Appendix I — Annotated bibliography", "",
           "Pointers in the shape of a reading list. These are descriptive",
           "placeholders rather than real citations.", ""]
    for i in range(n):
        out.append(
            "%d. %s, %s (%s). %s. Relevant to section %d; the useful contribution is "
            "%s, and the stated limitation is %s."
            % (i + 1, org(), pick(REFS),
               pick(["2024", "2025", "early 2026", "mid 2026", "2026"]),
               pick(["Methodologically careful and narrow in scope",
                     "Broad survey, light on measurement",
                     "Strong empirical section, weak threat model",
                     "Useful taxonomy, contested conclusions",
                     "Replicated independently at smaller scale",
                     "Widely cited and frequently over-generalised"]),
               rnd.randint(1, 17),
               pick(["a reusable failure taxonomy", "a measurement protocol others adopted",
                     "a clear negative result", "a cost model that survives contact with practice",
                     "an architectural pattern now considered standard",
                     "a dataset that became a de facto regression set"]),
               pick(["a single-vendor sample", "no held-out evaluation",
                     "results that predate current quantisation methods",
                     "a deployment context too narrow to generalise",
                     "grader calibration that was not reported",
                     "hardware that is no longer representative"])))
        out.append("")
    return out


# --- assembly -------------------------------------------------------------

def build_full():
    with open(CORE, "r", encoding="utf-8") as fh:
        core = fh.read().rstrip()
    parts = [core, "", "---", "",
             "# Appendices",
             "",
             "The appendices below extend the report with reference material: platform",
             "profiles, deployment case studies, incident post-mortems, evaluation suite",
             "specifications, hardware sizing notes, a regulatory digest, a glossary,",
             "practitioner questions and a reading list. They are synthetic composites,",
             "generated deterministically so that every card under test receives",
             "byte-identical prompts.",
             ""]
    for builder in (appendix_platforms, appendix_cases, appendix_incidents,
                    appendix_evals, appendix_hardware, appendix_regulatory,
                    appendix_glossary, appendix_faq, appendix_biblio):
        parts.extend(builder())
        parts.append("")
    text = "\n".join(parts)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    with open(FULL, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


def slice_to_tokens(text, target_tokens, chars_per_token=CHARS_PER_TOKEN):
    """Cut at a paragraph boundary near the character budget for a token target."""
    budget = int(target_tokens * chars_per_token)
    if len(text) <= budget:
        return text
    cut = text.rfind("\n\n", 0, budget)
    if cut < budget * 0.90:          # no nearby paragraph break: fall back to a word
        cut = text.rfind(" ", 0, budget)
    return text[:max(cut, 1)].rstrip()


def main():
    text = build_full()
    if not os.path.isdir(SIZES_DIR):
        os.makedirs(SIZES_DIR)
    print("full corpus: %s chars (~%d tokens at %.2f chars/token)"
          % (format(len(text), ","), len(text) / CHARS_PER_TOKEN, CHARS_PER_TOKEN))
    biggest = max(t for _, t in TARGETS)
    if len(text) < biggest * CHARS_PER_TOKEN:
        print("WARNING: corpus is short of the %d-token target by %s chars"
              % (biggest, format(int(biggest * CHARS_PER_TOKEN - len(text)), ",")),
              file=sys.stderr)
    for label, target in TARGETS:
        chunk = slice_to_tokens(text, target)
        path = os.path.join(SIZES_DIR, "agentic-ai-%s.txt" % label)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(chunk)
        print("  %-5s %9s chars  ~%6d tokens" % (label, format(len(chunk), ","),
                                                 len(chunk) / CHARS_PER_TOKEN))


if __name__ == "__main__":
    main()
