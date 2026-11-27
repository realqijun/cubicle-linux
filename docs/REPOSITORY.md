# Standalone repository

This directory is the planner repository root. Run `python3 -m planner`, tests,
and typechecking here. The code is in `planner/`; documentation, sanitized
evidence, evaluation fixtures and the Ubuntu scripts are in `docs/`,
`evidence/`, `artifact/` and `scripts/`.
No import or symlink depends on the source workspace's parent directory.

The repository has its own `.git` directory and `main` branch. It is not a Git
submodule or worktree and uses no shared object store. Its GitHub remote is
[realqijun/cubicle-linux](https://github.com/realqijun/cubicle-linux).
The original source workspace ignores this directory instead of tracking a second copy.

## Why `planner/planner`?

The outer directory is the Git project; the inner directory is the importable
Python package. From the project root, `python3 -m planner` finds the inner
package's `__main__.py`.

```text
planner/               Git repository root
  README.md
  pyproject.toml
  docs/                Guides, architecture and the course report PDF
  evidence/            Sanitized recorded outcomes and source hashes
  artifact/            Pinned evaluation inputs
  scripts/             Ubuntu install and guided demo
  planner/             Python package
    __main__.py        Control-plane CLI
    native/            Ubuntu runner and platform diagnostics
    tests/             Public-boundary tests
```

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
The one exception is the Qwen 3.5 9B documentation-injection runs cited in the course report.
They are kept with their raw sessions in `evidence/qwen35-documentation-injection/`.
The intentionally failing workload is an evaluation input, not the planner test
suite. See the [fixture README](../artifact/README.md).

## Validation records

The Ubuntu [offline sandbox](LINUX_SANDBOX.md) and the model-connected
[runtime](CONNECTED_RUNTIME.md) have recorded acceptance on Ubuntu 24.04.3
x86-64; the generic live-project backend remains gated. See
[known limitations](KNOWN_LIMITATIONS.md). Historical evidence is preserved with
its original source hashes and commit references, rather than rewritten to
describe newer code. Repository extraction does not strengthen security claims.
