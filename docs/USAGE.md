# Policies and terminal use

Run commands from the repository root with Python 3.11+. The trusted operator
CLI creates and inspects control-plane records and, on the accepted Ubuntu
runtime, launches confined workers with `run`. See the
[connected runtime](CONNECTED_RUNTIME.md) for setup and the run sequence.

## State and policy

The optional [hook-only CLI](#hook-only-opencode) uses the same reviewed records.

Every command requires `--state PATH`. Keep this SQLite file and its sibling
`.memory.sqlite` outside the approved project and outside worker access. The
operator CLI must not be exposed as a worker tool.

Import a TOML policy using `propose PROJECT --policy PATH`, or select a bundled
template with `--template source`, `inspection`, `tests`, or `documentation`.
Review the effective proposal before approving its exact revision.

```toml
[files]
read = ["**"]
write = ["src/**", "outputs/**"]
deny_read = [".env*", "**/.env*", ".git/**"]
deny_write = ["tests/**"]

[tools]
allow = ["file", "shell", "search"]
```

Read and write permissions are independent. Denials take precedence and also
cover descendants of a matching resource. Paths are relative to the approved
project; absolute in-project paths are accepted. Unknown settings are rejected.

Patterns use `/` between components. `*`, `?`, and character classes match inside
one component; a whole-component `**` matches zero or more components. Decisions
resolve symlinks before `..`, check on-disk case and Unicode aliases, and require
both logical and resolved paths to fit the worker's scopes. Hard-linked files
require explicit review. New matching sensitive files are checked on every
decision. These checks do not replace race-safe OS enforcement.

Optional `[external] read` entries must name individual existing absolute files.
A worker needs an exact read exception to use one; external inputs do not grant
writes. Credentials belong in neither policy files nor committed records.

## Common commands

The examples below omit the repeated prefix:

```sh
python3 -m planner --state /tmp/planner-control/control.sqlite
```

Append `--json` before the command for machine-readable output.

| Command | Purpose |
| --- | --- |
| `propose PROJECT --template source` | Create a proposal without starting work |
| `review TASK` | Inspect the policy and revision |
| `approve-task TASK --revision 1` | Approve the reviewed revision |
| `assign TASK WORKER --read 'src/**' --write 'src/**'` | Approve an exact worker assignment |
| `decide TASK WORKER write src/refunds.py` | Record a permission decision without executing an edit |
| `status TASK` / `history TASK` | Inspect task state and decisions |
| `revise TASK --policy PATH` | Replace the proposal and require fresh review |
| `revoke TASK WORKER` / `cancel TASK` | Revoke worker descendants or the entire task |
| `plan PROJECT --prompt TEXT --model M --provider-url URL` | Ask a model for a template-bounded proposal |
| `approve-task TASK --revision N --assign WORKER` | Approve and assign exactly the reviewed scopes |
| `run TASK WORKER --prompt TEXT ...` | Run one confined attempt; `--mode review` retains edits |
| `apply ATTEMPT` / `applications TASK` | Apply a retained changeset / list application outcomes |
| `recover TASK WORKER ...` | Verify cleanup after owner loss, then reconcile |
| `export TASK` | Export readable metadata |
| `report TASK /absolute/report.html` | Generate a report from actual task records |

Run `python3 -m planner --help` or append `--help` to a command for its arguments.

## Exceptions

`request-exception TASK WORKER read RESOURCE` creates a request ID. Inspect it
with `exception ID`, then use `approve-exception ID` or `refuse-exception ID`.
Approval is available only through the trusted operator channel.

An exception binds task, worker, operation, tool, normalized resource, filesystem
identity, policy revision, and expiry. With the connected runtime a once-only
approval is reserved for the next attempt and allows exactly one open of the file;
a write approval is consumed when that staged edit is applied. Outside a run it is
consumed by one authorization dispatch. `--lifetime task` lasts only for that task. Single-use consumption is
atomic across SQLite connections. Failed multi-resource checks consume none of
their approvals. A dispatched approval is not restored after an execution failure.
The original denial remains in the policy.

Sensitive reads require `--confirm-sensitive-provider-disclosure`. Revoking an
approval cannot make a provider forget content it already received. Source IDs
and worker explanations are information, not evidence of human approval.
Supervisor state and writes to supervisor code cannot be granted to workers.

## Delegation

An assignment must fit the task policy and every parent grant. Equal scopes,
literal matching children, and descendants of an approved `directory/**` scope
are supported; ambiguous arbitrary-glob containment is rejected conservatively.

Approve a reusable ceiling for one task, revision, and parent:

```sh
approve-template TASK source-child PARENT --read 'src/**' --write 'src/**'
delegate TASK CHILD --parent PARENT --template source-child --read 'src/file.py' --write 'src/file.py'
```

Assignments covered by that template need no new human approval. Other
assignments do. Policy changes, expiry, and revocation invalidate the relevant
authority. Inspect it with `templates TASK`, `workers TASK`, and `worker TASK ID`.
See [control-plane details](CONTROL_PLANE.md) for the API and transition rules.

## Recovery and memory

`prepare-attempt TASK WORKER`, `attempt ID`, and `attempts TASK` track execution
intent and transition history. `reconcile TASK` marks unresolved work interrupted,
suspends affected workers, and revokes their live exceptions. A database state
change does not prove that any process stopped.

`preflight TASK WORKER --model provider/model` returns exit 2 while protection
is unavailable. `resume` requires fresh protection checks and reconciled cleanup;
it restores durable authority only and starts no process. Its
`--cleanup-verified` option belongs to the trusted operator. Revoked or expired
grants and old approvals cannot be revived by recovery.

Project notes support `memory PROJECT list [--query TEXT]`,
`memory PROJECT inspect --note ID`, and `memory PROJECT delete --note ID`.
Sensitive-derived note text is rejected. Deleted notes cannot be retrieved by
later calls; content already admitted to a caller or provider cannot be recalled.
Reports omit note text. See [provenance](PROVENANCE.md) and
[context admission](CONTEXT.md) for origin and persistence rules.

## Configuration and validation

The CLI exposes `--task-seconds` (default 3600 for new proposals) and
`--denial-limit` (default 3 consecutive identical denials). Connector configuration
selects a provider/model, executable, endpoint, explicit credential environment
name, and finite time/body limits. The harness release is pinned until another
release's paths are verified. See [OpenCode setup](OPENCODE.md).

The project vendors the standard-library `artifact/` preparation fixtures and
uses their JSON-record style. The source workspace's unconfined collector/checker
is not included. The
intentionally buggy workload is an evaluation fixture, not a green application
suite. The [evaluation guide](EVALUATION.md) describes independent scoring,
matched configurations, and which results remain unverified.

## Hook-only OpenCode

`hook` opens the interactive OpenCode CLI with a small tool adapter connected to the existing supervisor.
It is an explicit, less protective mode: it changes the approved original project directly, without OS confinement, checked write-back, model-context provenance, or a claim of complete interception.
The normal `run` command and its Linux protection gates are unchanged.

Create and review a task as usual.
For a first file-only session:

```sh
python3 -m planner --state /tmp/planner-control/control.sqlite propose /absolute/project --template source
python3 -m planner --state /tmp/planner-control/control.sqlite review TASK
python3 -m planner --state /tmp/planner-control/control.sqlite approve-task TASK --revision 1
python3 -m planner --state /tmp/planner-control/control.sqlite assign TASK worker --read '**' --write 'src/**' --tool file
python3 -m planner --state /tmp/planner-control/control.sqlite hook TASK worker \
  --opencode /absolute/path/to/opencode \
  --model provider/model --provider-url https://your-provider.example/v1
```

Replace `TASK` with the returned task ID and supply your actual model and provider endpoint.
Add `--api-key-env YOUR_KEY_VARIABLE` when the provider needs a credential from that environment variable.
The launcher does not import your ordinary OpenCode credentials, plugins, or project configuration.
No global OpenCode settings are changed; typing plain `opencode` continues to use your ordinary setup.
Python 3.11+ and OpenCode **2.2.5** are required.

Add `--prompt 'Your task'` for one noninteractive session.
Without it, the launcher verifies a private server's hook registration before connecting the terminal UI.
Only one root session is bound to a worker; start a new launch to use another session.
The default run limit is one hour (`--run-seconds`).
An idle session or a clean terminal exit does not establish task correctness.

The adapter exposes only direct `read`, `write`, and `edit` tools.
Permission checks use the existing reviewed task, grant, denials, and narrow exceptions; OpenCode's write/edit tools require both read and write access.
Native permission prompts cannot override supervisor denials.
Completion hooks release tool tickets without sending tool output to the supervisor.
Hook mode does not record model inputs or reconstruct their provenance.

Shell commands, search/glob, directory reads, subagents, MCP tools, attachment resolution, and other tools are disabled.
A tool hook cannot reliably enumerate all file accesses inside those operations.
Consequently the agent cannot run your test commands in this mode; run them separately yourself, or use the full Linux runtime for shell-enabled work.

The launcher rejects incompatible binaries and withholds task submission and the UI until the expected hooks register.
Missing supervisor responses block tool calls, and the launcher checks current authority while the session runs.
This does not isolate the harness or supervisor at OS level, prevent races with external filesystem changes, cover arbitrary built-in harness accesses, or guarantee descendant cleanup after an abrupt owner death.
Hook-only availability on a host is not acceptance of the full macOS or Windows confinement contract.

Run `python3 -W error::ResourceWarning -m unittest planner.tests.test_pretool -v` to exercise hook permissions, tickets, and a real pinned OpenCode against a local dummy provider.
These tests make no paid provider calls; the real binary tests require permission to bind local sockets.
