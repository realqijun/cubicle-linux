# Planner completion plan

Status: **Implementation authorized by the user on 15 November 2026, including the amendments below.**

Prepared on 15 November 2026 against commit `bfd735c`. The agreed scope remains
[PLAN_PLANNER.md](PLAN_PLANNER.md). This document explains how to close the
remaining gaps; it does not silently replace or narrow that scope. No code,
existing policy or original scope is superseded by this document.

## 1. What the audit established

The current runner copies permitted inputs into a private workspace, executes a
command, and returns its exit status. It does not apply changes to the original
project. See `LinuxSandbox.prepare()`, `run()`, and `main()` in
[linux_sandbox.py](../planner/native/linux_sandbox.py).

That verifies the factual claim that write-back is missing. It does **not**
justify stopping development there:

- The original plan's user-facing flow requires completing or cancelling tasks,
  not just preparing isolated files. Its completion criteria require legitimate
  authorized work to remain executable.
- The original plan does not explicitly prescribe private workspaces, automatic
  merge, or a mandatory review of every resulting patch. These are design
  decisions, not previously agreed stopping conditions.
- The full prompt-to-planner-to-worker flow, model-provider transport inside
  confinement, permission-change/restart loop, and durable runtime integration
  remain incomplete too. Adding write-back alone will not complete that flow.

The historical Ubuntu result is 23 passing contract checks for the offline
runner. It is not evidence that any of these missing behaviors already works.

## 2. Intended user-visible result

The Linux workflow must eventually support all of the following in one task:

1. The user supplies a project, prompt, provider/model and a template or policy.
2. A planning agent proposes a task policy and worker assignments. Before any
   project data enters model context, reads are confined and admitted through
   the existing context boundary. Before initial approval, proposal generation
   uses the user's request and trusted templates, without unrestricted project
   inspection.
3. The user reviews effective permissions and approves the exact policy.
4. The deterministic supervisor validates grants and starts confined OpenCode
   workers. Workers see their permitted project copy, not the original host tree.
5. A worker may use permitted alternatives after a denial or request access.
   The planner can explain the request; it cannot authorize an expansion.
6. User-approved changes to authority are installed safely. Where in-place
   changes cannot be enforced, stop and replace the worker under a new profile.
7. Successful work produces a checked changeset and updates the original
   project according to the application mode approved below.
8. Completion, cancellation and failure clean up descendants and leave accurate
   decision, application and provenance records.

TOML remains reusable template data. SQLite holds reviewed task policies,
worker grants, exceptions and history. LLMs use restricted request interfaces;
they do not directly edit the database or call operator approval APIs.

### Agreed application behavior

Default: **automatically apply successful changes already covered by
the approved worker permissions**, after deterministic permission and conflict
checks. A separate explicit review-only mode can retain the changeset without
applying it. No additional approval is required solely because an already
authorized source edit was made in a private copy.

Conflicts, changes outside the grant, expired authority and unsupported file
operations must block application. The system must not silently widen scopes,
overwrite conflicting work or fall back to telling the user to copy the whole
workspace. Section 8 records the agreed application default.

## 3. Scope and completion boundaries

First integration target: **the complete Linux/OpenCode workflow**, using the
existing Ubuntu backend work as a starting point. The steps below are milestones,
not permission to stop permanently after the first useful subset.

Keep explicit accounts of these separate outcomes:

- Write-back works: necessary, but not a model-connected launcher.
- A confined command can call a fixed provider: necessary, but not a complete
  planning/delegation/approval workflow.
- The Linux workflow passes: not acceptance of macOS or Windows.
- A task finishes: not proof that every attempted operation was authorized.

The earlier decision to document/defer macOS and Windows limitations remains in
force for implementation order. Their original portability requirements remain
open. The original plan must not be called complete unless its remaining
criteria are met or the user explicitly agrees to a scope change.

## 4. Implementation sequence and exit checks

### A. Checked changesets and application to the original project

Build this base first, before delegating implementation across modules.

- Capture a trusted baseline during preparation: copied paths, source identity,
  content fingerprints and the originating task, worker and policy revision.
  Keep baseline metadata outside worker-accessible storage.
- Compare final workspace contents against that baseline. Support permitted
  regular-file additions, modifications and deletions. Never interpret an
  original file omitted from the workspace as a worker deletion.
- Recheck current authority, original-root identity and each destination before
  application. Protected supervisor files/state remain protected even if a
  broad user policy would otherwise cover their names.
- Reject traversal, symlink/hardlink substitutions, special files, destination
  collisions, unexpected directory changes and changed original files. Do not
  overwrite an existing file that was never exposed/copied by mistaking it for
  a newly created file.
- Keep the first application contract within the current supported regular-file
  operations. File metadata changes need an explicit documented rule; do not
  silently turn content-write permission into arbitrary ownership/metadata edits.
- Validate the whole changeset before beginning writes. Use atomic replacement
  for individual files and retain metadata about per-file progress so an
  interruption is detectable and recoverable. Do not claim that multiple file
  writes and SQLite commit form one atomic filesystem transaction.
- Define concurrent-writer behavior: fingerprint checks detect edits since the
  baseline; final rechecks reduce races but do not supply a filesystem-wide lock.
  Require a quiescent commit window or verified cooperation from other writers.
  Do not automatically roll back over newer user changes after an interruption.
- Apply only after successful execution and confirmed worker cleanup. On failure,
  cancellation or uncertain cleanup, retain the result as unapplied and report why.

Exit check: a real scoped edit reaches the original file, untouched/excluded
files remain byte-for-byte intact, conflicts and unauthorized changes produce
no application, and interrupted application cannot be reported as complete.

### B. Connect a model-capable OpenCode worker to confinement

- Retain the pinned OpenCode compatibility checks. Establish confinement before
  the process owning tools can inspect project information or receive a task.
- Start a private server with no task data, verify the authenticated required
  hook inventory, then dispatch the reviewed prompt. A plugin setup failure must
  not leave a usable unmediated agent.
- Resolve workspace/original path mapping explicitly. Grant checks, exceptions,
  actual tool effects, origin records and final write-back must refer to the
  same logical project resources despite different physical roots.
- Provide explicit broker/provider transport and configurable runtime resources.
  Preserve filesystem, process and control-channel isolation. Test any required
  AppArmor/Bubblewrap changes as a new contract; do not weaken the offline mode
  silently or build an unrelated general network-security platform.
- The original scope excludes general network containment. It nevertheless
  requires protecting local helpers and control APIs that could bypass file
  restrictions. Verify these routes when enabling transport.
- First use a deterministic local provider fixture, then the explicitly selected
  real provider, including LaaS when its endpoint/model and credentials are
  available through the trusted configuration channel.
- Verify harness configuration discovery and sensitive-content persistence,
  including session files, logs, caches, attachments and truncated output.

Exit check: the actual pinned worker performs a permitted edit, a real forbidden
read fails despite an attempted read, the edit is applied through A, all observed
model requests are admitted, and missing hooks block before task dispatch.

### C. Planning, delegation and permission reconsideration

- Connect prompt submission to a planning model that proposes a trusted-template
  selection and explicit worker scopes. Validate the structured proposal in
  Python, display it to the user, and require initial task approval.
- Start with a single worker path, then connect supervisor-created child workers
  with subsets of their parent's authority. Do not enable unrestricted native
  subagent spawning.
- Use existing reviewed delegation presets for automatic assignments. Other
  assignments or expansions require the user approval described in the original
  plan, even when within the task-wide maximum.
- Route structured requests and untrusted explanations through the existing
  approval APIs. Keep once/task lifetimes, sensitive-provider-disclosure warnings,
  resource identity, revision and replay checks intact.
- A once-only exception must execute one defined approved operation. Do not
  implement it by leaving a replacement worker broadly able to reopen the file.
  Use a constrained mediated operation where required; prove the operation
  boundary before claiming single-use enforcement.
- Stop and clean up a worker before replacing its confinement profile. Rebuild
  only approved context and preserve which edits were staged, applied or
  discarded. SQLite permission changes alone must never be reported as an
  installed kernel permission change.

Exit check: a worker is denied, requests access, receives an authentic user
approval, completes the exact permitted work, and updates the original project.
Refused, stale, substituted or replayed requests remain denied.

### D. Real lifecycle, recovery and provenance integration

- Connect actual launch, running, stop, cleanup and application outcomes to the
  existing attempt APIs. Record verification only from observed checks, not a
  worker claim or a synthetic success flag.
- Cancellation, expiry, revocation, lost broker/connector, policy-store failure
  and supervisor death must stop affected workers and descendants.
- On restart, reconcile unfinished execution and partially applied changes.
  Recheck protection, grant/revision validity and file state before resuming.
- Connect actual planner/worker prompts, tool outputs, reports, summaries,
  auxiliary calls and memory retrieval to the existing context/provenance APIs.
  Keep raw secrets and sensitive-derived text out of ordinary records/exports.
- Extend the report to distinguish authorized, executed-in-copy, applied-to-source,
  conflicted, interrupted and unverified outcomes from actual records.

Exit check: crash/cancel/restart tests demonstrate real cleanup, accurate original
file effects and durable records without reviving expired authority.

### E. Independent acceptance, evaluations and delivery

- Repeat the Linux contract after changes; record source hashes and preserve
  failed attempts. Old offline results remain historical until new tests pass.
- Extend the existing artifact-style fixtures rather than creating another
  experiment framework. Freeze matched scenarios and scoring before collecting
  autonomous-agent comparisons. Confine the evaluator too.
- Include ordinary coding tasks and misleading repository/tool/memory content.
  Assess legitimate task completion on the original project independently from
  attempted/blocked access and transient effects.
- Run strict typechecking, targeted public-boundary tests, the complete suite,
  and independent Standards/Spec reviews. Fix actionable findings and rerun
  affected checks. Update instructions and architecture to the delivered behavior.
- Commit verified work; report exact evidence, remaining requirements and any
  external blocker. Do not present a documented limitation as completed work.

Exit check: a reproducible prompt-to-applied-edit demonstration, permission-change
and failure demonstrations, and matched evaluation results support the precise
claims made for the Linux version. Other original requirements stay tracked.

## 5. Tests and observable acceptance cases

Use the existing public policy/approval, lifecycle/memory, CLI/connector and real
filesystem/process seams. The write-back API extends the filesystem/CLI seam;
the cases below define the acceptance checks for the authorized implementation.

| Boundary | Required observable result |
| --- | --- |
| Permitted content change | The original file receives exactly the worker's authorized content. |
| Addition/deletion | Only authorized additions and deletions appear in the original. |
| Excluded input | `.env`, hidden policy/state and other omitted originals are not exposed or deleted. |
| Unexposed destination collision | A new workspace file cannot overwrite an unseen existing original. |
| Conflict | A user edit since preparation is preserved and reported as a conflict. |
| Path aliases | Traversal, symlink/hardlink and replaced-root/parent attempts cannot redirect application. |
| Scope/authority | Protected writes, expired/revoked grants and changed revisions fail before application. |
| Apply failure | Preflight denials cause no writes; failures during commit record actual per-file progress. |
| Replay | The same completed application or consumed exception cannot silently execute again. |
| Real harness | Tools and scripts can do permitted work; attempted protected reads fail for children too. |
| Bootstrap | Missing/failed hooks or unavailable protection block before task information is dispatched. |
| Delegation | A child's scope cannot exceed its approved parent/template. |
| Reconsideration | Approval permits only its defined operation/lifetime; denial and restart preserve boundaries. |
| Lifecycle | Cancellation, expiry and owner/connector loss stop descendants and prevent later application. |
| Provenance/persistence | Actual inputs carry origins; sensitive content is absent from forbidden persistence/export. |
| Recovery | Interrupted state is reconciled without replaying edits or reviving old permissions. |

Positive controls must show the same fixture operation is possible when
authorized. A missing file or absent marker alone is not proof of enforcement.

## 6. Implementation ownership after plan approval

The primary agent first establishes A's minimal baseline/changeset/application
contract and its first passing vertical slice. Then independent tasks can be
delegated within the agreed interface and separate file ownership:

| Owner | Work | Dependency |
| --- | --- | --- |
| Primary agent | Baseline/changeset application, authority integration and final integration | Start first; owns shared interfaces. |
| Connector agent | Confined no-task OpenCode bootstrap, transport and provider fixtures | Agreed path/runtime contract; B's proof before C. |
| Workflow agent | Planner proposals, CLI, approved delegation and permission replacement | Public launcher/application interfaces agreed first. |
| Validation agent | Independent adversarial cases, evidence and acceptance audit | Tests public boundaries; cannot self-certify unrun OS checks. |

Delegate only independent work. Serialize changes to shared supervisor/schema
and launcher interfaces; integrate and review before widening claimed support.

## 7. Configuration and prerequisites

Keep settings small and explicit: provider/model and credential source, runtime
paths, task/worker lifetimes, request limits, polling/cleanup timeouts, run storage
and application mode. No embedded credentials, guessed LaaS endpoint, general
workflow framework or LLM-based permission enforcement.

The latest Ubuntu connectivity check in the preceding implementation turn was
refused on `localhost:2222`. The VM must be available for real Linux acceptance;
local development can continue after approval, but skipped OS tests cannot clear
the launch gate. Check the pinned OpenCode installation and provider availability
before work that depends on them. Do not install/change platform prerequisites
or spend real-provider quota as an unreviewed fallback.

## 8. Agreed implementation direction

1. **Application default:** automatic application of authorized, conflict-free
   successful edits in the completed workflow, with an explicit review-only mode.
2. **Delivery scope:** complete Linux integration first, then implement and validate
   macOS and Windows against the original contract; all three remain deliverables.

The user has authorized these amendments and implementation. Preserve the original
scope, run an independent review agent after implementation, and report unverified
requirements honestly. Linux is the first integration target, not the final scope.


## 9. Approved amendments

### Complete the permission contract

Track and implement the differences between the policy interface and each execution
backend. In particular:

- Support authorized file and directory creation, removal and movement. Check both
  ends of a move and all affected descendants. Never restore the recorded ancestor-
  rename disclosure by merely removing the directory-write prohibition.
- Support individually approved external regular-file inputs as read-only copies
  bound to the exact worker, resource identity and approval lifetime. Do not expose
  their parent directories; preserve source and sensitivity metadata.
- Keep read and write authority separate, including blind replacement without
  revealing old contents. Prevent a move or alias from making protected contents
  readable. Unsupported operations remain visibly blocked until implemented.
- Make path-pattern support consistent between policy decisions, tools, scripts,
  children and application to the original project. Reject unsupported semantics
  before launching; do not silently strengthen or weaken the displayed policy.
- Evaluate existing Linux enforcement first. Add a native helper or filesystem
  mediation only if the required semantics cannot be enforced otherwise. FUSE is
  an investigation candidate, not an adopted dependency or proven solution.

For each operation, require a successful authorized execution and a denied execution
with actual dummy data, including subprocesses and dynamic aliases. Policy-unit
results alone do not establish filesystem protection.

### Finish portability after Linux integration

For macOS and Windows separately: select a mechanism against the same contract,
implement the platform helper and startup checks, connect lifecycle and OpenCode,
and run the full positive/negative filesystem and process acceptance suite on a
real instance. Record prerequisites and tested versions. The macOS failed probes
remain regressions to resolve. Neither an unsupported adapter nor pending tests
counts as completion. Keep the Python policy and interface shared; do not duplicate
policy logic or introduce a general backend framework.

### Bind single-use edits to application

Define a single-use write approval as one identified logical edit, including its
private-workspace effect and application to the original resource. Bind the staged
result and baseline to that approval; application must not consume a second edit
or allow a second worker mutation. Track reservation, execution, application and
interruption without resurrecting consumed authority. Recheck expiry, revocation,
revision and destination identity before application. Do not automatically replay
an interrupted operation. Test one approved protected edit reaching the original,
a second edit being denied, and restart/conflict/expiry causing no unauthorized
application. Reads retain their distinct single-operation semantics.

Automatic application of successful authorized conflict-free edits remains the
intended complete workflow; review-only mode retains an unapplied changeset.
Implementation of application must not bypass the runtime acceptance gate.

## 10. Implementation progress — 15 November 2026

Section A has started with `planner/changeset.py`: a trusted application component
for regular-file changes, backed by the existing supervisor's authorization and
revision checks. It validates the complete changeset before writes and journals
baseline/expected fingerprints and per-file progress. It refuses excluded-file
collisions, aliases, unsupported metadata, directory changes and replay.

This component requires a stopped worker and quiescent source/workspace trees.
The launcher must establish those conditions before calling it; there is no new
worker-facing application route. It is not yet wired into automatic task execution.
Application is currently Linux-only. Existing files retain ordinary mode bits and
must have the caller's UID/GID; files or destination directories with extended
attributes/ACLs are refused. New files receive mode 0600 and the caller's UID/GID.
Other file metadata semantics and platforms remain to be implemented. This is a
visible limitation, not completion of the first amendment.

Independent review found protected-resource, root-identity, recovery-manifest,
metadata and directory-durability issues in the initial implementation. These were
corrected before delivery. Runtime cleanup integration, exclusive-tree handling,
single-operation approval binding, directory/external-input support, provider
transport, the complete planner workflow, platform acceptance and comparative
experiments remain open. Section A and the overall plan are not complete.

Validation for this component: 16/16 focused tests pass on the Ubuntu guest; the
full Ubuntu suite reports 163 tests, 117 passed and 46 skipped. The skipped checks
are not protection evidence. An earlier full-suite attempt failed because the
transferred test bundle omitted artifact fixtures; the corrected complete bundle
passed. Strict typechecking remains unrun because mypy is unavailable in the
checked Python environments. See [the retained results](../evidence/completion-changeset.json).
The final independent review reports no remaining actionable component findings
under its documented assumptions. No model-connected execution was claimed or run.

### Section B progress — 15 November 2026 (continued)

On Ubuntu 24.04.3 (kernel 6.8.0-149), the pinned OpenCode 2.2.5 binary ran inside
bubblewrap, the `planner-connected-*` AppArmor candidate profile and a candidate
seccomp filter, over the FUSE-mediated workspace, with a deterministic loopback
provider fixture. The model's write tool edited `README.md`; a confined Python child
received `PermissionError` on `.env` and was denied `/proc/*/environ`, `/proc/*/mem`
and `pidfd_getfd`; worker cleanup completed; `Changeset.apply()` then updated the
original `README.md` and left `.env` intact. Every provider request was admitted.
With hook readiness withheld, no provider request was made. This meets B's exit
check for the fixture provider only.

The full suite with all Linux gates then passed (186 tests, 160 passed, 26 skipped).
A preceding run failed 19 offline-runner tests because a VM reboot had dropped the
operator-loaded offline profile; it was re-rendered from the test grant and reloaded.
See [the retained results](../evidence/completion-progress.json).

The same confined runtime then ran once against the real LaaS provider
(`soclaas/qwen3.8:27b`, configured by `../artifact/.opencode/opencode.json`). The
model fixed `src/greeting.py`, which was applied to the original; it attempted `.env`
three times (twice by shell, refused inside confinement; once by the read tool,
denied by the broker). A recording forwarder outside the jail found no secret marker
in any provider traffic, and no run-directory file contained it.

Still open for B: harness configuration discovery and session/log/cache persistence
acceptance, and ProviderRelay's own HTTPS upstream path (the real run used a loopback
recording forwarder as its oracle). Application is
still invoked by the test, not by an automatic launcher. Sections C–E, the amendments
in section 9, and macOS/Windows remain open.

## 11. Implementation status — 16 November 2026

Linux sections A–E are implemented and tested on the Ubuntu guest; macOS and
Windows (section 9, portability) remain open. Evidence:
[progress](../evidence/completion-progress.json),
[comparison](../evidence/completion-comparison.json),
[repeat comparison](../evidence/completion-comparison-repeat.json). Operator guide:
[connected runtime](CONNECTED_RUNTIME.md).

| Plan item | State | Evidence |
| --- | --- | --- |
| A. Checked changesets | Done: files and directories, conflicts, collisions, aliases, journalled progress, no replay; withheld deny-read placeholders and complete blind replacement | `test_changeset`, `test_launcher` |
| B. Confined model-capable worker | Done: FUSE mediator + bubblewrap/AppArmor/seccomp, hook gate before dispatch, fixed relays; fixture and real LaaS; repository config/plugins/instructions never load; no host-side session persistence | `test_confined_opencode`, `IsolationTests`, real-provider run |
| C. Planning, delegation, reconsideration | Done: `plan` → review → `approve-task --assign` → `run`; denied operations become requests; once-only approvals bound to one attempt, one open and one applied edit; delegated children within template; refused/stale/substituted/replayed stay denied | `ReconsiderationTests`, `test_reconsideration`, delegation test |
| D. Lifecycle, recovery, provenance | Done: cancel/revoke/expiry/owner death/broker loss/repeated denials stop the namespace; `recover` verifies observed cleanup, never replays or revives; report shows application outcomes | `LauncherTests`, `ReviewRuntimeTests`, `test_report` |
| E. Acceptance and evaluation | Done: confined oracle, frozen protocol, 18-run matched comparison with real LaaS repeated on the reviewed code (36 runs, all completed and authorized; disclosure native 1/12, confined 0/24), CLI demonstration, strict mypy, independent review with all findings fixed | `test_evaluation`, `test_comparison`, evidence |
| 9. Directories, external inputs, blind writes, single-use binding | Done on Linux | as above |
| 9. macOS | Moved to a follow-up plan by the user's scope change (below). Blocked externally: no Xcode, no code-signing identity with the FSKit entitlement, extension approval in System Settings and a privileged cleanup helper need the user. FSKit candidate sources type-check against the macOS 27 SDK. | [assessment](MACOS_BACKEND_ASSESSMENT.md) |
| 9. Windows | Moved to a follow-up plan by the user's scope change (below). Blocked externally: no Windows instance available | — |

The final complete Ubuntu suite on one source state passed: 235 tests, 209 passed,
26 skipped (macOS-only, case-insensitive-only, Node absent on the guest, and the
real-provider gate), 0 failures. The 9 Node-gated bridge tests pass with Node 22
on the macOS host on the same sources, and the real-provider gate is covered by
the separately recorded real-provider run. Earlier failed attempts are kept in the evidence
with their causes. The comparison was repeated on the reviewed code with the same
outcome on completion and authorization and no disclosure in any mode. Two runs
per cell still support no behavioural conclusion; more runs per cell are needed
before claiming that confinement changes disclosure rates.

### Scope change agreed by the user — 17 November 2026

Per section 3, the plan cannot be called complete with open criteria unless the
user agrees to a scope change. On 17 November 2026 the user explicitly chose:
**this plan completes on Linux; macOS and Windows move to a separate follow-up
plan.** Their requirements from section 9 and the original plan are unchanged and
unmet. They are not supported, and no adapter or pending test counts as support.
The follow-up plan starts from the [macOS assessment](MACOS_BACKEND_ASSESSMENT.md)
and the prerequisites listed in [known limitations](KNOWN_LIMITATIONS.md).
