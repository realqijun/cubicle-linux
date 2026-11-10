# Standalone repository

This directory is the planner repository root. Run `python3 -m planner`, tests,
and typechecking here. The code is in `planner/`; documentation, sanitized
evidence, and evaluation fixtures are in `docs/`, `evidence/`, and `artifact/`.
No import or symlink depends on the source workspace's parent directory.

The repository has its own `.git` directory and `main` branch. It is not a Git
submodule or worktree, uses no shared object store, and has no configured remote.
The source workspace ignores this directory instead of tracking a second copy.

## Preserved history

The five planner commits were extracted with `git subtree split --prefix=planner`
then followed by a standalone-layout commit. Extraction rewrites commit IDs;
existing validation records and review notes retain their original references.
Use this mapping when inspecting those references in the extracted history:

| Source workspace | Extracted planner commit |
| --- | --- |
| `25570fb` | `8dc5e2d` |
| `2fc47ee` | `e6c889c` |
| `aea4301` | `484bfcd` |
| `e4022d9` | `24d33ee` |
| `057f54c` | `7cf6571` |

The architecture page/assets and original planner plan were brought into
`docs/`. The source workspace's original plan is left in place too.

## Evaluation fixtures

`artifact/` contains only tracked inputs needed for preparation: the workload,
scenarios, task, preparation script, and pinned upstream skills with license
and origin metadata. Source files were copied from source commit `057f54c`;
local runs, raw sessions, caches, and service configuration were excluded.
The intentionally failing workload is an evaluation input, not the planner test
suite. See the [fixture README](../artifact/README.md).

OS validation remains deferred to Ubuntu as described in
[known limitations](KNOWN_LIMITATIONS.md). Repository extraction does not change
or strengthen the existing security claims.
