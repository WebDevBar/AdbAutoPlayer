#!/usr/bin/env bash
# Build AdbAutoPlayer as a Linux RPM from source.
# Upstream ships NO Linux release (their CI Linux matrix is commented out), so
# we replicate .github/workflows/publish.yaml's Linux path here. This is the
# WebDevBar private fork (origin=WebDevBar/AdbAutoPlayer, upstream=AdbAutoPlayer/
# AdbAutoPlayer); our patches now live in git on the `webdevbar` branch, not in
# an external vault dir. Faithful to publish.yaml as of tag 12.9.17.
#
# UPDATE FLOW (when upstream tags a new release X.Y.Z):
#   cd /mnt/docs/adbautoplayer
#   git fetch upstream --tags
#   git rebase --onto X.Y.Z <current-tag> webdevbar   # carry our patches onto the new tag
#   ./build-rpm.sh
#   sudo dnf upgrade ./target/release/bundle/rpm/AdbAutoPlayer-*.rpm
#   git push --force-with-lease origin webdevbar
#
# First run after an update: watch for build breakage if upstream changed the
# build (pin versions below come from publish.yaml - re-check them against the
# new tag's publish.yaml if the build fails).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

# pnpm aborts a non-interactive node_modules purge without a TTY (ERR_PNPM_ABORTED_REMOVE_MODULES_DIR_NO_TTY).
# CI=true tells pnpm to proceed non-interactively. Needed since 12.9.16.
export CI=true

# Pins from publish.yaml (re-verify against a new tag's publish.yaml if build breaks)
PYTHON_VERSION="3.13.9"
PBS_TAG="20251031"
TARGET="x86_64-unknown-linux-gnu"
PRODUCT_NAME="AdbAutoPlayer"

echo "== 0/6 prerequisites (expected already installed from initial setup) =="
command -v uv   >/dev/null || { echo "missing: uv (astral-sh)"; exit 1; }
command -v pnpm >/dev/null || { echo "missing: pnpm (v10)"; exit 1; }
command -v cargo>/dev/null || { echo "missing: rust toolchain"; exit 1; }
# system deps (Fedora equivalents of publish.yaml's apt list):
rpm -q webkit2gtk4.1-devel libappindicator-gtk3-devel librsvg2-devel patchelf >/dev/null 2>&1 \
  || echo "  note: ensure webkit2gtk4.1-devel libappindicator-gtk3-devel librsvg2-devel patchelf are installed (sudo dnf install ...)"

echo "== 1/6 our patches are tracked in git (webdevbar branch); nothing to copy =="
# pnpm-workspace.yaml, scripts/pytauri-bootstrap.cjs, src-tauri/tauri.bundle.linux.json
# and the Waydroid device_stream.py fix are committed in-tree on this fork branch.

# Bake the fork API key into the build. Read from the credentials file that
# lives OUTSIDE every repo, or from the environment. Without it the build still
# succeeds - sync is simply disabled, which is the right default for a build
# nobody is going to share.
SOLSTICE_DIR="src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice"
CRED_FILE="$HOME/.local/share/webdevbar/gameretro-adb-api.md"
FORK_KEY="${ADB_SYNC_KEY_BUILTIN:-}"
if [ -z "$FORK_KEY" ] && [ -f "$CRED_FILE" ]; then
  FORK_KEY="$(awk '/^## Fork API key/{found=1; next} found && NF {print $1; exit}' "$CRED_FILE")"
fi
if [ -n "$FORK_KEY" ]; then
  printf '"""Generated at build time. NOT committed - see .gitignore."""\n\nFORK_API_KEY = "%s"\n' \
    "$FORK_KEY" > "$SOLSTICE_DIR/_forkkey.py"
  echo "== fork key baked in (${#FORK_KEY} chars) =="
else
  rm -f "$SOLSTICE_DIR/_forkkey.py"
  echo "== no fork key found; sync will be disabled in this build =="
fi

echo "== 2/6 node deps =="
pnpm install

echo "== 3/6 embedded python (python-build-standalone) into src-tauri/pyembed =="
DEST_DIR="src-tauri/pyembed"
rm -rf "$DEST_DIR"; mkdir -p "$DEST_DIR"
url="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/cpython-${PYTHON_VERSION}+${PBS_TAG}-${TARGET}-install_only_stripped.tar.gz"
curl -L "$url" | tar -xz -C "$DEST_DIR"
rm -rf "$DEST_DIR"/python/include \
       "$DEST_DIR"/python/lib/python*/test \
       "$DEST_DIR"/python/lib/python*/ensurepip \
       "$DEST_DIR"/python/lib/python*/idlelib \
       "$DEST_DIR"/python/lib/python*/turtledemo

echo "== 4/6 install project into embedded python =="
PYTAURI_STANDALONE=1 uv pip install --exact --compile-bytecode \
  --python="./src-tauri/pyembed/python/bin/python3" ./src-tauri

echo "== 5/6 build env (rpath to embedded python) + bundle templates =="
export PYO3_PYTHON="$(realpath ./src-tauri/pyembed/python/bin/python3)"
export RUSTFLAGS=" -C link-arg=-Wl,-rpath,\$ORIGIN/../lib/${PRODUCT_NAME}/lib -L $(realpath ./src-tauri/pyembed/python/lib)"
pnpm bundle-templates

echo "== 6/6 build the RPM =="
# Our tauri.bundle.linux.json pins targets: ["rpm"]; templates config adds the bundled templates.
#
# `|| true`: tauri exits non-zero AFTER writing a perfectly good RPM, because it
# wants TAURI_SIGNING_PRIVATE_KEY for updater artifacts we do not build. With
# `set -e` that aborted the script before the symlink below was refreshed, so the
# stable path silently kept pointing at an older build and `dnf upgrade` reported
# "nothing to do". The real check is whether the RPM exists, which happens below.
pnpm tauri build --config src-tauri/tauri.bundle.linux.json --config src-tauri/tauri.bundle.templates.json --verbose || true

# A stable path so the install command never changes. The real filename carries
# version AND release (AdbAutoPlayer-12.9.24-2.x86_64.rpm), which is correct for
# RPM but useless to type - and getting it wrong is how an install silently
# no-ops against the version already installed.
STABLE="AdbAutoPlayer-latest.rpm"
NEWEST="$(ls -1t target/release/bundle/rpm/*.rpm 2>/dev/null | head -1 || true)"
if [ -z "$NEWEST" ]; then
  echo "FAILED: no RPM was produced." >&2
  exit 1
fi
# Guard against a stale symlink surviving a failed build: only accept an RPM
# newer than the one currently linked.
ln -sfn "$(realpath "$NEWEST")" "$STABLE"

echo
echo "DONE. RPM(s):"
ls -1 target/release/bundle/rpm/*.rpm 2>/dev/null || echo "  (no rpm found — check build output above)"
echo
echo "  Built: ${NEWEST:-none}"
echo "  Stable path: $(realpath "$STABLE" 2>/dev/null || echo n/a)"
echo
echo "Install:"
echo "  sudo dnf upgrade $(pwd)/$STABLE"
echo
echo "BUMP THE RELEASE for every rebuild of the same upstream version:"
echo "  src-tauri/tauri.bundle.linux.json -> bundle.linux.rpm.release"
echo "Without a bump the filename is unchanged, dnf sees nothing newer, and the"
echo "install silently does nothing - which has already cost one debugging round."
