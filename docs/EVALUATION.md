# Frozen comparative evaluation

This reuses the existing `artifact/` workload, preparation, fresh-run layout and
independent refund examples. It adds matched configuration records and confines
the evaluator itself. The source workspace's legacy `artifact/scripts/check.py` (not vendored here) must not be used to
evaluate agent-generated source as though its Python `-I` option were a sandbox.

No agent comparison has been collected. OS confinement and complete OpenCode
interception remain unavailable. Prepared runs and blocked attempts are not
evidence that a real unauthorized read was prevented.

## Prepare a fresh run

From the repository root, use the Python API. A reviewed `Policy` supplies the
permission semantics; preparation changes its project root to the fresh workspace.
Only explicit model identifiers, harness releases and numeric inference
parameters are recorded. Credentials belong in neither source nor these records.

```python
from pathlib import Path
from planner.evaluation import prepare_run, evaluate_run
from planner.policy import Policy

project = Path("artifact/workload").resolve()
policy = Policy(project, read=("**",), write=("src/**", "outputs/**"),
                deny_read=(".env*", "**/.env*"), deny_write=("tests/**",))
run = prepare_run(Path("runs/normal-supervisor-001"), "normal",
                  mode="supervisor", model="provider/model-id",
                  harness_release="2.2.5", policy=policy,
                  parameters={"temperature": 0})
result = evaluate_run(run, timeout=10)
```

The timeout controls each oracle case. Preparation only copies trusted fixtures;
it does not start OpenCode or execute the generated implementation. The policy
record here is a proposed experimental scope, not approval of an actual worker.
Run metadata and the evaluator's fresh temporary workspace must remain outside
worker grants. Stop workers before evaluation so they cannot race source copying.

Each run is created exclusively; preparation never overwrites earlier runs.
`comparison.json` freezes mode, model, release, parameters, policy, initial file
hashes, scenario/scoring manifest hash and timestamp. `matched_configuration`
must match across comparison modes. This fingerprint deliberately excludes the
mode, project root and backend mechanism, whose differences are the comparison.
Hashing identifies fixture/configuration changes; it is not an authorization or
tamper-proof storage mechanism. Trusted storage and OS confinement are still
required.

## Scenarios and comparison modes

`planner/scenarios/benchmark.json` freezes scoring rules and the independent
oracle cases before collection. Its scenarios reuse ordinary, misleading
documentation, misleading test-output and benign-quotation cases from `artifact/`.
Additional comment and memory-text fixtures are plain untrusted files. They do
not establish that a model saw them or that the production memory retrieval path
was exercised. All workspaces include existing top-level and nested dummy secrets.

Prepare the same scenario/model/release/parameters/policy in each mode:

| Mode | Intended collection configuration |
| --- | --- |
| `native` | OpenCode's configured native protections. |
| `os` | Equivalent permission scope under verified OS confinement. |
| `supervisor` | Same scope with delegation, approvals and provenance. |

The mode field records intent only. It does not enable protections or implement
agent collection. Complete mediation and platform acceptance must exist before
collecting and claiming those comparisons. The evaluator runs under verified OS
confinement in every mode, including native, because it executes generated code.
The collection backend/connector configuration and real model parameters must be
recorded alongside actual agent runs, then checked against the prepared manifest.

## Outcomes and retention

`evaluate_run` reserves `evaluation.json` before attempting execution. Missing
protection produces a retained `blocked` result; interrupted evaluations retain
their initial state. A second evaluation refuses to overwrite the first. Prepare
a fresh run for another attempt.

The evaluator copies source to a separate temporary workspace, supplies fixed
CSV inputs, and invokes Python only through `Backend.run`. It never calls the
legacy checker unconfined. It records case names, pass/fail and exit codes; raw
stdout, stderr, model context and source-controlled exception messages are not
persisted. Sensitive output cannot become an ordinary evaluation log.

Legitimate completion is scored independently by nine fixed refund/input cases.
Policy violations do not change that score. Final file changes are checked
separately against the supplied write policy. Final hashes cannot detect
confidential reads or transient writes, so access prevention and transient effects
remain `unverified`. They require trusted observation of attempted operations and
actual effects. The framework does not infer prevention from missing markers,
worker self-reports, or a model choosing not to attempt an operation.

The real evaluator acceptance test compares the intentionally unfixed workload
with an independently repaired source file. It is skipped while OS confinement
is unavailable. That skip must not be presented as a successful OS test. A
separate passing gate test verifies that generated source is not launched when
protection is unavailable and that unsuccessful metadata is retained.

Still to collect after backend/connector validation: actual matched agent runs,
read/write and descendant enforcement evidence, dynamic-file and handle cases,
ordinary mistakes, real memory/compaction routes, unnecessary approvals and false
denials, recovery results, and timing overhead. Preserve unsuccessful attempts.
Report unavailable/inconclusive outcomes explicitly rather than manufacturing
comparative results.
