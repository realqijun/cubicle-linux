# Working in this repository

- Scope filesystem searches to this repository and explicitly named temporary
  test directories or system/SDK paths needed by the task. Read ancestor
  instruction files by exact path. Never recursively scan `/` or the user's
  home directory to discover instructions, tools or configuration.
- Use a configuration file the user identifies explicitly. Keep credential
  values out of tool output, source files and evidence.
- Before implementation, read [the original requirements](docs/PLAN_PLANNER.md)
  and [the authorized completion plan](docs/PLAN_COMPLETION.md). Preserve the
  user's latest scope decisions and the original security invariants.
- Test actual filesystem effects and real harness behavior at public interfaces.
  Distinguish a tested component from an accepted connected runtime. Skips,
  prototype code and documented limitations do not complete requirements.
- Keep evidence with its source hashes and failed attempts. Open runtime gates
  only after their required contract passes; fix independent review findings
  before claiming completion.
- `scripts/install-ubuntu.sh` and `scripts/demo.sh` (guide: docs/QUICKSTART.md)
  install and demo the connected runtime on Ubuntu 24.04. Re-test both on the
  Ubuntu guest (SSH localhost:2222) after changing either script or the runtime.
