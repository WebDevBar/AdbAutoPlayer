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
# BUMP bundle.linux.rpm.release in src-tauri/tauri.bundle.linux.json first
./build-rpm.sh
sudo dnf upgrade /mnt/docs/adbautoplayer/dist/AdbAutoPlayer-latest.rpm
```

`dist/AdbAutoPlayer-latest.rpm` is a symlink the build refreshes, so the install
command never changes even though the real filename carries version and release.

### ⛔ Always bump the RPM release

The fork's `version` tracks the UPSTREAM tag and must not be invented - inventing
one collides with a future upstream release. Our rebuilds bump the RPM **release**
instead, which is exactly what that field is for:

```
src-tauri/tauri.bundle.linux.json -> bundle.linux.rpm.release: "1" -> "2" -> ...
```

Reset it to `"1"` when rebasing onto a new upstream tag.

**Why this is a hard rule:** on 2026-07-27 a rebuild kept version AND release,
so the filename was identical, `dnf upgrade` correctly decided there was nothing
newer, and the old binary kept running. It looked exactly like the code fix had
failed - the fix was fine, the install never happened. Use `dnf reinstall` only
to recover from that situation; the cure is bumping the release.

## Update to a new upstream release X.Y.Z

```bash
git fetch upstream --tags
git rebase --onto X.Y.Z <current-tag> webdevbar   # carry our patches onto the new tag
./build-rpm.sh                                     # verify it still builds
sudo dnf upgrade ./target/release/bundle/rpm/AdbAutoPlayer-*.rpm
git push --force-with-lease origin webdevbar
```

Our patch set is tiny, so the rebase is normally clean. If upstream touches one
of the files above, resolve the conflict by keeping our intent on top of theirs.


## Update log

Fork history - what upstream release we rode and when (patches rebased on top each time).

| Date | Upstream | Notes |
|---|---|---|
| 2026-06-28 | 12.9.16 -> 12.9.17 | Fork created (private repo + `upstream` remote). Waydroid continuous-streaming fix + Fedora RPM build. First fork build = 12.9.17. |
| 2026-06-28 | 12.9.17 -> 12.9.18 | Rebased onto 12.9.18 (clean). Rebuilt + installed. |
| 2026-07-04 | 12.9.18 -> 12.9.20 | Rebased onto 12.9.20 (`git rebase --onto 12.9.20 12.9.18 webdevbar`, clean, Waydroid fix intact). Upstream since .18: Homestead rewrite, Supreme Arena + Quest fixes, per-task repeat, RapidOCR 3.9.0 config fix (39 commits). Rebuilt RPM + installed. |
