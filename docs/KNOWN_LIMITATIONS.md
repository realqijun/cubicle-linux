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
- `--alert` only displays refusals; it never enforces one. Over SSH only the
  text and the terminal bell reach the viewer, since the desktop sound and
  notification run only in a local graphical session. A busy state database
  delays a warning to the next poll, or leaves it out of the live display at
  the final check after the run; the refusal itself stays recorded.
- A task that expires in the seconds between runtime verification and worker
  start is reported as `runtime_error:ValueError`. No worker starts; only the
  result's `error` field says the task expired.
- The guest demo tests use a deterministic local provider. With a real model,
  whether a scenario other than `alarm` sets off the alarm depends on what the
  model tries.
- No Landlock rules are applied. Filesystem confinement comes from the
  Bubblewrap mount namespace, the FUSE mediator and AppArmor. Landlock, the
  Linux counterpart to OpenBSD's `unveil`, could add an independent
  kernel-enforced path restriction, but it is not implemented.

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
- macOS and Windows support moved to a follow-up plan by the user's scope
  decision of 17 November 2026; see [the completion plan](PLAN_COMPLETION.md).
- **Other Linux environments:** no acceptance results establish support. The
  connected runtime's startup check tests for Linux, `bwrap`, `aa-exec`, FUSE,
  the helper, the exact loaded AppArmor profile and OpenCode 2.2.5. It does not
  check the distribution or CPU architecture, so a host that provides all of
  these, such as another AppArmor distribution, can launch a worker without an
  accepted result. Hosts without AppArmor, such as SELinux-based Fedora and
  RHEL, are blocked. Do not infer support from an executable, kernel feature or
  container being present; supporting another host means running the full
  suite there and recording the evidence.

The earlier decision to defer platform testing is historical. Ubuntu testing
resumed on 13 November 2026; the successful private-workspace mode does not
resolve the separate live-project and model-connected contracts. See the
[enforcement overview](ENFORCEMENT.md) and [review history](REVIEW.md).
