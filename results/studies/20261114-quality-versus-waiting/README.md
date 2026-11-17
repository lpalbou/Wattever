# Four-model quality versus waiting time: saved interim evidence

Read [the current comparison](../../../docs/glm53-flash-comparison.md#four-model-quality-versus-waiting-time).
`plan.json` freezes public128 and practical24 question text, gold answers and coding cases;
`protocol.json` records conditions and scope. One inference attempt is retained per question/condition.
`question-results.csv` is a tidy table keyed by model, mode and question ID for paired analysis.
Each condition directory retains the request-level Responses events, provider usage, observed timestamps,
GPU/RAM telemetry and grades. Protocol probes are unscored and may have background downloading.
Earlier background-download XS accuracy results remain in the separate GLM optimization study.

`grading-sources/` and grading-review JSON files preserve the corrected execution policy and earlier grades;
no inference was repeated for those grader corrections. CPU execution uses constrained pure-function
AST/builtins and a bounded Windows Job; it is not an unrestricted arbitrary-code service.
Source copies preserve the original checkout paths and are review artifacts, not standalone engine launchers.
All real starts/unloads remain owned by Strata Control. No client in this study starts or switches an engine.

Native Linux performance and replacement-platform speed are unmeasured. No OS installation,
repartition, filesystem repair or hardware purchase has occurred.

## Publication note

Before publication, the Windows account name in recorded file paths was replaced with `user` and a private
network address with `192.168.x.x`. No measurement was altered. The SHA-256 values of the edited files, and every
reference to them in this folder and its manifest, were recomputed afterwards.
