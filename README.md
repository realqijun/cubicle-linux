# Planner supervisor

A small Python 3.11+ implementation of task policy decisions, reviewed worker
grants, narrow exceptions, project memory, and provenance records. It follows the
existing `artifact/` layout: standard-library Python, `unittest`, fresh experiment
directories, JSON records, and independent workload checks.

**This is an incomplete security implementation.** The policy/control-plane and
record APIs work, but autonomous task execution is blocked. Neither the OS
backend nor the pinned OpenCode connector has passed complete mediation and
confinement acceptance. Refusing all launches is a temporary safety gate, not
completion of the project. See [ENFORCEMENT.md](docs/ENFORCEMENT.md) and
[OPENCODE.md](docs/OPENCODE.md) for evidence and remaining work.

## Structure and independent work

The shared records and policy foundation were implemented first. Independent
agent work then covered OS confinement/evaluation, OpenCode compatibility, and
SQLite provenance/reporting. Integration, approval lifecycle, CLI, adversarial
checks, and documentation use those boundaries.

| Boundary | Files | Current behavior |
| --- | --- | --- |
| Policy | `policy.py`, `records.py`, `templates/` | Deterministic, deny-first path and grant checks |
| Control plane | `supervisor.py`, `__main__.py` | Reviewed proposals/revisions, scoped assignments, exceptions, expiry, revocation, cancellation, history |
| OS confinement | `enforcement.py`, `native/` | Blocking launch gate; local candidate experiments are separate |
| OpenCode | `connector.py` | Exact 2.2.5 compatibility probe and inspectable isolated launch proposal; execution blocked |
| Provenance/memory | `provenance.py` | Transactional metadata, conservative origins, project isolation, inspect/search/delete |
| Report | `report.py`, `report/` | Offline HTML from actual records; missing evidence marked |
| Evaluation | `evaluation.py`, `scenarios/` | Fresh matched fixtures and an independent oracle behind the OS gate |

There is no model permission enforcer, vector store, plugin framework, background
web service, or runtime Python dependency.

## Policy semantics

Policies use the TOML `[files]` example from `docs/PLAN_PLANNER.md`. `[tools] allow`
is an explicit tool-name list. Optional `[external] read` lists individual,
existing absolute input files; each worker needs a separate exact read exception
for them. External inputs do not grant writes.

Paths are relative to the approved project root; absolute in-project spellings
are accepted. `/` separates pattern components, `*`, `?`, and character classes
match within a component, and a whole-component `**` matches zero or more
components. Denials cover matching resources and their descendants. Unknown
settings and malformed tables are rejected. Read and write are independent.

Decisions resolve symlinks before collapsing `..`, preserve denied alias names,
and check on-disk case/Unicode spelling. Both logical and resolved paths must
fit worker scopes. Hard-linked files require explicit review rather than an
assumption about their aliases. Every decision rechecks current paths, including
new matching sensitive files; this is not a claim of race-free filesystem
enforcement. There is no unconfined read/write executor behind `decide`.

Delegation accepts equal scopes, literal children matching an existing scope,
and descendants of an approved `directory/**` scope. Ambiguous arbitrary-glob
containment is conservatively rejected. An exact user-approved assignment is
required for every worker; trusted automatic-assignment templates are not yet
implemented. Repository text and memories do not authorize assignments.

Policy revisions suspend the proposal and revoke previous grants and exceptions.
The approved root's filesystem identity is retained; replacing it blocks access.
Exceptions bind task, worker, operation, tool, normalized path, resource identity,
policy revision, and lifetime. Default lifetime is one authorization dispatch;
consumption and recording are atomic across SQLite connections. A failed
dispatch does not restore that approval. `task` lifetime expires with the task.
Explicit denials remain in the policy. Sensitive reads require explicit provider
disclosure acknowledgement. An approval cannot grant supervisor state or write
access to supervisor code.

## Terminal use

Run from the repository root. `--state` must name a trusted SQLite file outside
the target project. Keep both it and its sibling `.memory.sqlite` outside worker
access. The local CLI is a trusted operator interface, not a worker tool server.

```sh
python3 -m planner --state /tmp/planner-control/control.sqlite --json \
  propose /absolute/project --template source

# Use the returned task ID. Review before approving the exact revision.
python3 -m planner --state /tmp/planner-control/control.sqlite review TASK
python3 -m planner --state /tmp/planner-control/control.sqlite \
  approve-task TASK --revision 1
python3 -m planner --state /tmp/planner-control/control.sqlite \
  assign TASK worker --read 'src/**' --write 'src/**'
python3 -m planner --state /tmp/planner-control/control.sqlite \
  decide TASK worker write src/refunds.py
python3 -m planner --state /tmp/planner-control/control.sqlite history TASK
```

`--policy PATH` imports a TOML proposal instead of a bundled template. The four
plain-data templates are `source`, `inspection`, `tests`, and `documentation`;
inspect/edit a policy before approval. `revise TASK --policy PATH` requires a new
review and approval. It does not silently widen a running task.

`request-exception TASK WORKER read RESOURCE` returns a request ID.
`exception ID` displays the normalized resource, worker, operation, scope,
expiry, revision, sensitivity, and source references. Source references are
explanatory input, not proof of user approval. Use `approve-exception ID` or
`refuse-exception ID` through this trusted terminal. Sensitive read approval
additionally requires `--confirm-sensitive-provider-disclosure`: content admitted
to a provider cannot be made unseen by later revocation.

`preflight TASK WORKER --model provider/model` checks protections and returns exit
2 while unavailable. `resume` verifies protections and remains blocked until
worker replacement and connector execution are implemented. `revoke TASK WORKER`
revokes descendants; `cancel TASK` revokes the task's grants and approvals. These
currently affect durable control-plane state only: no autonomous process can be
started. Process cleanup, supervisor-failure containment, and interrupted-run
reconciliation remain acceptance work.

`memory PROJECT list [--query TEXT]`, `memory PROJECT inspect --note ID`, and
`memory PROJECT delete --note ID` expose project memory. The provenance API stores
only supervisor-assigned origin metadata and refuses sensitive-derived note
text. The OpenCode-to-provenance bridge is not implemented; manual API calls must
not be reported as coverage of model inputs.

`export TASK` emits readable metadata. `report TASK /absolute/report.html`
generates a local report from those records and task-filtered provenance. Note
text is excluded. Decisions are labeled separately from executed effects;
information edges mean observed exposure/derivation, not proven causation.

## Configuration

Permissions live in editable TOML, not command blacklists. The CLI exposes
`--task-seconds` (default 3600 for new proposals) and `--denial-limit` (default 3
consecutive identical denials). `ConnectorConfig` accepts explicit provider/model,
executable, and finite version-probe timeout (default 10 seconds). Evaluation
accepts inference parameters and a timeout. Defaults are convenience values, not
security guarantees. The OpenCode compatibility pin is deliberately fixed until
another release's interception paths are verified. Never put credentials in
policy files, scenario manifests, exports, or committed source.

## Verification

```sh
python3 -m unittest discover -s planner/tests -v
python3 -m unittest discover -s artifact/tests -v
python3 -m mypy --config-file planner/pyproject.toml planner
```

Mypy is a development-only tool. The strict check covers new production modules;
the existing untyped `artifact.scripts.prepare` import is an explicit excluded
boundary. The intentionally buggy `artifact/workload` is an evaluation fixture,
not a green application test suite. Tests exercise public boundaries with real
temporary files and SQLite databases, independent literal expectations, dummy
secrets, and positive controls. A skipped real-confinement test is missing
evidence, not a pass. See [EVALUATION.md](docs/EVALUATION.md) for frozen comparison
rules and the distinction between final hashes, confidential reads, and transient
effects. No matched model results have been collected.

## Remaining acceptance work

1. Validate an OS backend against the full path, alias, descendant, handle,
   process-control, and failure contract. The user selected this Mac for current
   work; Linux/Windows support remains unverified.
2. Implement and validate OpenCode's actual tool-execution and every model-input
   path, configuration isolation, private process lifecycle, and sensitive
   session-storage behavior for the pinned release.
3. Connect confined workers, safe dispatch of exact exceptions, trusted automatic
   templates, live cancellation/replacement, and restart reconciliation.
4. Run frozen matched native/OS/supervisor comparisons and record unsuccessful
   runs, legitimate completion, false denials, approvals, overhead, and limitations.
5. Visually verify the offline report in an allowed browser environment. Its
   Python output and embedded JavaScript syntax are checked; browser rendering
   was not established in this session.

The security claim remains limited to declared access and installed permissions.
Authorized edits can still be wrong. Underlying OS isolation and permission
inheritance are existing mechanisms; provenance records add auditability rather
than automatically proving or preventing malicious influence.
