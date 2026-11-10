# Actual model-context admission

`planner.context.ModelContext` is a provider-independent boundary for the
trusted supervisor connector. It carries actual input and output text in memory
and stores only metadata in `Provenance`. It does not implement OS confinement,
grant access to files, or prove complete OpenCode interception by itself.

## Trusted caller and API

Create `ModelContext(store, task_id, worker_id)` after registering the task's
project in the provenance store. The caller must be trusted supervisor code.
Never expose `observe` or its `kind`, `reference`, or `sensitive` arguments as
worker-supplied authority. The connector assigns those fields from actual
admitted sources. References must be resource IDs or paths, not source contents.

- `observe(text, kind, reference, sensitive=False)` creates metadata and returns
  a `ContextHandle`. Actual text stays in memory. The handle's `record_id` is
  readable, but constructing a new handle with that ID does not make it valid.
- `record(handle)` returns the immutable provenance record. `text(handle)`
  returns content to the trusted connector. Both reject foreign, expired, and
  deleted-note-derived handles.
- `admit(handles, kind="model_call")` records a derivation before handing its
  exact ordered input tuple to the caller as `AdmittedCall.inputs`. The record
  is durable before transport. A request's `repr` excludes its input text.
- `complete(call, output, kind="model_output")` creates an output handle with
  the call's union of origins and sensitivity. Each admitted call can complete
  only once; a reconstructed object with the same ID is rejected.
- `fail(call)` consumes a pending call and records a metadata-only failure.
- `call(handles, transport, kind="model_call")` is the synchronous convenience
  form. `transport` receives precisely `tuple[str, ...]` and returns a string.
  Input admission is recorded before invoking transport. Provider exceptions
  become `ContextTransportError` without their potentially sensitive messages;
  the failed-call metadata remains.
- `retrieve_note(project_id, note_id)` loads a current project note into a new
  handle with its original origins. Cross-project retrieval remains forbidden.
  Uses that start after deletion are rejected. An overlapping admission that
  already checked a live note may finish; deletion cannot recall input copies
  already admitted or held by a caller/provider.
- `save_note(project_id, handle)` saves only the handle's actual text, with its
  observed lineage. Sensitive inputs and all their derivatives are rejected by
  the provenance store before any note text is persisted.
- `receive(source_context, handle)` transfers a temporary worker report within
  the same task and store, preserving origins and sensitivity. The supervisor
  must separately approve exposure to the receiving worker's scope. Cross-task
  information reuse uses explicit project-note retrieval instead.
- `close()` revokes all handles and pending calls and drops component-owned text
  references. It is idempotent. Python strings or tuples already supplied to a
  transport may have other references; this is not secure memory erasure or a
  promise that a provider forgets content already sent.

The same admission/derivation calls cover primary model calls, summaries,
compaction, titles, and other auxiliary generations. `kind` is assigned by the
trusted adapter describing the actual route. Model-authored origin names,
citations, or approval claims remain ordinary text; they cannot change the
assigned metadata or install permissions.

## Deletion and failures

Note dependencies propagate through worker reports and outputs. Each later
retrieval or admission rechecks that every source note is still live. Deleting a
note therefore blocks reuse of both its cached handle and derived context
handles. Already admitted transport input cannot be recalled. Deletion does not
remove the non-sensitive historical fact that an earlier call received a note.

Provider failure leaves an admitted-call record because the transport received
the input tuple; it does not prove that a remote provider accepted or processed
it. The failed-call record contains no response text or exception message.
An unknown handle blocks before the callback runs. Closing a context blocks
subsequent input, completion, note access, or report transfer from that context.

## Integration and verification limits

The broker must map opaque wire handles to its own `ContextHandle` objects. It
must not accept arbitrary provenance record IDs from worker messages. The
provider adapter must supply every actual input through this boundary and must
not append hidden text in a later transformation. System prompts, tool results,
worker reports, memories, compaction, retry overlays, and auxiliary inputs all
need their own actual observations or tracked derivations. This component cannot
verify an arbitrary callback's implementation; pinned OpenCode hook/transport
coverage is tested separately and still needs the Ubuntu confinement retest.

Run `python3 -m unittest planner.tests.test_context -v`. Tests use actual
temporary SQLite stores and transport callbacks with independent literal input
expectations. They cover origin union, auxiliary transformations, worker report
transfer, handle substitution, sensitive persistence, sanitized failures, note
deletion, project boundaries, and context expiry. No external model or provider
credential is required. Edges mean observed admission/exposure or derivation,
not proven causal influence on a model decision.
