# Planner supervisor

Planner checks coding-agent file permissions, limits delegated authority, and
records where model inputs came from. On Ubuntu it runs a model-connected
OpenCode worker against a mediated private copy of the project and applies the
worker's authorized, conflict-free edits back to the original.

[Architecture](docs/ARCHITECTURE.md) · [Usage](docs/USAGE.md) ·
[Connected runtime](docs/CONNECTED_RUNTIME.md) · [Limitations](docs/KNOWN_LIMITATIONS.md)

## What works

| Area | Status |
| --- | --- |
| Policies, approvals, delegation and provenance | Implemented and tested through public APIs |
| Connected Linux runtime: plan, approve, confined OpenCode, apply | Accepted on Ubuntu 24.04.3 x86-64 with a fixed-provider fixture and real LaaS |
| Exceptions, single-use edits, external inputs, recovery | Tested end to end on the same guest |
| Ubuntu offline sandbox | Contract checks pass on the recorded guest |
| macOS and Windows | Not supported; external prerequisites block acceptance |
| Other Linux distributions or architectures | Untested; see [platform limits](docs/KNOWN_LIMITATIONS.md#other-execution-modes-and-platforms) |

The connected runtime combines a FUSE policy mediator with Bubblewrap, AppArmor
and seccomp. Read the [runtime guide](docs/CONNECTED_RUNTIME.md) before running a
task. Results and source hashes are in [evidence](evidence/completion-progress.json).

## Try it

**Fresh Ubuntu 24.04 machine:** follow the [quick start](docs/QUICKSTART.md).
Two scripts install the runtime and run a guided demo.

The control-plane commands below need only Python 3.11+. Run commands from this
repository root. Keep the trusted state database outside the target project and
outside worker access.

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

Follow the [connected runtime guide](docs/CONNECTED_RUNTIME.md) to run a worker,
or [the offline sandbox guide](docs/LINUX_SANDBOX.md) for a plain command. The [usage guide](docs/USAGE.md) covers custom TOML
policies, exceptions, delegation, recovery and memory.

Generate an example provenance report with
`python3 -m planner.demo /tmp/planner-records.html`, then open that file locally.

## Development

Python code uses the standard library. Node runs the bridge tests; mypy is a
development tool. Real connector diagnostics require OpenCode **2.2.5**.

```sh
python3 -W error::ResourceWarning -m unittest discover -s planner/tests
python3 -m mypy --config-file pyproject.toml   # strict; passes
```

Loopback tests need permission to bind local sockets. Platform-specific skips
provide no confinement evidence. See [repository layout](docs/REPOSITORY.md),
[enforcement status](docs/ENFORCEMENT.md), [connector](docs/OPENCODE.md),
[control plane](docs/CONTROL_PLANE.md), [mediation](docs/MEDIATION.md),
[context](docs/CONTEXT.md), [provenance](docs/PROVENANCE.md) and
[evaluation](docs/EVALUATION.md) for details.
