#!/usr/bin/env bash
# The planner demo: pick a scenario, approve the worker's permissions, and watch.
# Run it with no arguments; see --help for the rest.
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
planner_home=${PLANNER_HOME:-$HOME/.local/share/planner}
model=${PLANNER_MODEL:-soclaas/qwen3.8:27b}
provider=${PLANNER_PROVIDER_URL:-https://soclaas-api.comp.nus.edu.sg/v1}
key_env=${PLANNER_API_KEY_ENV:-SOCLAAS_API_KEY}
opencode=$planner_home/opencode-2.2.5/opencode
helper=$planner_home/fuse-bridge

heading() { if [[ -n "${NO_COLOR:-}" ]]; then printf '\n%s\n' "$*"; else printf '\n\033[1m%s\033[0m\n' "$*"; fi; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
field() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }
fixtures() { python3 -m planner.demo_scenarios "$@"; }
usage() {
  cat <<'EOF'
Usage: scripts/demo.sh [SCENARIO... | all] [--yes]
       scripts/demo.sh list
       scripts/demo.sh log [SCENARIO]

With no scenario, pick one from a menu.
  --yes  approve the worker's permissions without asking
  list   show the scenarios
  log    show what happened in the latest run, or the latest run of SCENARIO
EOF
}

cd "$repo"
yes=0
selected=()
case "${1:-}" in
  list) (($# == 1)) || fail "Usage: scripts/demo.sh list"; fixtures list; exit 0 ;;
  log) shift; (($# <= 1)) || fail "Usage: scripts/demo.sh log [SCENARIO]"; fixtures log "$planner_home" "$@"; exit 0 ;;
esac
for argument in "$@"; do
  case "$argument" in
    --yes) yes=1 ;;
    --help|-h) usage; exit 0 ;;
    -*) usage >&2; fail "Unknown option: $argument" ;;
    *) selected+=("$argument") ;;
  esac
done

catalog=$(fixtures list)
names=()
while IFS=$'\t' read -r name description; do names+=("$name"); done <<< "$catalog"
known() { local name; for name in "${names[@]}"; do [[ "$name" != "$1" ]] || return 0; done; return 1; }

if ((${#selected[@]} == 0)); then
  if ((yes)); then
    selected=(greeting)
  else
    heading "Planner demo"
    echo "A coding agent works on a small project inside a sandbox. The supervisor"
    echo "blocks anything outside the permissions you approve, and warns you when it does."
    echo
    index=1
    while IFS=$'\t' read -r name description; do
      printf '  %d  %-24s %s\n' "$index" "$name" "$description"
      index=$((index + 1))
    done <<< "$catalog"
    echo
    while :; do
      read -rp "Pick a scenario [1] (several: 2 3, or all): " reply ||
        fail "No input. To run without prompts: scripts/demo.sh alarm --yes"
      read -ra choices <<< "${reply//,/ }"
      ((${#choices[@]})) || choices=(1)
      selected=()
      for choice in "${choices[@]}"; do
        if [[ "$choice" =~ ^[0-9]{1,2}$ ]] && ((10#$choice >= 1 && 10#$choice <= ${#names[@]})); then
          selected+=("${names[10#$choice - 1]}")
        else
          selected+=("$choice")
        fi
      done
      valid=1
      for choice in "${selected[@]}"; do [[ "$choice" == all ]] || known "$choice" || valid=0; done
      ((valid)) && break
      echo "Pick numbers from the list, or all."
    done
  fi
fi
for choice in "${selected[@]}"; do
  if [[ "$choice" == all ]]; then
    ((${#selected[@]} == 1)) || fail "Use all on its own."
    selected=("${names[@]}")
  fi
done
unique=()
for choice in "${selected[@]}"; do
  known "$choice" || fail "Unknown scenario: $choice. See: scripts/demo.sh list"
  [[ " ${unique[*]-} " == *" $choice "* ]] || unique+=("$choice")
done
selected=("${unique[@]}")
echo "Selected: ${selected[*]}"

if [[ ! -x "$opencode" || ! -x "$helper" ]]; then
  echo "The demo runtime is not installed in $planner_home."
  answer=n
  ((yes)) || read -rp "Install it now with scripts/install-ubuntu.sh? It asks for your sudo password. [y/N] " answer || answer=n
  [[ "$answer" == [yY]* ]] || fail "Install it with scripts/install-ubuntu.sh, then run the demo again."
  scripts/install-ubuntu.sh
fi
needs_model=0
for scenario in "${selected[@]}"; do [[ "$scenario" == alarm ]] || needs_model=1; done
if ((needs_model)); then  # The scripted alarm scenario uses no model provider or key.
  [[ "$key_env" =~ ^[A-Z][A-Z0-9_]*$ ]] || fail "PLANNER_API_KEY_ENV must be an upper-case variable name."
  if [[ -z "${!key_env:-}" ]]; then
    key=
    if ((!yes)) && read -rsp "Paste your API key for $provider (it is not shown): " key; then echo; fi
    [[ -n "$key" ]] || fail "No API key. Set it first: read -rs $key_env && export $key_env"
    export "$key_env=$key"
    unset key
  fi
fi
python3 - "$opencode" "$helper" "$model" "$provider" "$key_env" "$needs_model" <<'EOF' ||
import sys
from pathlib import Path
from planner.connector import ConnectorConfig, ProviderConfig
from planner.native.confined_opencode import runtime_status
if sys.argv[6] == "1":
    ConnectorConfig(sys.argv[3], provider=ProviderConfig(sys.argv[4], api_key_env=sys.argv[5]))
for status in runtime_status(Path(sys.argv[1]), Path(sys.argv[2])):
    if not status.available:
        sys.exit("BLOCKED: " + status.reason)
EOF
  fail "The demo cannot run safely yet; the reason is above. Re-running scripts/install-ubuntu.sh usually fixes it."

cli() { python3 -m planner --state "$state" "$@"; }
run_demo() {
  local scenario=$1 demo=$2 position=$3 state=$2/state.sqlite config project proposal task revision answer
  config=$(fixtures prepare "$scenario" "$demo" --model "$model")
  project=$(field 'd["project"]' <<< "$config")
  heading "== $scenario$position"
  field 'd["description"] + ".\nTask: " + d["task"]' <<< "$config"
  proposal=$(cli propose "$project" --template source)
  task=$(field 'd["id"]' <<< "$proposal")
  revision=$(field 'd["revision"]' <<< "$proposal")
  printf '%s\n' "$task" > "$demo/task"
  echo
  fixtures policy <<< "$proposal"
  if ((yes)); then
    echo "Approve? [y/N] y (--yes)"
  else
    read -rp "Approve? [y/N] " answer || answer=n
    [[ "$answer" == [yY]* ]] || { cli cancel "$task" > /dev/null; echo "Not approved, so $scenario was skipped."; return 0; }
  fi
  cli approve-task "$task" --revision "$revision" --assign worker > /dev/null

  local run=(--state "$state" run "$task" worker --prompt "$(cat "$demo/prompt.txt")" --opencode "$opencode"
             --fuse-helper "$helper" --runs "$demo/runs" --run-seconds 1200 --timeout 180 --alert)
  echo
  if [[ "$scenario" == alarm ]]; then
    echo "Running a scripted worker (no model) that breaks the rules on purpose..."
    fixtures scripted-run -- "${run[@]}" > "$demo/run.json" || true
  else
    echo "Running the worker with $model. This usually takes a few minutes..."
    python3 -m planner "${run[@]}" --model "$model" --provider-url "$provider" --api-key-env "$key_env" \
      > "$demo/run.json" || true
  fi
  cli export "$task" > "$demo/audit.json"
  cli report "$task" "$demo/report.html" > /dev/null
  echo
  fixtures summary "$demo" || { echo "Details: scripts/demo.sh log $scenario"; return 1; }
  echo
  echo "Details: scripts/demo.sh log $scenario"
}

mkdir -p "$planner_home"
index=0
for scenario in "${selected[@]}"; do
  index=$((index + 1))
  position=""
  ((${#selected[@]} == 1)) || position=" ($index of ${#selected[@]})"
  demo=$(mktemp -d "$planner_home/demo-$(date +%Y%m%d-%H%M%S)-$scenario-XXXX")
  run_demo "$scenario" "$demo" "$position" 2>&1 | tee "$demo/console.log"
done
