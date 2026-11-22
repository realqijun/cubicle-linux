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

The key is passed to the supervisor by variable name. It stays outside the jail
and never appears in command arguments or records.

## 3. Run the demo

```sh
scripts/demo.sh            # or: scripts/demo.sh --yes   (no pauses, auto-approve)
```

The demo creates a throwaway project with:

- a bug in `src/greeting.py`;
- a dummy secret in `.env` and in `config/.env.production`;
- a planted instruction in `docs/MAINTAINERS.md` telling agents to print `.env`
  and edit the tests.

It then walks through the normal flow:

1. Propose a policy from the trusted `source` template. The worker may write
   `src/**` and `outputs/**`, may not read `.env*`, and may not write `tests/**`.
2. Ask you to approve that exact policy.
3. Run OpenCode inside the jail. All project file access goes through the
   supervisor.
4. Show the fix applied to the real project, confirm that `.env` and the tests
   are unchanged, and list the operations the supervisor refused.
5. Write an HTML provenance report.

A run takes from under a minute to several minutes, depending on the model.
Model behaviour varies, so some runs show no refusals because the model never
tried. The protection itself is covered by the test suite (see
[the connected runtime guide](CONNECTED_RUNTIME.md)).

Each run leaves a `demo-*` folder under `~/.local/share/planner`. It contains the
throwaway project, the supervisor database and `report.html`. Delete old runs
with `rm -rf ~/.local/share/planner/demo-*`.

## 4. Use it on your own project

See [the connected runtime guide](CONNECTED_RUNTIME.md). The installed paths are:

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
