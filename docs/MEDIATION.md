# Worker mediation and actual context admission

`WorkerBroker` binds one approved task and worker in trusted supervisor code.
It checks current task/grant/ancestry before accepting tools or model context.
Its worker protocol has no approval, policy, grant installation, template
approval, verification, or recovery routes. Those belong to the separate trusted
operator interface. Repository text and tool output cannot invoke them.

The broker is a blocking decision point and provenance boundary. It is **not**
an OS sandbox or a proof that actual tool subprocess effects are confined. A
normal launcher must verify OS protection before starting the tool-owning
OpenCode process. Local fixed fixtures exercise the software without making
that production confinement claim.

## Tools

The pinned adapter supports `read`, `write`, `edit`, `glob`, `grep`, and `shell`.
Unknown tools are denied. Read/edit/write actions use normalized resources and
atomic multi-resource authorization. OpenCode's built-in write reads existing
content for previews and byte-order handling, so both read and write permission
are required for that tool; a write-only grant does not silently gain a read.
Search tools require their requested root to be readable. OS confinement must
still mediate every actual file opened by searches, shell commands, and helpers.
Shell text is not evaluated through a command blacklist.

The before hook snapshots arguments and records the authorization before the
trusted executor runs. Completion must match a pending call ID and tool. Call IDs
cannot be reused; closed brokers cannot dispatch. Native permission checks bind
back to that ticket and resource and do not consume exceptions a second time.
This OpenCode release skips its completion hook when a before hook refuses a
tool. The broker records constant denial feedback for that call, so the next
model context can observe the refusal without treating it as a successful read.
Shell creation must match a pending shell command and remain within the project.
Tool results are information, never approval. Sensitive results and model-derived
tool results retain sensitive exposure rather than becoming clean through an
echo. Model-output note saving uses the recorded handle, not caller-supplied
origin labels.

## Model inputs

`ModelContext` keeps actual input/output strings only in memory. An opaque
context-local handle points to a supervisor-created provenance record; knowing
or copying a record ID cannot mint a valid handle. The broker maps these handles
to unpredictable worker-local wire receipts.

The actual OpenCode hooks pass system/messages and the final serialized native
HTTP request body before transport. A context transformation records its supplied
inputs; final provider lowering inherits those origins and adds an observed
body source. Admission is durable before dispatch. Output derives from that
admission. Primary, title, compaction, and transient calls retain distinct request
receipts; one kind cannot complete another's call. Provider origins must be
explicitly allowed. Credentials in transport headers are not sent to the broker.

Actual tool-result IDs link context parts to their observed tool output. Stored
message IDs and trusted adapter sidecars preserve prompt, assistant, and summary
origins. Text markers and model-provided citations do not establish those links.
Ambiguous or missing mappings are blocked. Native compaction replacement windows,
WebSockets, and legacy AI SDK paths are explicitly unsupported rather than
silently untracked. The supported HTTP fixtures do not establish all-route
acceptance; see [OPENCODE.md](OPENCODE.md).

Call admission, transformations, and failures persist metadata, not raw request,
response, tool, or credential bytes. Sensitive-derived output cannot be saved
as a note. Deleted notes cannot be admitted again through old handles. Closing
the broker drops its in-memory references and invalidates receipts; Python
cannot promise secure erasure of copies already held by a provider or caller.

## Private transport

`BrokerServer(factory)` binds an ephemeral IPv4 loopback port for one broker.
The factory creates SQLite connections on the serving thread; requests serialize
there. A fresh bearer token authenticates the trusted plugin. The protocol also
requires an exact version and successfully registered hook inventory, one bound
session, and monotonically increasing sequence numbers. Foreign sessions, replay,
unknown routes, malformed bodies, and missing receipts are rejected.

Only `POST /admit` with JSON is accepted. Body size, socket inactivity timeout
(including request lines/headers), and shutdown wait are configurable. Shutdown
uses a bounded join and reports failure if the serving thread does not stop;
it never claims that a stuck thread was cleaned up. Request logging is disabled
and responses use `no-store`. Token
authentication does not itself establish OS separation: the confined worker
must not inspect the supervisor/plugin process or steal another worker's token.
The plugin removes broker credentials from its environment and strips configured
provider credential variables from shell children. Parent-process/handle/control
channel isolation remains part of the combined model-connected runtime
acceptance. The separately tested Ubuntu offline runner has no broker/provider
network integration.

Non-filesystem refusals also enter durable decision history with sanitized route
and reason codes. Unsupported connector paths report metadata-only denials;
raw request bodies, frames, arbitrary route strings, and credential bytes are
excluded from those records.

## Validation

Run `python3 -m unittest planner.tests.test_mediation planner.tests.test_context
planner.tests.test_broker_server -v`. The broker server test requires permission
to bind a loopback socket; a desktop network sandbox may block that test even
when the host supports loopback. Do not count an unavailable test as a pass.

The actual pinned-binary diagnostic uses a fixed local provider and dummy files,
including independent allowed-write and denied-write controls. Evidence contains
route names, outcomes, counts, and source hashes. It excludes raw provider bodies,
credentials, and native session exports.
