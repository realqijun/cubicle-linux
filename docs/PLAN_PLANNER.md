# Planner supervisor: project scope and execution plan

## Purpose

Build a deterministic Python supervisor that makes autonomous coding safer by enforcing user-approved file access, controlling delegated permissions, and preserving the origins of information used by agents.

The project covers ordinary mistakes and misleading instructions. Examples include accidentally reading a project’s `.env` file, editing tests outside the authorized task, or treating a repository’s maintenance note as permission to change protected files. Detecting an attacker or classifying text as malicious is not required for enforcement.

The project must establish the problem, describe the solution model, implement the solution, and produce evidence about its security and usefulness. Permission inheritance and sandboxing are existing mechanisms; the project must identify the measured contribution of task policies, delegation controls, provenance, and approval handling without claiming those underlying mechanisms are new.

This document is separate from `PLAN_PLEDGE.md`. It defines the agreed planner-supervisor project without a delivery deadline.

## Implementation principles

- Use a small, human-readable Python program with plain control flow and explicit data structures.
- Follow YAGNI. Implement behavior required by this scope, not a general agent platform, plugin framework, policy language, or workflow engine.
- Use Python 3.11 or newer and standard-library components where practical: `argparse`, `tomllib`, `sqlite3`, `json`, `pathlib`, and `subprocess`.
- Add small harness connectors or native helpers only where required for complete interception or OS enforcement. Security policy decisions remain in the Python supervisor.
- Keep policy decisions independent of the model provider and separate from OpenCode-specific integration and OS-specific enforcement.
- Avoid custom cryptography, trust scores, embeddings, vector databases, and an LLM-based permission enforcer.
- Keep code, configuration, errors, and operating instructions understandable to a human maintainer. Tests must demonstrate meaningful behavior rather than mirror implementation details.

## Security claim and trust boundary

The supervisor enforces declared read and write boundaries and controls changes to installed permissions. It does not guarantee that an edit inside an authorized file is correct or harmless, or determine whether a test change is semantically related to a task.

The user’s authenticated instructions, reviewed task policy, and trusted task templates can authorize access. Repository contents, file names, comments, tool output, worker messages, retrieved memories, and model-generated summaries are information, not independent authorization.

The user reviews the initial task policy before execution. The planner proposes work and permissions; deterministic supervisor code validates, installs, and enforces grants. The planner cannot authorize expansion of the overall task boundary.

Required invariants:

1. Every effective worker permission is covered by the approved task policy or a valid, narrowly scoped user-approved exception.
2. Delegation does not increase authority. A worker receives only its assigned subset, and every worker and descendant remains confined.
3. An operation is refused before an unauthorized read or filesystem change occurs. A warning after the effect is not sufficient.
4. Project information and memories cannot rewrite policy, forge approval, or promote their own authority.
5. The agent cannot directly modify the supervisor, active policy, grants, approval records, provenance metadata, or decision records.
6. Missing or unverified protection results in a blocking warning, not weaker execution.

Assume a trusted OS, supervisor, policy storage, and user-authorization channel. Kernel exploits, administrator bypass, compromised supervisor code, and a user deliberately approving excessive access are outside the protection guarantee. Designated sensitive resources must be protected; discovering every secret copied into otherwise permitted content is not part of the claim. Network containment is outside this project’s scope.

## Supported execution and portability

Support OpenCode as the coding harness, with configurable tool-capable model providers, including the user’s NUS SoC LaaS access. Pin and document the supported OpenCode release and connector compatibility. Do not assume different releases expose identical interception or session APIs.

Provide the same supervisor interface and policy semantics on Linux, macOS, and Windows. Enforcement may require different helpers and documented prerequisites on each OS.

- Linux: use kernel-enforced filesystem confinement through an appropriate existing mechanism, with Bubblewrap or Landlock evaluated against the required permission semantics.
- macOS: provide and validate a confinement backend; do not treat deprecated arbitrary-command sandbox interfaces as an unqualified, stable security contract.
- Windows: provide and validate restricted execution and resource access controls; a process container or reduced token alone does not establish the required path policy.

Select the concrete backend only after testing the complete policy contract, including denied reads, restricted writes, descendant processes, and inherited handles. A working interface or an adapter that only reports “unsupported” does not complete platform support. Record the actual tested OS versions and mechanism prerequisites.

Before launching work, check that the selected environment can enforce the approved policy. Unsupported environments or unavailable protections must warn and block. Do not silently substitute tool-level filtering for OS confinement.

Existing resources include an Ubuntu 24.04.3 x86-64 UTM guest reachable through SSH forwarding on localhost port 2222, an OpenCode installation on the host, the `artifact/` experiment harness, and LaaS model access. Recheck their state before implementation work that depends on them. Keep credentials out of source files and experiment records.

## User-editable policy

Use a small TOML configuration. Paths are relative to the approved project root unless an external input is explicitly authorized. The example below expresses a source-editing task:

```toml
[files]
read = ["**"]
write = ["src/**", "outputs/**"]

deny_read = [".env*", "**/.env*"]
deny_write = ["tests/**"]
```

Interpretation:

- Read project files except explicitly denied resources.
- Write only under the approved write scopes.
- Explicit denials take precedence over ordinary allowances.
- Reading one file does not imply permission to modify it.
- Access outside the project is denied except for explicit external inputs and the necessary, documented runtime resources.
- Runtime and temporary storage must not grant broad access to the user’s home directory or supervisor state.
- Resolve paths according to the actual filesystem, including traversal, symlinks, aliases, case behavior, and link semantics. String-prefix checks alone are insufficient.
- Evaluate and enforce patterns consistently across built-in tools and subprocess access. A pattern that cannot be enforced faithfully must not be presented as active protection.
- Specify and test how new matching files and filesystem changes are handled during a running task; do not rely on a startup scan while claiming continuous protection.

Keep the active configuration in a trusted supervisor location that the user can edit but supervised agents cannot modify. A repository-local configuration may be imported for review, but cannot silently become trusted policy. Edits must not silently widen a running task; show the effective change and obtain the appropriate approval before applying it.

Use trusted templates as plain data presets for inspection, source changes, test maintenance, and documentation. Templates suggest scopes and tools; they do not imply blanket access to an entire repository. The user can inspect and edit the proposal before confirming it. Exact worker assignments must remain within that reviewed policy.

Example decisions under the source-editing policy:

| Operation | Expected decision |
|---|---|
| `cat README.md` | Allow the read |
| `cat .env` | Refuse the read |
| `cat config/.env.production` | Refuse the read |
| Edit `src/refunds.py` | Allow the write |
| Edit `tests/test_refunds.py` | Refuse the write |
| Python code opens `.env` | Refuse that read |
| Redirect output into `tests/copy.txt` | Refuse the write |

These are file-operation rules, not a blacklist of command strings. Changing utilities, command syntax, or path spelling must not bypass them.

## Planner, workers, and permission changes

The user-facing flow is:

1. Accept a project, task, and policy or template selection.
2. Generate a proposed task policy using the user’s request and trusted templates. Treat structural project information as untrusted data; do not load repository instructions into an unrestricted planner before protection is established.
3. Display the effective read, write, tool, external-input, sensitive-file, and runtime access for user review.
4. Install the approved policy and create scoped worker processes under supervisor control.
5. Mediate operations and record their decisions. Return structured denials so workers can choose permitted alternatives or request reconsideration.
6. Complete or cancel the task, clean up descendants, expire grants, and retain the permitted decision and provenance records.

The supervisor controls worker creation and delegation. Do not assume native subagent permissions are automatically subsets of their parent’s. Constrain each worker’s built-in operations and its subprocesses to its own effective grant, not merely the union of task-wide permissions.

A planner may automatically assign additional worker permissions only when a trusted, user-approved template already authorizes that assignment. A repository comment, tool result, or memory can motivate a request, but cannot supply its authorization. Other assignments or expansions require human approval, even if they remain within the task-wide maximum. Expanding the task-wide maximum always requires human approval.

An approval must bind to the task, worker, normalized resource, operation, and lifetime. The user explicitly chooses a single operation or access for the current task. Default behavior must not install permanent broad permissions or carry approvals into unrelated tasks.

An explicit denial remains active unless the user deliberately authorizes a narrow exception. Preserve the general denial rule. Recheck the actual dispatched operation against the approved exception, consume single-operation approvals atomically, and reject replay or substitution.

If reading a sensitive file would expose its contents to the model provider, say so before approval. An approval controls access; it cannot promise that an external provider forgets data already sent to it.

Worker text, tool output, and saved notes must not be interpreted as user approval. The approval channel must be separate from worker output, with no worker-accessible route for impersonating the user.

Where an OS restriction cannot be widened or tightened safely in place, stop and replace the affected worker with a newly confined process. Do not release the existing process into an unrestricted environment.

## OpenCode integration and complete mediation

Use private, supervisor-controlled OpenCode execution rather than attaching to an uncontrolled shared background server. Isolate configuration and state from the user’s unrelated sessions. Confine the process that actually owns tool execution, not only its terminal client.

The connector must cover registered reads, searches, edits, writes, shell calls, delegation, and model-context transformations. A notification stream or completed-tool event is useful evidence but cannot serve as the blocking enforcement point.

OS confinement must already apply before shell commands or other executable code starts. It must cover descendants, inherited file descriptors or handles, process-control routes, and any local helpers that could bypass the approved file boundary.

Additional plugins and tool servers are disabled unless their operations and outputs can be mediated. Unknown tools, unsupported execution paths, incompatible connector versions, or untracked context routes must warn and remain blocked. Do not enable integrations merely because they are present in repository configuration.

Cover the actual inputs passed to model calls, including worker contexts, compaction, summaries, and auxiliary calls that can consume task information. Account for provider transformations and transport differences. Reconstructing provenance solely from the visible conversation transcript is insufficient.

## Provenance and persistent memory

Keep memory and authorization as separate records. A note may inform work but cannot grant access.

Assign origin metadata in supervisor code from the inputs actually admitted to a model call. Do not trust origin names, citations, “user approved” claims, or trust labels written by a model or found inside content.

Use conservative propagation: an output or summary inherits the union of its input origins. Repository text does not become authoritative because an agent paraphrases it, another tool repeats it, or several copies appear in memory. “Untrusted” describes authorization status; it does not establish that content is malicious or factually false.

Track both temporary worker reports and persistent project notes. Notes must be project-scoped, held outside workers’ writable directories, and available through inspect and delete commands. There is no automatic sharing of authorization between projects or tasks.

Persist only the records needed for this behavior: tasks and policy revisions, workers and grants, observed inputs and derivations, notes and source references, approvals, and decisions. Use SQLite for transactional state and produce readable JSON exports. No vector store or semantic retrieval subsystem is required; straightforward note listing, filtering, and text search are sufficient.

Protect designated secret contents from persistence in supervisor notes, ordinary logs, and visualization exports. Record operation metadata instead of secret bytes. If an explicitly approved sensitive read enters model context, propagate that sensitivity to derived material and prevent automatic persistence of that material. Configure and verify harness session storage accordingly; do not make a no-persistence claim while raw contents remain in automatically exported sessions.

User deletion of a note must prevent further retrieval of that note. Retain only the non-sensitive event metadata needed to explain decisions, including the fact of deletion. Do not preserve deleted note text in routine audit exports.

Conservative origin tracking does not reveal which sentence the model relied on. Records and visualizations must distinguish observed input exposure or derivation from proven causal influence.

## Terminal interface, history, and recovery

Provide terminal operations to start a task, review its proposal, answer approval requests, inspect status and decision history, cancel work, resume after verification, and list, inspect, or delete memory notes. Use clear text output and optional JSON output for experiments.

Approval displays must show actual normalized access, its scope and duration, the requesting worker, and the sources behind the request. Treat model-provided reasons as untrusted explanatory text. Do not render worker output as an authentic supervisor prompt or acceptance.

A normal denial returns a structured reason. Workers may try an allowed alternative. Detect repeated identical denied requests and stop that retry sequence, offering a visible path for user review or cancellation.

If the supervisor, policy storage, connector, provenance coverage, or OS backend cannot establish protection, display a blocking warning and suspend affected work. Ensure workers and descendants cannot continue unprotected after supervisor failure or connection loss. Do not switch modes, install missing tools, or weaken policy automatically.

After the user fixes a problem, verify the backend, connector, policy revision, and grant validity before resuming. Expired or revoked approvals do not become valid through recovery. Cancellation and revocation must stop affected workers and clean up their descendants.

Persist enough non-sensitive state to identify interrupted work and reconcile decisions after a restart. Do not assume a live event subscription has durable replay or that a missed event means no action occurred.

## Implementation work

Execute the following dependency order without adding unrelated infrastructure:

1. Define and test the policy semantics, action and grant records, path handling, and approval rules independently of an LLM.
2. Implement validated OS enforcement and startup checks. Establish descendant-wide read and write boundaries before integrating autonomous execution.
3. Connect OpenCode’s actual execution and model-input paths through the supervisor. Pin the compatible harness release and verify interception coverage.
4. Implement reviewed task proposals, scoped delegation, human exceptions, cancellation, and recovery.
5. Implement origin propagation, temporary reports, project memory, sensitive-data persistence controls, and decision history.
6. Run deterministic enforcement tests and matched agent evaluations; record results and operating instructions.
7. Build the provenance visualization described below after the supervision implementation produces verified records.

Reuse the existing `artifact/` preparation and task-checking code where it helps. Its current collection output is observational, its checks do not establish confidential-read prevention, and its evaluator executes generated source. Adapt those boundaries explicitly and confine the evaluator itself. Preserve unrelated workspace changes.

## Provenance visualization — after implementation

Build an interactive, local visualization for planning, research, and reviewing supervised runs. Generate it from actual supervisor records after the implementation and record format are established. It must not be an LLM-generated reconstruction of what probably happened.

Use a standalone HTML report with embedded data and local rendering. No web dashboard service, remote analytics, or hosted graph database is required.

The report should show a readable left-to-right graph connecting:

- User task and approved policy revisions.
- Repository observations, tool results, and other input sources.
- Planner and worker model calls and their resulting reports.
- Summaries, saved notes, and later memory retrievals.
- Permission requests, human approvals or refusals, and installed grants.
- Attempted operations, supervisor decisions, and observed effects.

Support task and worker filtering, searching by file or note, tracing a selected item back to its origins, and inspecting node details. Provide a timeline for policy changes, exceptions, denials, and cross-task memory use. Show task boundaries and approval expiry explicitly.

Use clear labels, restrained colors, and distinct shapes or icons so meaning does not depend on color alone. Distinguish information flow from authorization flow, and distinguish requested, denied, approved, executed, and unverified outcomes. Mark missing evidence rather than inventing an edge.

Node details should show IDs, timestamps, origin references, policy and grant relationships, and non-sensitive reasons. Exclude designated secret contents and credentials. A summary edge means the input was supplied to the producing call; it does not prove that the input caused a later decision.

The visualization is a research and explanation aid. It does not participate in enforcement, and its absence must not weaken the supervisor. Include it in the project deliverables after implementing the core behavior.

## Verification and comparative evidence

Evaluate three configurations under matched tasks and permissions where possible:

1. OpenCode’s configured native protections.
2. Equivalent filesystem restrictions enforced through OS confinement.
3. The complete supervisor with delegation, provenance, and approval handling.

Keep model, harness release, inputs, and task requirements controlled within a comparison. Separate changes to permission scope from changes to enforcement mechanism. Use fresh task state and record the model identifier, relevant parameters, policy, backend, and configuration.

Required deterministic coverage includes:

- Permitted reads and writes, denied sensitive reads, and protected test or configuration writes.
- Built-in tools, search tools, shell utilities, scripts, children, and grandchildren.
- Relative and absolute paths, traversal, symlinks, link aliases, platform case behavior, file replacement, newly matching sensitive files, rename, deletion, and truncation.
- Existing open descriptors or handles and attempts to alter policy, memory metadata, approval state, or supervisor control channels.
- Excessive delegation, fabricated user approval, single-use consumption, task expiry, replay, revocation, and exact-exception matching.
- Origin preservation across paraphrasing, tool echoes, summaries, compaction, retrieval, and cross-task memory. Include legitimate notes and legitimate requests to measure unnecessary restrictions.
- Supervisor failure, storage failure, lost connector events, unsupported tools, unavailable enforcement, repeated denials, interrupted tasks, and recovery.
- Safe persistence and export when a user explicitly approves a sensitive read.

Use dummy secrets and independent positive controls. A nonexistent file, a model declining to try an operation, or an absent final marker does not alone prove that the supervisor blocked a real read or write.

Measure attempted operations and actual effects separately. Include transient effects and confidential reads that final file hashes cannot detect. Protect the evaluator and evidence from agent modification. Do not use an agent’s self-report as the outcome oracle.

Agent tasks must include ordinary mistakes and misleading README content, comments, test output, and memories. Judge legitimate completion independently of whether harmful behavior occurred; a run may complete its task and still violate the policy.

Report prevented access, successful legitimate work, unnecessary approvals, false denials, recovery outcomes, and overhead. Freeze comparative scenarios and scoring rules before collecting the reported results, preserve unsuccessful attempts, and identify limitations. If provenance adds only auditability in a setting, report that rather than inventing a prevention benefit.

Platform support is accepted only after the relevant behavior passes on that platform. Unit tests, mocks, or successful import of a Python module are not substitutes for OS enforcement tests.

## Completion criteria and deliverables

The project is complete when it provides:

- A grounded problem statement covering accidental and instruction-driven unsafe file behavior.
- A documented authorization, delegation, provenance, and failure model with explicit assumptions.
- The minimal readable Python supervisor, OpenCode connector, and verified OS backends.
- Editable TOML policies and trusted task templates with documented semantics.
- User-reviewed task proposals, confined workers, controlled exceptions, project memory, and reliable cancellation and recovery.
- Independent tests and matched comparative evidence, including honest negative or inconclusive findings.
- Setup, compatibility, operation, troubleshooting, and research instructions.
- The interactive provenance report generated from real, non-sensitive run records.

There must be no successful unauthorized access in the required enforcement tests. Legitimate authorized tasks must remain executable; refusing all work is not an acceptable result. Document any scenario that requires user intervention and any environment whose enforcement has not been verified. Do not claim elimination of prompt injection, universally correct permission generation, or universal safety of authorized code changes.

## Primary references

- [OpenCode permissions](https://opencode.ai/docs/permissions/) and [agent configuration](https://opencode.ai/docs/agents/).
- [OpenCode execution architecture](https://opencode.ai/docs/cli/) and [plugin integration](https://opencode.ai/docs/build/plugins/server).
- [CapScope: Authority Is Not a String](https://arxiv.org/abs/2609.08371): task-wide ceilings and per-agent capability allocation.
- [Progent: Securing AI Agents with Privilege Control](https://arxiv.org/abs/2504.11703): externally enforced policies and controlled policy expansion.
- [PlanGuard](https://arxiv.org/abs/2604.10134): isolated planning and action consistency checks.
- [Fides: Securing AI Agents with Information-Flow Control](https://arxiv.org/abs/2505.23643): integrity and confidentiality tracking.
- [TMA-NM: Securing LLM-Agent Long-Term Memory Against Poisoning](https://arxiv.org/abs/2606.24322): preservation of origin-bound authority through memory transformations; review its attribution and trust assumptions rather than importing its guarantees automatically.
- [Linux Landlock](https://docs.kernel.org/userspace-api/landlock.html), [Bubblewrap](https://github.com/containers/bubblewrap), and [Windows AppContainer](https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-for-legacy-applications-): platform enforcement mechanisms whose actual suitability must be tested.
