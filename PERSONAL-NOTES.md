# WebDevBar fork - AdbAutoPlayer

Private fork of [AdbAutoPlayer/AdbAutoPlayer](https://github.com/AdbAutoPlayer/AdbAutoPlayer).
Upstream ships no Linux release, so we build our own RPM and carry a few
Fedora/Waydroid patches.

## Remotes / branches

- `origin` -> `WebDevBar/AdbAutoPlayer` (this private fork) - we push here.
- `upstream` -> `AdbAutoPlayer/AdbAutoPlayer` - pull releases from here.
- `webdevbar` branch - our patches committed on top of the current upstream
  release tag. This is the branch we build and install from.

## Our patches (in-tree on `webdevbar`)

1. `src-tauri/src-python/adb_auto_player/device/adb/device_stream.py`
   - Waydroid uses continuous streaming instead of the BlueStacks-style chunked
     `screenrecord --time-limit=1`. The chunked mode churns Waydroid's encoder
     and after ~1h it stops producing frames (permanent screenshot fallback).
2. `scripts/pytauri-bootstrap.cjs`
   - `uv venv` instead of `uv venv --python-preference only-system`, so uv's
     managed Python 3.13.9 is used (Fedora system Python is 3.14).
3. `pnpm-workspace.yaml` - allowBuilds for core-js / protobufjs.
4. `src-tauri/tauri.bundle.linux.json` - RPM bundle target + tesseract dep.
5. `build-rpm.sh` - replicates upstream `publish.yaml`'s Linux path to build the RPM.

## Build + install

```bash
cd /mnt/docs/adbautoplayer
./build-rpm.sh
sudo dnf upgrade ./src-tauri/target/release/bundle/rpm/adb-auto-player-*.rpm
```

## Update to a new upstream release X.Y.Z

```bash
git fetch upstream --tags
git rebase --onto X.Y.Z <current-tag> webdevbar   # carry our patches onto the new tag
./build-rpm.sh                                     # verify it still builds
sudo dnf upgrade ./src-tauri/target/release/bundle/rpm/adb-auto-player-*.rpm
git push --force-with-lease origin webdevbar
```

Our patch set is tiny, so the rebase is normally clean. If upstream touches one
of the files above, resolve the conflict by keeping our intent on top of theirs.
