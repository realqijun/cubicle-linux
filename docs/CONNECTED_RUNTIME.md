# Connected Linux runtime

The connected runtime runs a model-connected OpenCode 2.2.5 worker against a
private copy of the project, then applies its authorized edits to the original.
It is accepted on Ubuntu 24.04.3 x86-64 only. macOS and Windows are blocked.
Other Linux hosts are untested: the startup check verifies prerequisites, not
the distribution or architecture. See
[known limitations](KNOWN_LIMITATIONS.md#other-execution-modes-and-platforms).

## How a run works

1. The operator approves a policy revision and an exact worker grant. `plan` can
   propose both from the user's request and trusted templates; it never approves.
2. `run` records an attempt and checks the runtime (`runtime_status`): bwrap,
   aa-exec, FUSE, the FUSE helper, the pinned OpenCode release and the exact
   `planner-connected-*` AppArmor profile. Any gap blocks before a provider
   request.
3. Approved exceptions are reserved for this attempt. External inputs the user
   approved are copied after their identity is rechecked.
4. The project is copied into run storage and served through a FUSE mediator.
   Every open, read, write, rename, unlink, mkdir, rmdir and truncate is checked
   against the worker's grant by the Python policy. Only the mount enters the jail.
5. OpenCode starts with no task inside bubblewrap (user, PID, IPC, net, UTS
   namespaces), the candidate AppArmor profile and a seccomp filter. Its plugin
   must register the authenticated hook inventory before the prompt is sent.
   Provider and broker traffic use fixed Unix relays; the real credential stays
   with the operator, and the worker receives a short-lived relay token.
6. The runner polls authority. Cancellation, revocation, expiry or a lost
   authority check kills the whole PID namespace. Owner death does the same
   (`--die-with-parent`); the FUSE helper exits and unmounts on owner death.
7. After verified cleanup the runner records the outcome. In `auto` mode it
   applies conflict-free authorized changes with the checked changeset; in
   `review` mode it retains them until `apply ATTEMPT`. Denied operations become
   inert pending exception requests. The private copy is deleted once final.

## Setup

Run these on the Ubuntu host as the operator. `sudo` is needed only to load
AppArmor profiles; they last until reboot.

```sh
# FUSE helper (headers from the libfuse3-dev package; runtime library from fuse3)
gcc -Wall -Wextra -Werror -O2 -D_FILE_OFFSET_BITS=64 -I/usr/include/fuse3 \
    planner/native/fuse_bridge.c -lfuse3 -o /opt/planner/fuse-bridge

# Connected-runtime profile; its name changes when runtime sources change
python3 -c 'from planner.native.confined_opencode import profile_text; print(profile_text(), end="")' \
    > /tmp/planner-connected.apparmor
sudo apparmor_parser -r /tmp/planner-connected.apparmor

# Optional: the confined evaluation oracle profile
python3 -c 'from planner.evaluation import oracle_profile; print(oracle_profile(), end="")' \
    > /tmp/planner-oracle.apparmor
sudo apparmor_parser -r /tmp/planner-oracle.apparmor
```

Use the official OpenCode 2.2.5 Linux binary. Keep the state database, run
storage and helper outside every project.

## Operator workflow

```sh
p="python3 -m planner --state /srv/planner/state.sqlite"
rt="--opencode /opt/opencode-2.2.5 --fuse-helper /opt/planner/fuse-bridge --runs /srv/planner/runs"

$p plan /work/project --prompt "Fix refund handling" --model soclaas/qwen3.8:27b \
   --provider-url https://soclaas-api.comp.nus.edu.sg/v1 --api-key-env SOCLAAS_API_KEY
$p review TASK
$p approve-task TASK --revision 2 --assign worker      # exact reviewed scopes
$p run TASK worker --prompt "Fix refund handling" --model soclaas/qwen3.8:27b \
   --provider-url https://soclaas-api.comp.nus.edu.sg/v1 --api-key-env SOCLAAS_API_KEY $rt
$p applications TASK
```

- `--mode review` retains edits; `apply ATTEMPT` applies them later with the
  same checks and never twice.
- `--alert` prints a `BLOCKED` warning on stderr for each refusal recorded for
  the worker while it runs. The first refusal in a three-second burst also
  prints an ASCII warning sign, rings the terminal bell and, in a local desktop
  session, plays the desktop warning sound and shows a notification. Alerts
  only display refusals that were already enforced and recorded. Worker-chosen
  names are escaped before display, and sound players receive no provider
  credential.
- `exception ID`, `approve-exception ID` and `refuse-exception ID` answer the
  requests a run created. An approval takes effect for the next replacement
  worker, never the running one. Sensitive reads need
  `--confirm-sensitive-provider-disclosure`. Refusing a held approval stops any
  attempt that could use it, including one prepared but not yet started. A
  staged write stays withdrawable (state `withdrawn`) until its application
  starts; a spent read cannot be undone.
- `request-exception TASK WORKER read /abs/file --tool shell` requests an external
  input listed in the policy's `[external] read`. The worker sees only that file,
  at `/workspace/.planner-inputs/NAME`.
- After a crash, `recover TASK WORKER $rt` verifies that the recorded processes,
  the worker PID namespace and the mount are gone before reconciling. It never
  replays an edit and never revives expired or revoked authority.
  `--operator-confirms-cleanup` records your attestation when evidence is missing.

## Single-use approvals

A once-only approval is reserved for one attempt before its worker starts. The
FUSE mediator permits exactly one open of that file; later opens fail. If the
attempt was dispatched the approval is spent, used or not. For a write, the
changeset consumes the approval when it applies that exact staged file, after
rechecking expiry, revision, attempt and the file's identity. An interrupted
application leaves it consumed. Task-lifetime approvals stay usable until the
task ends. Approved sensitive reads mark later tool output sensitive.

## Application contract

Regular files and directories are supported. Additions, modifications,
deletions, directory creation and removal apply only within current write
authority and only if the original has not changed since preparation. Moves
appear as removal plus addition, so both ends are authorized. A directory is
removed only if the original holds nothing the worker was not shown. New files
get mode 0600 and new directories 0700, owned by the operator. Conflicts,
unauthorized changes, symlinks, hardlinks, special files, ownership and extended
attributes block the whole changeset before any write. Each file replacement is
atomic; the journal records progress, and an interrupted application is
reported, not replayed. Keep the original quiescent while applying.

## Tested contract

[Evidence](../evidence/completion-progress.json) records source hashes and every
attempt. The real-runtime tests are `test_launcher`, `test_confined_opencode`,
`test_mediatedfs`, `test_changeset` and `test_comparison`; enable them with
`PLANNER_RUN_CONFINED_OPENCODE=1`, `PLANNER_RUN_FUSE_PROBES=1`,
`PLANNER_RUN_LINUX_PROBES=1`, `PLANNER_OPENCODE_BINARY` and
`PLANNER_FUSE_BRIDGE`. They cover permitted and denied reads and writes by tools
and child processes, directory operations, conflicts, review mode, replay,
cancellation, revocation, expiry, owner death and recovery, single-use and
sensitive exceptions, external inputs, delegation, repository configuration
isolation and a real LaaS run.

## Limits

- The policy is enforced by the mediator for paths inside the mount. The jail
  also sees `/usr` read-only, tmpfs `/tmp` and `/state`, `/proc` with process
  memory and environment denied, and the relay sockets.
- `/proc` is required by OpenCode's runtime; the candidate profile denies
  environment, memory, pagemap and map_files reads.
- The mediator is single-threaded Python; large builds are slow.
- One worker per attempt. Native OpenCode subagents are unavailable; delegated
  children are separate supervisor-launched workers.
- Owner death leaves an `interrupted` record until `recover` runs.
- No Landlock rules are applied; see [known limitations](KNOWN_LIMITATIONS.md#connected-runtime).
- Model output quality is outside the guarantee; authorized edits can be wrong.
