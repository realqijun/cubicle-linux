# Known limitations and remaining work

The [connected runtime](CONNECTED_RUNTIME.md) and the [offline sandbox](LINUX_SANDBOX.md)
are accepted on Ubuntu 24.04.3 x86-64 only. The generic live-project `Backend`
stays gated. Skipped tests and approval records do not establish containment.

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

## Connected runtime

Accepted on one Ubuntu guest; see its [limits](CONNECTED_RUNTIME.md#limits). Also:

- The AppArmor and seccomp profile is a candidate specific to this runtime; its
  name is a hash of the runtime sources, so changes require reloading it.
- Harness persistence was checked on the host side (no session content survives
  in operator storage) and for repository configuration discovery; OpenCode's
  own state lives on tmpfs inside the jail and is discarded.
- Matched comparison results are single runs per cell; see [evaluation](EVALUATION.md).

## Other execution modes and platforms

- **Live project:** `Backend.run()` refuses all launches. A backend needs its own
  complete contract for dynamic path denials, aliases, file replacement, runtime
  resources, inherited handles and descendants before this gate can open.
- **macOS:** not supported. The Seatbelt candidate still fails 2/14 probes
  ([evidence](../evidence/macos-candidate.json)). The FSKit candidate sources
  type-check against the macOS 27 SDK, but building, signing and enabling the
  extension needs Xcode, a code-signing identity with the FSKit module
  entitlement, and the user's approval in System Settings; detached-descendant
  cleanup needs a privileged helper. None is available on the test host. See
  the [assessment](MACOS_BACKEND_ASSESSMENT.md).
- **Windows:** not supported; no Windows instance was available for acceptance.
- **Other Linux environments:** no acceptance results establish support. Do not
  infer support from an executable, kernel feature or container being present.

The earlier decision to defer platform testing is historical. Ubuntu testing
resumed on 13 November 2026; the successful private-workspace mode does not
resolve the separate live-project and model-connected contracts. See the
[enforcement overview](ENFORCEMENT.md) and [review history](REVIEW.md).
