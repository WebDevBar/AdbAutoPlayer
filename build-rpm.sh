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
#   sudo dnf upgrade ./src-tauri/target/release/bundle/rpm/adb-auto-player-*.rpm
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
pnpm tauri build --config src-tauri/tauri.bundle.linux.json --config src-tauri/tauri.bundle.templates.json --verbose

echo
echo "DONE. RPM(s):"
ls -1 src-tauri/target/release/bundle/rpm/*.rpm 2>/dev/null || echo "  (no rpm found — check build output above)"
echo "Install: sudo dnf upgrade <path-above>"
