# OS enforcement status

The launch gate is implemented. OS confinement is **not implemented or validated**
on any platform. `Backend.check(policy, grant)` returns a structured blocking
status; `Backend.run(...)` raises `ProtectionUnavailable` before spawning the
command. This also applies to evaluators that execute generated source. There is
no permissive development mode, environment override, startup-scan fallback, or
claim that refusing all execution completes the project.

The policy module provides deterministic authorization decisions. It cannot
enforce arbitrary subprocess filesystem access by itself. Callers must not use
an allowed decision as permission to launch an unconfined process.

## Checked environment

Diagnostics on 10 November 2026 found:

| Resource | Observed result | Meaning |
| --- | --- | --- |
| Host | Darwin 27.0.0, ARM64 | Only this host was available for inspection. |
| Ubuntu SSH forwarding at localhost:2222 | Connection refused | No Linux kernel enforcement tests ran. |
| UTM `utmctl list` | OSStatus -1743; Automation authorization unavailable | VM inventory could not be established. |
| macOS `sandbox-exec` | Present; nested invocation denied; escalated allow-default/read-only profile ran `/usr/bin/true` | The executable exists and one permissive diagnostic ran. This does not validate the policy contract. |
| macOS deny-default diagnostic | `/usr/bin/true` aborted, exit 134 | Even basic legitimate execution was not established for this candidate profile. |
| Windows | No execution environment available | No Windows enforcement tests ran. |

These are negative or inconclusive diagnostics, not security acceptance results.
No secret files or provider credentials were used.

## Backend selection findings

Linux [Landlock](https://docs.kernel.org/userspace-api/landlock.html) grants rights
to filesystem hierarchies. A broad tree allowance does not directly express a
continuous subtraction of names such as `**/.env*`. Existing file descriptors,
renames, hard links, and descendants need explicit contract tests. A startup
enumeration of files must not be advertised as continuous protection.

[Bubblewrap](https://github.com/containers/bubblewrap) constructs a mount namespace;
its security model depends on its arguments. Binding a project tree and masking
the sensitive files that exist at startup does not faithfully implement dynamic
deny patterns for newly created names. Candidate use needs a design that addresses
this gap, process-control routes, runtime mounts, and inherited descriptors.

macOS `sandbox-exec` and the profiles shipped under `/usr/share/sandbox` are
private/deprecated interfaces. Apple's shipped profiles explicitly warn that the
rules are a private interface subject to change. A future candidate must state
the exact supported OS build, validate the complete contract, and revalidate on
updates. Its presence cannot imply a stable cross-release security contract.
Apple's documented [App Sandbox](https://developer.apple.com/documentation/security/app-sandbox)
is another mechanism to investigate, not an implementation supplied here.

Windows [AppContainer](https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-for-legacy-applications-)
can constrain resources, but resource ACLs, inherited handles, path aliases, and
worker lifecycle controls must actually implement the reviewed policy. A reduced
token or process container by itself is not sufficient evidence.

## Acceptance work still required

Before enabling `run`, implement one backend and run independent real-process
tests on its target OS. Include positive controls for permitted reads/writes;
denied dummy-secret reads through tools, scripts, children and grandchildren;
traversal and symlinks; hard-link aliases; replacement and newly matching files;
rename, unlink, truncation; inherited descriptors/handles; protected supervisor
state and control channels; and supervisor failure cleanup. Repeat on each OS
before claiming support. Kernel enforcement tests must record actual access
effects, including confidential reads and transient writes.

The current test proves only that the gate blocks a command before its visible
effect. The same fixture command runs as a positive control outside the gate.
It does not establish that any OS filesystem confinement mechanism works.
