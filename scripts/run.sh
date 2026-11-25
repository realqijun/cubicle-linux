#!/usr/bin/env bash
# Run the planner on your own project: approve the worker's permissions, watch it,
# then answer its access requests. See --help.
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
planner_home=${PLANNER_HOME:-$HOME/.local/share/planner}
model=${PLANNER_MODEL:-soclaas/qwen3.8:27b}
provider=${PLANNER_PROVIDER_URL:-https://soclaas-api.comp.nus.edu.sg/v1}
key_env=${PLANNER_API_KEY_ENV:-SOCLAAS_API_KEY}
opencode=$planner_home/opencode-2.2.5/opencode
helper=$planner_home/fuse-bridge

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
absolute() { if [[ "$1" == /* ]]; then printf '%s\n' "$1"; else printf '%s\n' "$PWD/$1"; fi; }
usage() {
  cat <<'EOF'
Usage: scripts/run.sh PROJECT "TASK" [--plan] [--review] [--yes]
       scripts/run.sh log [PROJECT]

A confined coding agent does TASK in the PROJECT folder. You approve its
permissions first, see each blocked attempt as it happens, and decide about
its access requests afterwards.
  --plan    let the model propose narrower permissions (default: the source template)
  --review  hold the changes until you approve them
  --yes     approve the permissions without asking; access requests are always asked
  log       show the latest run, or the latest run on PROJECT
EOF
}

if [[ "${1:-}" == log ]]; then
  shift
  (($# <= 1)) || fail "Usage: scripts/run.sh log [PROJECT]"
  target=()
  (($# == 0)) || target=("$(absolute "$1")")
  cd "$repo"
  exec python3 -m planner.guided log --home "$planner_home" ${target[@]+"${target[@]}"}
fi
yes=0
flags=()
positional=()
for argument in "$@"; do
  case "$argument" in
    --yes) yes=1; flags+=(--yes) ;;
    --plan|--review) flags+=("$argument") ;;
    --help|-h) usage; exit 0 ;;
    -*) usage >&2; fail "Unknown option: $argument" ;;
    *) positional+=("$argument") ;;
  esac
done
((${#positional[@]} == 2)) || { usage >&2; fail "Give a project folder and a task, e.g.: scripts/run.sh ~/code/app \"Fix the failing test\""; }
request=("$(absolute "${positional[0]}")" "${positional[1]}" ${flags[@]+"${flags[@]}"}
         --home "$planner_home" --model "$model" --provider-url "$provider" --api-key-env "$key_env"
         --opencode "$opencode" --fuse-helper "$helper")
cd "$repo"
python3 -m planner.guided check "${request[@]}"

if [[ ! -x "$opencode" || ! -x "$helper" ]]; then
  echo "The planner runtime is not installed in $planner_home."
  echo "scripts/install-ubuntu.sh installs system packages and may add a machine-wide AppArmor"
  echo "profile that lets any local user run Bubblewrap; see docs/QUICKSTART.md before agreeing."
  answer=n
  ((yes)) || read -rp "Run it now? It asks for your sudo password. [y/N] " answer || answer=n
  [[ "$answer" == [yY]* ]] || fail "Install it with scripts/install-ubuntu.sh, then run this again."
  scripts/install-ubuntu.sh
fi
prompted_key=
if [[ -z "${!key_env:-}" ]]; then
  if read -rsp "Paste your API key for $provider (it is not shown): " prompted_key; then echo; fi
  [[ -n "$prompted_key" ]] || fail "No API key. Set it first: read -rs $key_env && export $key_env"
fi
# Exported in this subshell only; never put on a command line.
( [[ -z "$prompted_key" ]] || export "$key_env=$prompted_key"
  exec python3 -m planner.guided run "${request[@]}" )
