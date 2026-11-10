# Provenance and memory

`Provenance` stores observed input metadata and derivation links in SQLite. It
does not parse worker text for authorization. Its caller must be trusted
supervisor code, and its database must be outside worker access. A record is
evidence of an admitted input or derivation, not proof of causal influence.

## Public boundary

```python
store = Provenance(database_path)
store.register_task(task_id, project_id)
observed = store.record_input(task_id, worker_id, kind, resource_reference,
                              sensitive=False)
derived = store.derive(task_id, worker_id, kind, [observed.id])
note = store.add_note(project_id, task_id, worker_id, text, [derived.id])
retrieved = store.retrieve_note(project_id, note.id, later_task_id, worker_id)
store.get_note(project_id, note.id)
store.list_notes(project_id, query="")
store.delete_note(project_id, note.id)
store.export(task_id=None)
store.close()
```

`Record` and `Note` are immutable dataclasses. Unknown tasks, records, and notes
raise `KeyError`; cross-project derivations and sensitive-note persistence raise
`PermissionError`. A task cannot be reassigned to another project. An empty
derivation is rejected rather than silently losing origin information.

The supervisor registers a task with its stable project identifier before
recording inputs. Project identifiers and origin metadata come from supervisor
state, never model supplied labels. `record_input` accepts a kind and resource
reference; these are metadata, not input contents. `derive` takes actual admitted
input record IDs and computes their origin union and sensitivity. Unknown inputs
and input references from other projects are rejected.

Notes are explicitly project scoped. `add_note` requires source records from the
same project and rejects any sensitive origin before storing text. `get_note`,
`list_notes`, and `delete_note` all require the project identifier. A later task
in the same project uses `retrieve_note` to record the exposure and retain the
original origins. This grants no file or tool access.

Deletion clears note text and prevents subsequent retrieval and search. Routine
exports retain the note ID, source references and deletion timestamp, without
deleted text. SQLite secure deletion is enabled; this is not a promise to erase
external backups or previously delivered model context.

The store never receives raw tool outputs, model responses, or sensitive file
contents. A sensitive approved read may be recorded as a resource reference, but
its contents and all text derived from it must stay out of automatic persistence.
The connector must separately verify its own session storage behavior; this
module cannot establish a no-persistence guarantee for a harness or provider.

Exports contain `tasks`, `nodes`, `edges`, and `notes`. Nodes carry IDs, task and
worker identifiers, kind, metadata reference, input-origin union, sensitivity,
and timestamp. Edges contain source and target IDs and the kind `information`.
They never represent authorization. Task filtering includes earlier source
ancestors so a memory retrieval does not lose its recorded history. Notes contain
their ID, project, active text or `null`, and deletion timestamp. This is a JSON
serializable record format intended for offline inspection and later reports.

Like the existing `artifact/` harness, tests use standard-library `unittest`,
fresh temporary state and independent expected values. The provenance store is
separate from the artifact collector's raw events: blindly importing a native
session would neither prove admission to a model call nor provide safe secret
persistence.

## Validation

Tests exercise public APIs with a temporary real database. They cover origin
union through summaries and note retrieval, sensitivity propagation, rejection
of sensitive notes, project isolation, deletion and exports, persistence across
restarts, and transaction rollback when an input is invalid. These establish the
store's behavior, not the completeness of model-call interception.

Run `python3 -m unittest planner.tests.test_provenance planner.tests.test_report
-v` from the repository root.

## Offline report

`planner.report.generate(data, output_path)` writes a standalone HTML report.
Pass `{"provenance": store.export(), **supervisor.export(task_id)}` to combine
information records and control-plane snapshots. It embeds local CSS and
JavaScript and makes no network requests. Filters, search, record details,
upstream tracing, task boundaries and a timestamp/expiry timeline support
inspection. Information and authorization use different line styles and text
labels; outcomes do not rely on color.

The report excludes all note text, including active notes. It selects explicit
metadata fields rather than embedding raw sessions or arbitrary export keys.
Embedded JSON escapes HTML delimiters and labels use DOM `textContent`.
Authorization links require explicit installed grants or an approval whose task,
worker, operation, resource and tool match the referenced decision. Missing
records and unavailable history are shown as evidence limits. An allowed
decision is labeled as an unverified effect, never an executed operation.
When supplied, recorded policy revisions show their actual creation and approval
timestamps. Worker grants link to their installed revision, including revoked
workers, rather than to the task's newest policy. Missing revision evidence
produces a warning and no invented authorization edge.

Automated checks verify real record IDs and edges, text exclusion, script
escaping, local assets, exact approval matching and deletion metadata. JavaScript
syntax is checked separately. Browser screenshot validation was unavailable in
the implementation environment because its browser security policy rejected
local file URLs; no alternate serving route was used to bypass that policy.
