# Planner supervisor

Planner checks coding-agent file permissions, limits delegated authority, and
records where model inputs came from. Its Ubuntu sandbox runs offline commands
in a private workspace and keeps edits separate for review.

[Architecture](docs/ARCHITECTURE.md) · [Usage](docs/USAGE.md) ·
[Ubuntu setup](docs/LINUX_SANDBOX.md) · [Limitations](docs/KNOWN_LIMITATIONS.md)

## What works

| Area | Status |
| --- | --- |
| Policies, approvals, delegation and provenance | Implemented and tested through public APIs |
| Ubuntu offline sandbox | 23 contract checks pass on the recorded Ubuntu guest |
| OpenCode 2.2.5 bridge | Four integration fixtures pass with a local provider |
| Full model-connected launcher and live-project execution | Gated; integration and acceptance remain |

The sandbox uses Bubblewrap, AppArmor and seccomp. It has no provider network
access or automatic merge. Read the [sandbox restrictions](docs/LINUX_SANDBOX.md)
before running a task. Recorded results and source hashes are in the
[review log](docs/REVIEW.md) and [evidence](evidence/linux-private-workspace.json).

## Try it

Requires Python 3.11+. Run commands from this repository root. Keep the trusted
state database outside the target project and outside worker access.

```sh
planner_state=/tmp/planner-control/control.sqlite

# Create a proposal; replace TASK with the returned ID.
python3 -m planner --state "$planner_state" propose /absolute/project --template source
python3 -m planner --state "$planner_state" review TASK
python3 -m planner --state "$planner_state" approve-task TASK --revision 1

# Approve a worker and check an edit. These commands do not execute the edit.
python3 -m planner --state "$planner_state" assign TASK worker --read 'src/**' --write 'src/**'
python3 -m planner --state "$planner_state" decide TASK worker write src/refunds.py
```

Follow [Ubuntu setup and execution](docs/LINUX_SANDBOX.md) to run a command under
an approved `shell` grant. The [usage guide](docs/USAGE.md) covers custom TOML
policies, exceptions, delegation, recovery and memory.

Generate an example provenance report with
`python3 -m planner.demo /tmp/planner-records.html`, then open that file locally.

## Development

Python code uses the standard library. Node runs the bridge tests; mypy is a
development tool. Real connector diagnostics require OpenCode **2.2.5**.

```sh
python3 -W error::ResourceWarning -m unittest discover -s planner/tests
python3 -m mypy --config-file pyproject.toml planner
```

Loopback tests need permission to bind local sockets. Platform-specific skips
provide no confinement evidence. See [repository layout](docs/REPOSITORY.md),
[enforcement status](docs/ENFORCEMENT.md), [connector](docs/OPENCODE.md),
[control plane](docs/CONTROL_PLANE.md), [mediation](docs/MEDIATION.md),
[context](docs/CONTEXT.md), [provenance](docs/PROVENANCE.md) and
[evaluation](docs/EVALUATION.md) for details.
