#!/usr/bin/env bash
# Install the connected Linux runtime on Ubuntu 24.04 x86-64.
# Run from the repository root as your normal user; sudo is used for apt and AppArmor.
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
home=${PLANNER_HOME:-$HOME/.local/share/planner}
version=2.2.5
tarball=opencode-linux-x64.tar.gz
checksum=09f44302c10fd036912f59c81fd1aef5df45dd06f649197f9dfc9bbe2a79614e
url=https://github.com/anomalyco/opencode/releases/download/v$version/$tarball

step() { printf '\n== %s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

. /etc/os-release
if [[ "${ID:-}" != ubuntu || "${VERSION_ID:-}" != 24.04 || $(uname -m) != x86_64 ]]; then
  [[ "${PLANNER_ALLOW_UNTESTED:-}" == 1 ]] ||
    fail "Accepted only on Ubuntu 24.04 x86-64 (found ${PRETTY_NAME:-unknown} $(uname -m)). Set PLANNER_ALLOW_UNTESTED=1 to try anyway."
  printf 'WARNING: untested platform; protection checks still run but there are no acceptance results.\n'
fi
[[ $EUID -ne 0 ]] || fail "Run as your normal user, not root."

step "Installing system packages"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_SUSPEND=1 apt-get install -y -qq \
  python3 bubblewrap apparmor apparmor-utils libseccomp2 fuse3 libfuse3-dev gcc curl ca-certificates tar

step "Allowing Bubblewrap to create user namespaces"
if bwrap --unshare-user --unshare-pid --ro-bind / / /usr/bin/true 2>/dev/null; then
  echo "Bubblewrap already works."
else
  existing=$(grep -lE '^[^#]*/usr/bin/bwrap' /etc/apparmor.d/* 2>/dev/null | grep -v planner-bwrap || true)
  [[ -z "$existing" ]] || fail "Another AppArmor profile already covers bwrap ($existing). Review it; see docs/LINUX_SANDBOX.md."
  sudo tee /etc/apparmor.d/planner-bwrap >/dev/null <<'EOF'
abi <abi/4.0>,
include <tunables/global>
/usr/bin/bwrap flags=(unconfined) {
  userns,
}
EOF
  sudo apparmor_parser -r /etc/apparmor.d/planner-bwrap
  bwrap --unshare-user --unshare-pid --ro-bind / / /usr/bin/true || fail "Bubblewrap still cannot create namespaces."
  echo "Added /etc/apparmor.d/planner-bwrap."
fi
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || fail "Python 3.11 or newer is required."

mkdir -p "$home"
chmod 700 "$home"

step "Installing OpenCode $version"
opencode=$home/opencode-$version/opencode
if [[ -x "$opencode" ]] && [[ $("$opencode" --version 2>/dev/null) == "opencode v$version" ]]; then
  echo "Already installed: $opencode"
else
  download=$(mktemp -d)
  trap 'rm -rf "$download"' EXIT
  curl -fsSL "$url" -o "$download/$tarball"
  echo "$checksum  $download/$tarball" | sha256sum -c --quiet || fail "OpenCode checksum mismatch"
  mkdir -p "$home/opencode-$version"
  tar -xzf "$download/$tarball" -C "$home/opencode-$version"
  [[ -x "$opencode" ]] || fail "OpenCode binary not found in $tarball"
fi

step "Building the FUSE helper"
gcc -Wall -Wextra -Werror -O2 -D_FILE_OFFSET_BITS=64 -I/usr/include/fuse3 \
  "$repo/planner/native/fuse_bridge.c" -lfuse3 -o "$home/fuse-bridge"

step "Loading the worker AppArmor profile"
# The profile name is derived from the runtime sources; re-run this script after updating them.
profile=$home/planner-connected.apparmor
(cd "$repo" && python3 -c 'from planner.native.confined_opencode import profile_text; print(profile_text(), end="")') > "$profile"
[[ -s "$profile" ]] || fail "Could not generate the AppArmor profile."
sudo install -m 644 "$profile" /etc/apparmor.d/planner-connected
sudo apparmor_parser -r /etc/apparmor.d/planner-connected

step "Checking the runtime"
cd "$repo"
python3 - "$opencode" "$home/fuse-bridge" <<'EOF'
import sys
from pathlib import Path
from planner.native.confined_opencode import runtime_status
for status in runtime_status(Path(sys.argv[1]), Path(sys.argv[2])):
    print(f"{status.backend}: {'ready' if status.available else 'BLOCKED'} - {status.reason}")
    if not status.available:
        sys.exit(1)
EOF

cat <<EOF

Installed under $home. Next, see "Run the demo" in docs/QUICKSTART.md:
  read -rs SOCLAAS_API_KEY && export SOCLAAS_API_KEY
  scripts/demo.sh
EOF
