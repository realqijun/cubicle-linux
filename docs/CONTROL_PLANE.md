# Delegation and durable execution records

The supervisor's SQLite database is the trusted control plane. Workers must not
access that database or call operator approval, verification, cleanup, or
recovery methods. The connector/broker exposes only the worker operations it
explicitly supports. An arbitrary `approved` string from a worker is never an
operator action.

## Active authority and multi-resource operations

`check_worker(task, worker, tool=None)` returns a currently valid grant after
checking task approval/expiry, policy revision, project-root identity, worker
state, tool assignment, and all parent grants. It does not consume an exception
or record a filesystem access. `worker_status` and `workers` expose durable
state; an `active` grant is not evidence that a process is running.

`authorize_many(actions)` checks one operation's complete resource set inside a
single transaction. The actions must belong to one task and worker. Exact
single-use approvals are consumed only if every resource passes. On a denied
resource, speculative consumption rolls back and every returned decision is a
denial. All decisions are retained. This supports read-modify-write and rename
mediation without consuming half of an operation's approvals. The single-action
`authorize` method uses the same path.
Repeated-denial detection compares the complete normalized operation/resource/
tool set. Repeating a denied multi-resource request cannot evade suspension by
alternating its resource rows. `access_requests(task)` and exports retain these
metadata-only request groups; they contain no command bodies or file contents.
A reactivated worker starts a new sequence: only requests recorded after its
latest `resume` or successful `recover_attempt` count toward the limit.

## Reviewed automatic assignments

The trusted operator calls:

```python
supervisor.approve_delegation_template(
    task_id, "source-reader", parent_worker,
    read=("src/**",), write=(), tools=("file",),
)
```

The preset is plain data, bound to the exact task, policy revision, and parent
worker. It must fit both the approved task policy and that parent's grant.
`delegate(grant, "source-reader")` may install an equal or narrower subset for a
child of that same parent. It cannot add tools, change parents, bypass denials,
reuse a template in a different task, or use a previous policy revision.
Assignments outside such a preset still require `approve_worker` through the
operator channel. Stored workers include template attribution; `templates(task)`
and `export(task)` make the reviewed ceilings inspectable.

Policy revision, task cancellation, or parent revocation invalidates the
corresponding templates. Restoring a parent does not revive a revoked template.
The policy module's conservative glob-containment rules apply; this is not a
new policy language or automatic authority inferred from repository text.

## Execution attempt lifecycle

The lifecycle APIs record what the trusted runner knows. They do not launch or
terminate processes. The connected runtime's [launcher](../planner/launcher.py)
records every run through this lifecycle, with its cleanup evidence. The
[Ubuntu offline runner](LINUX_SANDBOX.md) has separate process-containment tests
but does not populate these attempt records.

1. `prepare_attempt(task, worker)` snapshots the exact grant, revision, and expiry
   into a fresh ID with state `prepared`. Unresolved attempts prevent another
   attempt for that worker.
2. `verify_attempt(id, backend_status, connector_status)` records trusted actual
   preflight results. Both statuses must be `available` and `verified`; authority
   is rechecked. Failure records `blocked` and a fixed reason code. Diagnostic
   output and arbitrary reason text are not persisted.
3. `start_attempt(id)` rechecks authority and records `running` for the trusted
   runner's verified dispatch. It starts no subprocess and cannot be replayed.
4. `finish_attempt(id, successful=..., cleanup_verified=...)` records a terminal
   outcome only when cleanup evidence is supplied. Missing cleanup produces
   `interrupted`, suspends the grant, and revokes its live exceptions.

Revocation, cancellation, policy changes, and repeated-denial suspension mark
dispatched attempts `stopping`. That state explicitly means cleanup is required;
it does not claim that a detached descendant was killed. Prepared/verified
attempts become `blocked`. Each transition appends a metadata-only event, so
failed recovery checks remain visible after later success.

## Restart and recovery

`reconcile(task)` is an explicit owner-loss operation, not a side effect of
opening a database or inspecting status. It marks unfinished attempts
`interrupted`, suspends their nonrevoked workers, and revokes their live
exceptions. An unavailable event subscription or an absent effect marker does
not establish that no worker ran.

The trusted operator/runner may call `recover_attempt` with verified backend and
connector statuses plus independent cleanup evidence. Recovery requires the
original task, revision, grant, root, ancestry, and expiry to remain valid. It
never revives revoked or expired authority or old exceptions. Successful
recovery closes the old attempt as `reconciled`; a replacement needs a fresh
attempt and new verification. Unsuccessful checks remain `interrupted` with
fixed reasons such as `cleanup_unverified`, `protection_unavailable`, or
`authority_invalid`.

`resume(task, worker, backend, connector, cleanup_verified=False)` reactivates a suspended worker.
The trusted caller supplies the runtime statuses.
The operator CLI `resume TASK WORKER --opencode PATH --fuse-helper PATH` obtains them from `runtime_status`, the same connected-runtime check that `run` uses.
Both statuses must be `available` and `verified`.
The task must be approved and unexpired, and the worker must be suspended, unrevoked and installed under the current policy revision with valid ancestry.
A prepared, verified, running or stopping attempt blocks resumption until it is reconciled.
An interrupted attempt is closed through `recover_attempt`, which still requires `cleanup_verified`.
Resumption restores the existing grant unchanged: it starts no worker, widens no scope and restores no exception.
Each success appends a `resume` decision with tool `operator` to the task history and export.
`cleanup_verified` belongs to the trusted operator channel and must not be an option accepted from worker text or a tool request.

`preflight(task, worker, backend, connector)` returns the supplied statuses for a worker that may run now.
The CLI `preflight TASK WORKER --opencode PATH --fuse-helper PATH` exits 2 unless both are verified.
The generic live-project `Backend` still refuses every launch; neither command uses it.

Legacy workers receive a nullable template-attribution column. Earlier decision
records retain unknown grant-revision evidence. New tables do not invent past
attempts, transitions, or automatic-template approvals.

## Validation

Run from the repository root:

```sh
python3 -m unittest planner.tests.test_delegation_recovery planner.tests.test_supervisor planner.tests.test_control_adversarial -v
python3 -m mypy --config-file pyproject.toml planner/supervisor.py
```

Tests use real temporary SQLite databases and public APIs. Protection-status
fixtures exercise bookkeeping only; they are not OS-confinement evidence.
