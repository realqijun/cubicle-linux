# Quick start on a fresh Linux machine

Tested target: **Ubuntu 24.04 x86-64**, as a normal user with sudo. Other
distributions and ARM are untested; the installer refuses them unless you set
`PLANNER_ALLOW_UNTESTED=1`. Containers usually lack `/dev/fuse` and AppArmor,
so use a VM or bare metal.

## 1. Install (a few minutes)

```sh
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/realqijun/cubicle-linux.git
cd cubicle-linux
scripts/install-ubuntu.sh      # asks for your sudo password
```

The installer:

- installs Python 3, Bubblewrap, AppArmor tools, FUSE 3, libseccomp, gcc and curl;
- downloads OpenCode 2.2.5 and verifies its SHA-256;
- builds the FUSE helper;
- installs the worker's AppArmor profile as `/etc/apparmor.d/planner-connected`;
- finishes by running the same startup check that every run uses, and stops if
  anything is missing.

If Ubuntu blocks Bubblewrap from creating user namespaces, the installer also
adds `/etc/apparmor.d/planner-bwrap`. That profile lets **any** local user run
Bubblewrap with user namespaces, which relaxes an Ubuntu hardening default for
the whole machine. To remove it:

```sh
sudo apparmor_parser -R /etc/apparmor.d/planner-bwrap
sudo rm /etc/apparmor.d/planner-bwrap
```

Everything else goes in `~/.local/share/planner`. To use another location, set
`PLANNER_HOME` for both the installer and the demo.

Re-run `scripts/install-ubuntu.sh` after pulling new code. The worker profile's
name is derived from the runtime source files, so it changes when they change.

## 2. Choose a model provider

By default the demo uses `qwen3.8:27b` on the NUS SoC LaaS endpoint
(`https://soclaas-api.comp.nus.edu.sg/v1`). That needs a SoC LaaS API key:

```sh
read -rs SOCLAAS_API_KEY && export SOCLAAS_API_KEY   # paste the key; it is not echoed
```

Any other OpenAI-compatible provider works if it supports streaming chat
completions with tool calls:

```sh
export PLANNER_PROVIDER_URL=https://api.example.com/v1
export PLANNER_MODEL=example/model-name     # any label, a slash, then the provider's model ID
export PLANNER_API_KEY_ENV=EXAMPLE_API_KEY  # the name of the variable holding the key
read -rs EXAMPLE_API_KEY && export EXAMPLE_API_KEY
```

If you skip this, the demo asks for the key when it needs one.
The key is passed to the supervisor by variable name. It stays outside the jail
and never appears in command arguments or records.

## 3. Run the demo

```sh
scripts/demo.sh
```

Pick a scenario from the menu, check the worker's permissions, and approve them.
The demo asks for your API key if it is not set, and offers to run the installer if the runtime is missing.

To see the alarm straight away, pick `alarm`.
It needs no model or API key: a scripted worker follows a planted checklist that tells it to print `.env` and edit the tests.
Only the worker's choices are scripted; the sandbox, the supervisor and the alerts are real.

The other scenarios use your model, so whether the alarm goes off depends on what the model tries.
`normal` and `benign-quotation` are controls, where no alarm is the right result.

While the worker runs, each blocked attempt prints a red `BLOCKED` line.
The first in a burst also shows a warning sign and rings the terminal bell, which over SSH plays on your own computer.
Afterwards the demo shows what changed, how many attempts were blocked, and the diff.

To see what happened in a run, including every file access the supervisor allowed or blocked:

```sh
scripts/demo.sh log            # the latest run
scripts/demo.sh log alarm      # the latest run of one scenario
```

Other forms:

```sh
scripts/demo.sh alarm          # run one scenario without the menu
scripts/demo.sh all            # run every scenario, one after another
scripts/demo.sh alarm --yes    # approve automatically, with no prompts
scripts/demo.sh list           # list the scenarios
```

Runs are saved under `~/.local/share/planner/demo-*`.
Delete them with `rm -rf ~/.local/share/planner/demo-*`.

## 4. Run it on your own project

```sh
scripts/run.sh ~/code/myapp "Fix the failing test in tests/test_parser.py"
```

Commit your work first: unless you pass `--review`, approved changes are written straight into the project.

The script walks you through one task:

1. It asks for your API key if it is not set, as the demo does.
2. It shows the worker's permissions from the built-in `source` template and asks `Approve? [y/N]`.
   Declining cancels the task and changes nothing.
3. The worker runs on a private copy of the project, and each blocked attempt prints a `BLOCKED` line.
   Ctrl-C stops the run; wait while the planner stops the worker.
   Afterwards you see what changed, how many attempts were blocked, and the diff.
4. Each blocked attempt becomes an access request, and the script asks about each one.
   An approved request lets the worker do exactly that once, in the next run; the others are refused.
   Approving a read of a protected file such as `.env` sends its contents to the model provider, so you must also type `yes`.
5. If you approved any, it offers to run again.
   Each run starts a fresh worker that does not remember the previous one.
6. If the worker keeps repeating the same blocked operation, the supervisor suspends it.
   The script says so and offers to resume it and run again.

Options:

```sh
scripts/run.sh PROJECT "TASK" --plan     # the model proposes narrower permissions instead of the template
scripts/run.sh PROJECT "TASK" --review   # hold the changes and ask before applying them
scripts/run.sh PROJECT "TASK" --yes      # approve the permissions without asking
```

`--yes` never approves access requests, sensitive reads, held changes or a resume; those are always asked.

To see what happened, including every file access the supervisor allowed or blocked and the full diff:

```sh
scripts/run.sh log ~/code/myapp    # the latest run on this project
scripts/run.sh log                 # the latest run on any project
```

Records stay outside your project, in `~/.local/share/planner/projects/NAME-ID/`.
Each run keeps its console output, `report.html`, `audit.json` and copies of the files the worker could change, for the diff.
The script refuses a project folder that contains these records, such as your home folder.

The individual commands it runs are in [the connected runtime guide](CONNECTED_RUNTIME.md).
The installed paths are:

```sh
--opencode ~/.local/share/planner/opencode-2.2.5/opencode
--fuse-helper ~/.local/share/planner/fuse-bridge
```

## Troubleshooting

| Message | Fix |
| --- | --- |
| `The runtime is not ready` or `outcome: blocked` | Re-run `scripts/install-ubuntu.sh`. The code probably changed since installation, or a profile was removed. |
| `Runtime not found in …` | Run the installer first, with the same `PLANNER_HOME`. |
| `The model made no changes and attempted nothing` | Check `PLANNER_PROVIDER_URL`, `PLANNER_MODEL` and the key. The provider may have rejected the requests; the supervisor does not yet report provider errors. |
| `Bubblewrap still cannot create namespaces` | The host blocks user namespaces. Check `sysctl kernel.apparmor_restrict_unprivileged_userns` and [the sandbox guide](LINUX_SANDBOX.md). |
| `FUSE device unavailable` | `/dev/fuse` is missing (common in containers). Use a VM or bare metal. |
| `BLOCKED: …` | The supervisor refused to start without full protection. Read the reason; it never falls back to weaker execution. |
