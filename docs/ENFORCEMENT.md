# Enforcement status

Planner has two execution interfaces with different contracts:

| Interface | Current behavior |
| --- | --- |
| `LinuxSandbox` / `python3 -m planner.native.linux_sandbox` | Runs offline commands in a private copy under Bubblewrap, AppArmor and seccomp. Edits stay in the copy. |
| `Backend.check()` / `Backend.run()` | The live-project gate remains closed on every platform. `run()` raises `ProtectionUnavailable` before spawning the command. |

A successful policy decision authorizes a proposed operation; it does not
confine arbitrary subprocesses. There is no unconfined fallback. Evaluators
using `Backend` remain gated too.

## Tested Ubuntu mode

The recorded environment is Ubuntu 24.04.3 x86-64, kernel 6.8.0-137-generic.
The final contract run passes **23 checks**: twenty real-process probes and
three snapshot API tests. A separate administrative probe verifies that a
complain-mode profile cannot start the supplied command.

The runner copies permitted regular inputs rather than mounting original
project inodes. AppArmor enforces dynamic read/write denials; seccomp blocks
links, `io_uring` and process-memory syscalls. A PID namespace contains detached
descendants. The operator command checks current task/worker authority while
running. Timeout, revocation and launcher-loss probes exercise actual cleanup.

This mode is offline and uses an existing directory tree. It rejects external
inputs and unsupported scope syntax, prevents directory changes and makes
read-denied paths write-denied too. It does not supply model-provider access,
model provenance integration, durable attempt records or automatic patch merge.
See [setup, commands and the complete restrictions](LINUX_SANDBOX.md).

[Linux evidence](../evidence/linux-private-workspace.json) retains failed attempts,
per-test outcomes, source hashes and package versions. The
[review log](REVIEW.md#ubuntu-private-workspace-validation-13-november-2026)
records the broader validation. Repeat acceptance after relevant changes; results
on this guest do not establish support for every Linux environment.

## macOS diagnostic

The disposable `sandbox-exec` prototype is diagnostic-only. On macOS 27.0.1,
build 26A434, Darwin 27.0.0 ARM64, its final attempt passes **12 of 14** probes.
Two required failures keep it disabled:

- Another process can create an allowed-name hardlink to a denied file after
  startup, exposing dummy secret bytes to the worker.
- A detached descendant can retain its sandbox authority and perform an allowed
  write after its owner exits.

An earlier denied-file rename also exposed bytes; the diagnostic subsequently
blocked that rename by denying unlink on read-denied names. This stronger rule
is recorded as a prototype restriction. The private/deprecated Seatbelt
interface is not a stable cross-release contract.

The runner owns its disposable dummy project; it accepts no caller project or
arbitrary task grant. Run it only in an environment that permits nested macOS
sandbox creation:

```sh
python3 -m planner.native.macos_contract --output evidence/macos-candidate.json
```

[macOS evidence](../evidence/macos-candidate.json) and the [review log](REVIEW.md)
preserve the attempts. These failures describe this candidate, rather than a
universal limitation of macOS.

## Remaining acceptance

Windows has no implemented or validated backend here. The full OpenCode launcher,
live-project policy semantics, provider access, storage isolation and matched
agent comparisons still need integration and independent acceptance. The
[remaining work](KNOWN_LIMITATIONS.md) distinguishes these gaps from the tested
Ubuntu offline mode. Initial connection failures and exploratory diagnostics
remain historical context in the review records; they are not the current
platform status.
