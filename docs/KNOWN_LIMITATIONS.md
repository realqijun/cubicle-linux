# Known limitations and deferred platform validation

## Decision recorded on 10 November 2026

Continue the platform-independent supervisor and OpenCode implementation on the
current Mac. Defer further OS-backend acceptance to an Ubuntu machine. These
limitations do not prevent implementing or testing the control plane, connector
protocol, provenance admission, templates, and recovery records. They do prevent
claiming verified autonomous execution or collecting accepted supervised-agent
comparison results. There is no unconfined execution fallback.

## macOS findings

The latest disposable `sandbox-exec` candidate attempt on macOS 27.0.1,
build 26A434, Darwin 27.0.0 ARM64 passes 12 of 14 probes. Four attempts and the
unsuccessful intermediate fixes remain in
[`macos-candidate.json`](../evidence/macos-candidate.json).

| Limitation | Observed behavior | Required follow-up |
| --- | --- | --- |
| Live hardlink alias | An independent process creates an allowed-name hardlink to a denied file after startup; the worker reads dummy secret bytes. | Test inode aliases, concurrent filesystem changes, and backend representation of sensitive resources on Ubuntu. A startup scan is insufficient. |
| Detached descendant lifetime | A detached child retains its existing filesystem restrictions and performs an allowed write after its owner exits. | Implement and test task-wide process ownership, expiry, cancellation, revocation, and supervisor-loss cleanup. This failure is missing grant lifetime enforcement, not proof of a filesystem sandbox escape. |
| Private/deprecated mechanism | The diagnostic uses `sandbox-exec` profiles; no stable production macOS contract has been established. | Keep the candidate diagnostic-only. Any later native macOS backend needs its own supported-mechanism assessment and complete acceptance run. |

An earlier rename of a denied sensitive file to an allowed name exposed dummy
bytes. The final candidate blocks that rename by making read-denied names
immutable for unlink/rename. This is stricter than the policy's independent
read/write scopes, so it does not establish faithful production policy semantics.

These findings describe this prototype. They do not establish that macOS cannot
confine processes or that Ubuntu will automatically satisfy this contract.

## Ubuntu retest plan

On 13 November 2026 the supplied Ubuntu VM became available via password SSH.
The [private-workspace runner](LINUX_SANDBOX.md) now implements a narrower,
offline execution mode with Bubblewrap, AppArmor and seccomp. It copies inputs
instead of exposing the live project, and keeps edits separate for review.
Its [real-process evidence](../evidence/linux-private-workspace.json) covers
confidential reads, continuous name denials, aliases, protected writes and
descendant cleanup. This does not accept the original live-project backend or
the full model-connected OpenCode launcher.

The remaining full-runtime acceptance plan is:

1. Record OS release, kernel, architecture, Python, OpenCode release, backend
   version, user-namespace/LSM prerequisites, and the exact policy. Use fresh
   disposable fixture state, dummy secrets, and independent positive controls.
2. Evaluate Bubblewrap and Landlock against the policy contract before selecting
   a mechanism. Reject policies whose dynamic denials, alias semantics, or
   per-worker scopes cannot be represented faithfully; do not substitute
   tool-level filtering or startup file masking.
3. Retest permitted reads/writes; denied reads/searches; scripts, children and
   grandchildren; inherited descriptors; symlinks and hardlinks (including
   aliases created during execution); traversal; new sensitive names; rename,
   deletion, replacement, and truncation. Observe actual reads and transient
   effects independently of final hashes and worker claims.
4. Test process ownership using a mechanism that covers detached descendants,
   not only a process-group kill. Test cancellation, grant expiry/revocation,
   supervisor crash, storage failure, and connector disconnection. Verify that
   no descendant continues with an expired grant.
5. Run the pinned OpenCode connector's interception fixtures inside confinement,
   including all tool, model-input, summary/compaction, and persistence routes.
   Confirm that workers cannot access policy, approval, memory metadata, or
   supervisor control channels.
6. Retain failed attempts and publish sanitized records. Enable accepted
   supervised runs only after the required cases pass. Then collect the frozen
   native/OS/supervisor comparisons with matched model and task configuration.

The tested Ubuntu private-workspace mode has its own evidence and restrictions.
Other Linux environments, live-project execution and Windows remain unverified.
Skipped tests do not establish enforcement.

## Connector and lifecycle acceptance alongside Ubuntu retesting

The continuation implements real OpenCode hooks, a worker-bound broker, actual
context admission, trusted delegation templates, atomic multi-resource decisions,
and durable execution/recovery records. These components can be tested now with
fixed dummy-provider fixtures. The remaining runtime acceptance includes:

- OpenCode 2.2.5 logs plugin setup failures and can continue without that plugin.
  A normal launcher must start a private server without task data, verify the
  authenticated successful hook inventory, then dispatch the reviewed prompt.
  Passing plugin configuration alone does not establish protection.
- The current connector supports native HTTP and blocks WebSockets, legacy SDK
  transports, native compaction replacement windows, unknown tools, attachments,
  and unreviewed plugins/integrations. Summary lineage uses stored message types
  and actual output receipts; missing or ambiguous mappings block the call.
- System/managed configuration discovery, non-memory session artifacts, protected
  process environments, inherited descriptors, and private API isolation must be
  checked inside the chosen backend. Removing credentials from shell children
  does not alone isolate the tool-owning parent's memory or environment.
- Execution-attempt transitions are trusted runner records. Reconciliation
  suspends unresolved authority and requires fresh protection and cleanup
  evidence; it does not kill a process by changing a database field. Wire actual
  process ownership, safe exception installation/replacement, and live
  cancellation into the validated Ubuntu backend before accepted autonomous
  runs. Never count a supplied worker claim as cleanup evidence.

The fixed OpenCode fixture is an integration diagnostic with controlled responses,
not a matched autonomous-agent evaluation or proof of confidential-read prevention.
Its evidence preserves unsuccessful attempts as well as positive controls.
