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
