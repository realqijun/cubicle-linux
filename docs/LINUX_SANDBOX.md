# Ubuntu private-workspace sandbox

This execution mode enforces file restrictions on Ubuntu using Bubblewrap,
AppArmor and seccomp. It runs a command in a private copy of approved inputs.
The original project, home directory, supervisor database and host sockets are
absent from its filesystem. Edits stay in the copy for human review.

It is an offline process runner, separate from the still-gated live-project
backend and OpenCode/model-provider launcher. It does not establish complete
autonomous-agent acceptance or support on every Linux distribution.

## How it protects files

1. Copy regular files permitted by the worker's read grant, excluding read-denied
   names. Omit symlinks, multiply linked files and special files. Copy bytes;
   never bind-mount original project inodes.
2. Bubblewrap provides separate mount, PID, IPC, network, user and UTS namespaces.
   Only the private workspace is mounted writable. `/usr` is the explicit
   read-only system runtime; `/tmp` and `/dev` are private. No home or `/etc` is
   mounted, no provider credentials or ambient environment are forwarded, and
   inherited descriptors are closed.
3. An enforcing AppArmor profile grants the worker's read/write paths and denies
   protected patterns continuously, including files inserted after launch.
   The trusted bootstrap verifies its actual kernel profile is in **enforce**
   mode before executing the command. A missing or complain-mode profile cannot
   start the command. The profile declares AppArmor ABI 4.0 and explicitly denies
   networking; relying on a legacy default ABI can silently omit newer mediation.
4. Seccomp denies hardlinks, symlinks, `io_uring` and cross-process memory/ptrace syscalls.
   AppArmor also denies writing read-denied names, preventing a secret from
   being renamed into a readable name. Workspace directory creation, removal
   and renaming are denied too, so an ancestor rename cannot change a protected
   file's path. File replacement in existing directories remains available.
   These restrictions are intentionally stricter than the general policy interface.
5. A PID namespace contains detached descendants. Owner exit, launcher death,
   timeout or failed authority checks stop execution. The operator CLI checks
   task/worker authority periodically; revocation is detected within the polling
   interval plus scheduling delay, rather than atomically at the database edit.

An outside process adding a secret hardlink to the original project cannot
inject it into the already-created copy. The source tree must be quiescent while
the snapshot is prepared, and the run directory must remain trusted. This does
not defend against a hostile host administrator or another malicious process
with the supervisor's UID that can tamper with the supervisor or private copy.
Directory prefixes compatible with read/write scopes are copied for navigation;
wildcards can expose an empty directory skeleton even when no contained file is
copied. Filename and directory-name confidentiality is not promised.

## Setup

Run in Ubuntu, from the repository root, with Python 3.11+:

```sh
sudo apt-get update
sudo apt-get install bubblewrap apparmor libseccomp2
```

First test namespace creation as the ordinary user:

```sh
bwrap --unshare-user --unshare-pid --ro-bind / / /usr/bin/true
```

If Ubuntu's AppArmor user-namespace restriction blocks Bubblewrap, inspect any
existing `/usr/bin/bwrap` profile before changing it. On the tested guest there
was none; this narrowly scoped profile allowed namespace creation without
disabling the global restriction:

```text
abi <abi/4.0>,
include <tunables/global>
/usr/bin/bwrap flags=(unconfined) {
  userns,
}
```

Save that as `/etc/apparmor.d/planner-bwrap` and load it with
`sudo apparmor_parser -r /etc/apparmor.d/planner-bwrap`. This profile permits
Bubblewrap's setup; the worker subsequently enters its separate enforcing
profile. Do not replace another application's existing profile blindly.

## Run an approved worker

Start with an approved supervisor task, such as the `source` template from the
[quickstart](../README.md). Choose an explicit worker grant that fits its policy.
Keep state and run output outside the source project. Replace `TASK` below with
your approved task ID; use the same database as the quickstart.

```sh
state=/tmp/planner-control/control.sqlite
run=/tmp/planner-control/first-run

# Approve a worker with the file scopes and shell authority used in this example.
python3 -m planner --state "$state" assign TASK offline-worker \
  --read 'README.md' --read 'src/**' --write 'src/**' --tool shell

# Generate the exact approved policy for operator inspection and installation.
python3 -m planner.native.linux_sandbox --state "$state" --task TASK \
  --worker offline-worker --output "$run" profile > /tmp/planner-worker.apparmor
sudo apparmor_parser -r /tmp/planner-worker.apparmor

# Run offline; edits appear under "$run/workspace".
python3 -m planner.native.linux_sandbox --state "$state" --task TASK \
  --worker offline-worker --output "$run" --timeout 60 --poll-interval 0.1 \
  run -- /usr/bin/python3 -c 'from pathlib import Path; print(Path("README.md").read_text())'
```

The run directory must not already exist. Review the resulting edits before
applying selected changes to the original project; no automatic merge is
implemented. Profile names bind both the code version and policy scopes, so
changing either requires generating and loading the new profile. Profiles are
loaded into the current kernel; reload after reboot or install approved profiles
through normal system administration. Remove a loaded profile with
`sudo apparmor_parser -R /tmp/planner-worker.apparmor` when no worker needs it.

## Repeat the contract tests

```sh
python3 -m planner.native.linux_contract --print-profile > /tmp/planner-test.apparmor
sudo apparmor_parser -r /tmp/planner-test.apparmor
python3 -W error::ResourceWarning -m planner.native.linux_contract \
  --output evidence/linux-private-workspace.json
```

The runner preserves attempts with source hashes, host/package versions and
per-test outcomes. It saves no raw command output, dummy secret bytes or failure
tracebacks. Ordinary test discovery skips real Linux probes unless
`PLANNER_RUN_LINUX_PROBES=1` is set; skipped tests provide no confinement evidence.
`PLANNER_PROBE_TIMEOUT` configures the real-process test deadline (default 20
seconds); the timeout cleanup probe uses half that value after verifying that
its detached worker reached a checkpoint.

## Current limits

- Tested on the supplied Ubuntu 24.04.3 x86-64 VM, kernel 6.8.0-137-generic.
  Repeat the probes after kernel, AppArmor, Bubblewrap or implementation changes.
- Scopes support component `*`, `?` and `**` with ASCII letters, digits, `_`, `.`,
  `/` and `-`. Unsupported syntax, external inputs and missing `shell` authority
  are refused. Linux names remain case-sensitive.
- Symlinks, hardlinks, `io_uring` and workspace directory changes are unavailable;
  read-denied paths are also write-denied. Tools can edit and replace files in
  existing directories. Additional operations need a reviewed future design.
- `/usr` is trusted runtime access, outside project path rules. Review installed
  runtime contents. Extra mounts, host networking, credential injection and
  provider access are not supported.
- No automatic patch application, model-input/provenance integration, active
  attempt records, or CPU/memory/output quotas are supplied by this mode.
- The Python API takes a trusted policy/grant; its optional authority callback
  belongs to its trusted caller. The operator CLI always supplies the supervisor
  check. This runner does not turn the existing live-project preflight green.

References: [Bubblewrap security model](https://github.com/containers/bubblewrap),
[Ubuntu AppArmor](https://documentation.ubuntu.com/security/security-features/privilege-restriction/apparmor/),
[AppArmor profile syntax](https://manpages.ubuntu.com/manpages/jammy/man5/apparmor.d.5.html).
