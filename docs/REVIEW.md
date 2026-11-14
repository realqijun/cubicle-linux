# Implementation review

Reviewed against starting commit `4d612f3d0e93bf5565a17761f194955a06bdbcb8`.
The initial implementation commit is `25570fb`; follow-up fixes address the
findings below. Two independent reviewers ran with fresh contexts. The spec is
`docs/PLAN_PLANNER.md`; the user's later instruction selected local Mac testing
instead of the VM. The project remains incomplete.

## Standards

No hard violations found against the documented implementation principles or
README standards.

One optional heuristic finding:

- **P3 — Duplicated Code: centralize persisted grant decoding.**
  `supervisor.py` repeated the six-field `Grant` reconstruction in normal grant
  lookup and recovery. Changes to the stored shape could diverge. This is a
  smell-based judgment call, not a documented rule violation.

**Resolution:** both paths now use one small `_decode_grant` function. Existing
public lifecycle tests verify the behavior; no new abstraction was added.

## Spec

- **P1 — Full execution deliverables remain incomplete.** `enforcement.py`,
  `connector.py`, and `supervisor.py` block autonomous execution. Verified
  confinement, complete OpenCode mediation, live worker lifecycle/recovery,
  and matched agent comparisons remain outstanding. The spec says:
  “Legitimate authorized tasks must remain executable; refusing all work is
  not an acceptable result.” Blocking is correct given failed macOS diagnostics;
  this is incomplete delivery, not an authorization bypass.
- **P1 — Approved hardlink reads lost sensitivity.** The approval's sensitive
  flag was dropped from the returned decision, allowing a caller to persist
  sensitive-derived note text. The spec requires propagating sensitivity and
  preventing automatic persistence. **Fixed:** exception decisions preserve
  both decision and approval sensitivity. The regression uses the real
  provenance API and rejects note persistence.
- **P2 — Fresh comparison runs acquired different matching hashes.** Fresh
  workspace inode identity was included in matching configuration. The spec
  requires fresh state and matched inputs/settings. **Fixed:** physical root
  identity remains in audit metadata and is excluded from the comparison hash.
  The fresh-run comparison regression passes.
- **P2 — Ordinary authorization paths were disconnected in the visualization.**
  Only approval-backed decisions linked to authorization records. The spec
  requires a graph connecting installed grants and attempted operations.
  **Resolution:** decision records now snapshot the actual checked grant
  revision; the report links matching recorded grant relationships and marks
  missing or mismatched evidence rather than inventing history.

No confirmed unrequested scope or additional deletion, replay, or project
isolation defects were found.

Standards: one optional finding, resolved; no hard violations. Spec: four
findings, three addressed; incomplete execution remains the worst open issue.

## Validation notes

The deterministic planner suite, development typecheck, and compilation are
run separately from real OS contract diagnostics. The local candidate's latest
recorded attempt passes 12 of 14 probes. Live external hardlink aliasing and
detached descendants fail; production launch remains disabled. Four attempts,
including unsuccessful fixes, are retained in
[`macos-candidate.json`](../evidence/macos-candidate.json).

The final planner discovery run executes 78 tests: 63 pass and 15 skip. Fourteen
skips are the opt-in native probes (executed separately as above); one is the
independent oracle acceptance test requiring a verified backend. Strict mypy
checks 13 production source files successfully, and compilation passes.
Existing decision databases receive a nullable grant-revision column on restart;
legacy rows remain explicitly without that evidence. A regression checks this
upgrade through the public authorization API.

The unchanged `artifact/` helper suite has 18 tests: 16 pass, one fails, and one
errors. Both failures concern its legacy collector's `--dir` assumptions:
`test_model_commands_use_explicit_model_and_json_output` and
`test_collector_handles_agent_error_and_refuses_rerun`. `git diff` against the
starting commit confirms no changes under `artifact/`. Its copy-only preparation
API is reused; its unconfined collector/checker is not a supervised executor.

Browser visual verification was blocked by the browser tool's local-file URL
policy. No alternate transport was used to bypass that restriction. The report's
metadata generation, escaping, and JavaScript syntax are checked, but browser
rendering remains unverified.

## Continuation review: Standards

Fixed point: `2fc47eed4231ca1e8942b50cb0379400e8213e10`; initial continuation
commit: `aea4301`. The user explicitly deferred OS acceptance to Ubuntu while
requesting the remaining software implementation.

One documented correctness finding:

- **P1 — Apply the timeout before parsing HTTP headers.** The broker set its
  socket timeout inside `do_POST`, after the standard handler reads request
  lines and headers. An incomplete unauthenticated request could occupy the
  single serving thread indefinitely. Blocking `HTTPServer.shutdown()` could
  then hang teardown before the timed join. This contradicted the documented
  configurable request-time limits.

**Fixed and rechecked:** timeout setup now precedes HTTP parsing. The serving
loop uses a stop event and bounded join rather than an unbounded shutdown call.
A real partial-header socket test passes and confirms that the next request is
served. A stuck serving thread is reported, not claimed cleaned up. No further
Standards changes were requested; no additional actionable smells were found.

## Continuation review: Spec

One confirmed finding:

- **P2 — Persist mediation refusals in decision history.** The spec requires
  mediated decisions and attempted operations to be recorded. Unsupported
  tools, unreviewed provider origins, replay, and other protocol refusals
  returned a denial without durable evidence. The reproduced unsupported-tool
  refusal left both history and provenance empty.

**Fixed and rechecked:** non-filesystem denials now enter supervisor decision
history with sanitized protocol route and fixed reason codes. JavaScript early
guards send metadata-only refusal events before blocking; oversized envelopes
use a compact denial without raw bytes or a sequence gap. Unknown route strings
are not persisted. A restart test verifies durable history and absence of dummy
secret payloads. No further confirmed core Spec bugs were found.

Continuation totals: Standards one finding, resolved; Spec one finding, resolved.
The Ubuntu acceptance work remains explicitly deferred, rather than counted as
a newly introduced core defect or a completed security guarantee.

## Continuation validation

- Planner discovery: **124 tests, 109 pass, 15 skip**, including the real
  loopback transport regression. The fourteen macOS probes remain opt-in and
  the oracle acceptance requires a verified backend; neither is counted as a
  new passing confinement result.
- Strict mypy: **18 production source files pass**. Compilation, bridge
  JavaScript syntax, and whitespace checks pass.
- Actual OpenCode 2.2.5 + supervisor/broker + fixed local provider:
  **four of four cases pass**. Permitted reads/writes work; the identical denied
  write has no write effect and the model can continue; an unreviewed provider
  receives no requests. Every observed fixture provider request is admitted by
  the bridge. Seven attempts, including failures, are retained in
  [`opencode-http-fixture.json`](../evidence/opencode-http-fixture.json), with
  source hashes stable across the final attempt.
- `artifact/` remains unchanged from the continuation baseline. Its previously
  recorded two collector-test failures remain separate from the planner suite.

These checks validate the implementation boundaries and fixed integration
fixtures. They do not establish OS confinement, complete real-provider coverage,
safe harness persistence, or matched autonomous-agent comparisons. See
[KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) for the accepted Ubuntu retest plan.

## Ubuntu private-workspace review: Standards

Fixed point: `89a5581ffa30e8924f921329f90bc4a201d96912`; initial implementation
commit: `d2cc201`. Two fresh agents reviewed Standards and Spec independently.
The scoped behavior is documented in [LINUX_SANDBOX.md](LINUX_SANDBOX.md).

- **P2 — Include the referenced Linux evidence.** Documentation linked an
  artifact that was still being collected. **Resolved and independently
  rechecked:** [the evidence](../evidence/linux-private-workspace.json) now
  contains six attempts, including failures, final per-test outcomes and matching
  source hashes. No other actionable standards findings remain.

## Ubuntu private-workspace review: Spec

- **P1 — Ancestor rename exposes an exact-path denial.** Denying writes to a
  protected filename did not prevent renaming its permitted parent directory.
  **Reproduced, fixed and rechecked:** workspace directory writes are denied,
  preventing creation/removal/rename of directories. Existing-directory file
  replacement remains available. A real exact-path-secret regression passes.
- **P2 — `io_uring` bypasses the symlink syscall blacklist.** Asynchronous
  symlink operations do not enter the blocked `symlink` syscalls. **Reproduced,
  fixed and rechecked:** all three `io_uring` entry points are denied. The guest
  can create a ring outside confinement; the same operation is denied inside.

Standards: one finding resolved. Spec: two findings resolved. Both independent
reviewers report no remaining actionable findings in the final source.

## Ubuntu private-workspace validation, 13 November 2026

- Ubuntu 24.04.3 x86-64, kernel `6.8.0-137-generic`, Python 3.12.3;
  Bubblewrap `0.9.0-1ubuntu0.3`, AppArmor
  `4.0.1really4.0.1-0ubuntu0.24.04.4`, libseccomp `2.5.5-1ubuntu3.1`.
- Final scoped contract: **23 pass, zero failures/errors/skips**: twenty actual
  process probes and three public snapshot API tests. An additional administrative
  probe confirms that a complain-mode profile returns 125 before the supplied
  command can create its marker. Enforcing mode was restored afterward.
- Complete Ubuntu suite: **147 tests, 121 pass, 26 skip**. macOS-only probes,
  case-insensitive filesystem probes, unavailable Node bridge checks and the still-gated independent oracle are
  separate from the passing Linux confinement contract.
- Complete local macOS suite: **147 tests, 112 pass, 35 skip**. Linux probes are
  skipped locally, rather than counted as passing. Strict mypy passes all
  **20 production source files**; whitespace and local documentation links pass.
- Testing also exposed a trailing-`**` zero-component bug and permitted socket
  creation under AppArmor's default compatibility ABI. Both are fixed and have
  real-process regressions. The profile now declares ABI 4.0 and explicitly
  denies networking. Profile escape, injected unassigned files, new sensitive
  names, original-project hardlink injection, inherited handles/environment,
  protected writes, revocation, timeout and launcher-loss cases pass.
- Slow guest scheduling caused earlier startup deadlines to fail; probe deadlines
  are now configurable and lifecycle probes synchronize on actual checkpoints.
  One intermediate SSH wrapper timed out before collecting a complete attempt;
  no passing result is inferred from that interrupted run. The six completed
  diagnostic attempts retain their failures without raw worker output.

These results establish the documented **offline private-workspace** mode on
this guest. They do not enable the live-project `Backend`, provide provider
network access, integrate model provenance or automatically merge edits. Those
full-runtime acceptance items remain explicit limitations.

## Documentation and code cleanup, 15 November 2026

Fixed point: `1163163`; cleanup commit: `405d3d0`.

### Standards

No findings. Independent review checked the strict typechecking configuration,
development instructions, sandbox restrictions, command paths and changed code.
No actionable baseline code smells were found.

### Spec

No findings. Independent review confirmed that the shorter guides and updated
diagram distinguish tested Ubuntu offline execution from the gated full launcher
and live-project backend. Sandbox restrictions and historical evidence remain.

### Validation

- Local suite: **147 tests, 112 pass, 35 platform-specific skips**. Strict mypy
  passes all **20 production source files**. The targeted launch-gate test and
  whitespace checks pass.
- The documented offline worker assignment and profile generation were exercised
  with a real temporary supervisor database. Local documentation links resolve.
- The Mermaid SVG was regenerated with CLI 12.2.0 and visually checked. It uses
  native SVG text and includes the offline runner and pending launcher path.
- All four source hashes in the latest Linux contract attempt still match.
  Confinement code, policy code, Linux probe code, fixture inputs and evidence
  files are unchanged. No new OS acceptance run is claimed for this cleanup.
- Python changes clarify the live-project gate's message, remove an unused
  executable-presence probe and correct the macOS diagnostic's recorded command
  path. The historical macOS evidence retains its original diagnostic hash.
  Generated Python and mypy caches were removed from the local checkout.

Review totals: Standards zero findings; Spec zero findings.
