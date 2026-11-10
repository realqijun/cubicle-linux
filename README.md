# Planner supervisor

A Python supervisor for coding agents. It checks file permissions, limits
delegated authority, and records where model inputs came from.

[Architecture diagram](../docs/ARCHITECTURE.md) · [Usage guide](docs/USAGE.md) ·
[Known limitations and Ubuntu retest plan](docs/KNOWN_LIMITATIONS.md)

## Current status

Policy checks, approvals, delegation, provenance, and recovery records are
implemented. The OpenCode 2.2.5 bridge passes fixed integration tests with a local
model-provider fixture.

**Autonomous execution remains gated.** OS confinement, process cleanup, and
complete runtime acceptance still need validation on Ubuntu. See the
[limitations](docs/KNOWN_LIMITATIONS.md) before using it with real tasks.

## Try the control plane

Requires Python 3.11+. Run from the repository root. Keep the state database
outside the target project and outside worker access.

```sh
planner_state=/tmp/planner-control/control.sqlite

# Create a proposal. Use the returned task ID in place of TASK below.
python3 -m planner --state "$planner_state" propose /absolute/project --template source
python3 -m planner --state "$planner_state" review TASK
python3 -m planner --state "$planner_state" approve-task TASK --revision 1

# Assign a worker and check a proposed edit. This does not execute the edit.
python3 -m planner --state "$planner_state" assign TASK worker --read 'src/**' --write 'src/**'
python3 -m planner --state "$planner_state" decide TASK worker write src/refunds.py
```

Use `--policy PATH` instead of `--template` for your own TOML policy. Bundled
templates cover source edits, inspection, tests, and documentation. The
[usage guide](docs/USAGE.md) covers exceptions, delegation, recovery, and memory.

## Inspect a report

```sh
python3 -m planner.demo /tmp/planner-records.html
```

Open the generated HTML locally. This demo uses real fixture records without
calling a model. Reports distinguish recorded decisions from verified effects.

## Development

The supervisor uses the Python standard library. Connector tests also use Node;
real connector diagnostics require OpenCode **2.2.5**. Mypy is a development tool.

```sh
python3 -m unittest discover -s planner/tests
python3 -m mypy --config-file planner/pyproject.toml planner
```

Tests that bind loopback sockets need host network permission. Skipped OS tests
do not establish confinement. Validation results and the existing artifact
collector failures are recorded in the [review log](docs/REVIEW.md).

## More detail

| Topic | Read |
| --- | --- |
| Components, trust boundaries, and request flow | [Architecture](../docs/ARCHITECTURE.md) |
| Policies and terminal commands | [Usage](docs/USAGE.md) |
| Delegation and recovery state | [Control plane](docs/CONTROL_PLANE.md) |
| OpenCode compatibility and provider setup | [Connector](docs/OPENCODE.md) |
| Tool checks and model-input tracking | [Mediation](docs/MEDIATION.md), [context admission](docs/CONTEXT.md) |
| Project memory and offline reports | [Provenance](docs/PROVENANCE.md) |
| Frozen evaluation scenarios and scoring | [Evaluation](docs/EVALUATION.md) |
