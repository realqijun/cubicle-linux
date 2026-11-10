# OS enforcement status

Further platform acceptance is deferred to Ubuntu by user request. Continue the
remaining implementation using the [known-limitations and retest plan](KNOWN_LIMITATIONS.md).
No additional local sandbox experiments are required for that work.

The launch gate is implemented. Production OS confinement is **not validated**
on any platform. A real macOS candidate has been built and tested locally, and
fails required contract cases described below. `Backend.check(policy, grant)` returns a structured blocking
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
| Ubuntu SSH forwarding at localhost:2222 | Initially refused; after the user started the VM, key login failed for both documented and host usernames | The user then selected host-only testing; no Linux enforcement tests ran. |
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

## Local macOS candidate and actual evidence

At the user's request, further testing used this host only; no VM was used.
`planner/native/macos_contract.py` contains the disposable Seatbelt prototype and
the fixed diagnostic runner. The fixture creates its own temporary project. It
accepts no actual task policy, grant, project, executable or runtime mounts.
Production `Backend.run` never calls it. The prototype is not a supported backend.

The candidate uses Python-generated SBPL regex rules for component `*`, `?` and
`**` globs. Bracket globs are refused. It continuously applies name denials,
limits read/write resources, denies new hardlinks, closes inherited file
descriptors, and denies Mach lookup, network connections, other-process signals,
other-process information, Apple Events and authorization-right requests.
Runtime reads are explicit system directories plus the executing Python prefix
and its Homebrew gettext library if present. Runtime aliases and canonical paths
are both included. A startup check refuses preexisting multiply linked project
files. That scan is supplementary and cannot establish continuous inode safety.

The initial deny-default profile aborted even on `/usr/bin/true`. An inverted
filesystem allowlist worked after including the root and device-directory
runtime reads. This is an experiment with a private/deprecated interface, not a
stable cross-release security contract.

Run the actual local diagnostics in an environment allowed to create a nested
Seatbelt sandbox:

```sh
python3 -m planner.native.macos_contract --output planner/evidence/macos-candidate.json
```

The runner enables only the fixed dummy-data tests, appends sanitized evidence,
and returns nonzero when the candidate violates its contract. No raw tool output,
secret bytes or traceback messages are saved. Ordinary unit discovery skips the
host probes because the desktop workspace sandbox prevents nested confinement.
Those skips are separate from the explicitly executed host diagnostics.

The actual host was macOS 27.0.1, build 26A434, Darwin 27.0.0, ARM64. The final
recorded attempt ran fourteen tests: twelve passed and two failed. The evidence
file preserves earlier unsuccessful attempts too.

Passing cases covered legitimate reads/writes, protected writes/rename/unlink/
truncation, existing sensitive paths, traversal and symlinks, case aliases,
newly created matching sensitive files, permitted source replacement, children
and grandchildren, inherited descriptor closure, parent-process and Unix control
socket denial, sensitive symlink names, worker hardlink creation denial, and
startup refusal of preexisting hardlinks.

An earlier attempt exposed another dummy secret by renaming `src/.env.movable`
to `src/visible` and reading it. The final candidate conservatively denies
`file-write-unlink` on read-denied names, so that rename is now blocked. This
extra restriction is stricter than the independent read/write scopes in the
policy; it is a diagnostic safety restriction, not a change to production policy
semantics. The unsuccessful earlier attempt remains in the evidence file.

Required failures remain:

1. An independent process can create a hardlink to `.env` under an allowed name
   after the startup scan. The worker reads the dummy bytes through that alias.
2. A worker forks a child that creates a new session and closes inherited output
   pipes. After its owner exits, the detached child retains its sandbox grant and
   performs an authorized write. Process-tree expiry/cleanup is not established.

These failures prevent acceptance and keep normal execution blocked. Startup
scans, successful individual path tests and an interface that launches correctly
must not be substituted for complete continuous enforcement and lifecycle tests.
