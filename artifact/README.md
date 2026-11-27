# Evaluation fixtures

This is the fixture subset used by the planner evaluator: copy-only preparation,
the expense-summary workload, four scenarios, the task, and pinned upstream skills
with their license and provenance manifest. The workload intentionally contains
an unfixed refund bug.

These files were copied unchanged from the source workspace at commit
`057f54c`. Local runs, configuration, caches, collector/checker scripts, and
raw session records were excluded. The Qwen 3.5 9B documentation-injection
runs cited in the course report are kept separately in
`evidence/qwen35-documentation-injection/`. The independent evaluator lives in
`planner/evaluation.py`; it requires verified OS confinement before executing
any generated code.

From this repository root:

```sh
python3 artifact/scripts/prepare.py normal --output /tmp/planner-fixture-001
```

Preparation copies fixtures only. It does not run an agent or confer filesystem
isolation. See [evaluation instructions](../docs/EVALUATION.md).
