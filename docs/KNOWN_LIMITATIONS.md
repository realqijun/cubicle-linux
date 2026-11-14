# Known limitations and remaining work

The [Ubuntu offline sandbox](LINUX_SANDBOX.md) is tested on the recorded guest.
The full model-connected agent launcher and live-project `Backend` remain gated.
Skipped tests and approval records do not establish execution or containment.

## Ubuntu offline sandbox

The final [recorded contract](../evidence/linux-private-workspace.json) passes
23 checks on Ubuntu 24.04.3 x86-64, kernel 6.8.0-137-generic. Its limits are:

- Commands run offline in a private copy. The original project is not mounted;
  edits need human review and manual application.
- Existing-directory file edits and replacement are supported. Directory
  creation/removal/rename, symlinks, hardlinks and `io_uring` are unavailable.
- Read-denied paths are also write-denied. Scope syntax is a supported subset of
  the control-plane policy language; external inputs are refused.
- The source must be quiescent during preparation and the run directory must
  remain trusted. This does not defend against a hostile host administrator or
  a process able to tamper with the supervisor under its own UID.
- `/usr` is the explicit trusted runtime. Wildcard scopes can expose directory
  names. CPU, memory and output quotas are not implemented.
- The operator CLI polls current authority. Revocation is detected within the
  polling interval plus scheduling delay. The Python API's optional authority
  callback is the trusted caller's responsibility.
- This runner does not populate durable execution-attempt records, mediate model
  inputs or provide network access to a provider. It does not make live-project
  preflight succeed.

Repeat its contract tests after kernel, AppArmor, Bubblewrap or relevant code
changes. See [the runner guide](LINUX_SANDBOX.md) for exact setup and restrictions.

## Model-connected launcher

The OpenCode 2.2.5 bridge, broker and context admission are implemented and tested
with fixed provider fixtures. Full-runtime acceptance still requires:

1. Connect confinement, private no-task server startup and authenticated hook
   verification before dispatching a reviewed prompt. OpenCode can log plugin
   setup failure and continue without the plugin.
2. Provide reviewed model-provider transport and worker/broker isolation inside
   confinement. Preserve current fail-closed handling of unsupported transports,
   tools, attachments, plugins and ambiguous context lineage.
3. Verify system/managed configuration discovery and harness-wide session, log,
   cache, attachment and truncated-output persistence. In-memory records and
   stripping child environment variables do not alone establish isolation.
4. Wire process ownership, cancellation, grant expiry, exception replacement,
   connector loss, storage failure and cleanup evidence into durable attempt
   records and recovery. A database state change does not terminate a process.
5. Run the pinned connector fixtures inside confinement with positive controls,
   observe actual effects and retain failed attempts. Then collect matched
   native/OS/supervisor agent comparisons using the frozen evaluation settings.

The current fixed-provider diagnostic is an integration check; it is not a
matched autonomous-agent evaluation. [Connector details](OPENCODE.md) and
[control-plane lifecycle](CONTROL_PLANE.md) describe the existing interfaces.

## Other execution modes and platforms

- **Live project:** `Backend.run()` refuses all launches. A backend needs its own
  complete contract for dynamic path denials, aliases, file replacement, runtime
  resources, inherited handles and descendants before this gate can open.
- **macOS:** the disposable Seatbelt candidate passes 12/14 probes; live hardlink
  injection and detached-descendant lifetime fail. It remains diagnostic-only.
  [Evidence](../evidence/macos-candidate.json) retains the failures.
- **Windows and other Linux environments:** no acceptance results establish
  support. Do not infer support from an executable, kernel feature or container
  being present.

The earlier decision to defer platform testing is historical. Ubuntu testing
resumed on 13 November 2026; the successful private-workspace mode does not
resolve the separate live-project and model-connected contracts. See the
[enforcement overview](ENFORCEMENT.md) and [review history](REVIEW.md).
