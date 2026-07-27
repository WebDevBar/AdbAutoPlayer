# WebDevBar fork - AdbAutoPlayer

Private fork of [AdbAutoPlayer/AdbAutoPlayer](https://github.com/AdbAutoPlayer/AdbAutoPlayer).
Upstream ships no Linux release, so we build our own RPM and carry a few
Fedora/Waydroid patches.

## Where it lives

`~/Dev/webdevbar/adbautoplayer` - moved there on 2026-07-27 from `/mnt/docs/adbautoplayer`
so the fork sits with every other repo on this machine.

What that trade costs: `/mnt/docs` is a separate disk that survives a reformat and `~/Dev`
is not. `target/` (13 GB of build output) and `.venv` were not carried over - both are
rebuildable, `.venv` with `uv sync` and `target/` with `./build-rpm.sh`. The one artifact
worth keeping is the last built RPM, so it lives on the surviving disk at
`/mnt/docs/adbautoplayer-rpm/` and the repo-root `AdbAutoPlayer-latest.rpm` symlink points
there until the next local build overwrites it.

`~/Dev/webdevbar/fedora-setup/SETUP-waydroid-adbautoplayer.md` covers the Waydroid host
side (storage, SELinux, `suspend=false`, adb auto-connect, the gamescope viewer). The
build and install of this fork are documented here.

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
cd ~/Dev/webdevbar/adbautoplayer
# BUMP bundle.linux.rpm.release in src-tauri/tauri.bundle.linux.json first
./build-rpm.sh
sudo dnf upgrade ~/Dev/webdevbar/adbautoplayer/AdbAutoPlayer-latest.rpm
```

`AdbAutoPlayer-latest.rpm` at the repo root is a symlink the build refreshes, so the
install command never changes even though the real filename carries version and
release. It is NOT in `dist/` - that directory is the SvelteKit build output.

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


---

## ⚠️ amdgpu kernel oops with Waydroid running (2026-07-27)

**Symptom:** Waydroid and any video app (Stremio) freeze at the same time. The frozen
process cannot be killed by any signal. Stopping Waydroid releases everything.

**Cause: a kernel bug, not our code.**

```
BUG: kernel NULL pointer dereference, address: 0000000000000000
Comm: kcompactd0
RIP: amdgpu_hmm_invalidate_gfx+0x38/0xd0 [amdgpu]
Call Trace: try_to_migrate_one -> __mmu_notifier_invalidate_range_start -> amdgpu_hmm_invalidate_gfx
```

`kcompactd0` (background memory compaction) migrates pages, tells amdgpu its mapping
moved, and amdgpu dereferences NULL. It happens inside memory-management locks, which
is why the affected processes become unkillable. Waydroid is the trigger, not the bug:
it pins a large GPU userptr region, which is what makes compaction touch it.

Upstream: introduced by `drm/amdgpu: fix waiting for all submissions for userptrs`,
fixed by `drm/amdgpu: fix check in amdgpu_hmm_invalidate_gfx`, commit `631849ff5d60`.
Reported upstream by someone hitting it "when playing video with mpv".
https://lwn.net/Articles/1081243/

**Affects all 7.1.x.** Verified against the stable changelogs 2026-07-27: the bad commit
is in the 7.1 base, not a stable backport, so reverting to 7.1.3 or 7.1.4-202 does NOT
help. The fix is NOT in 7.1.5-200 either. Mesa is irrelevant - this is kernel-side.

### Current mitigation

```bash
sudo sysctl -w vm.compaction_proactiveness=0     # default is 20
```

Stops the background compaction thread that has caused every crash so far. On-demand
compaction still runs, so this narrows the window rather than closing it. Costs some
memory fragmentation (huge pages fall back to 4K); harmless on a desktop, revertible
instantly, does not survive reboot unless written to `/etc/sysctl.d/`.

### ⛔ CHECK PERIODICALLY AND REVERT

This mitigation exists ONLY because the kernel fix has not shipped. When a kernel
carrying commit `631849ff5d60` lands, install it and revert the sysctl:

```bash
# is the fix in the available kernel yet?
dnf list --available kernel
curl -s https://cdn.kernel.org/pub/linux/kernel/v7.x/ChangeLog-<version> \
  | grep -i amdgpu_hmm_invalidate_gfx

# once it is:
sudo sysctl -w vm.compaction_proactiveness=20    # back to the default
```

Also delete `/etc/sysctl.d/` entry if one was ever added. Leaving a workaround in place
after its cause is fixed is how a machine accumulates unexplained settings.


---

## Releasing to collaborators (Windows installer + Linux RPM)

`.github/workflows/release-webdevbar.yaml` builds both and attaches them to a
GitHub release. Deliberately a SEPARATE file from upstream's `publish.yaml`:
our patches live on `webdevbar`, which is rebased onto each new upstream tag, and
editing upstream's workflow would conflict on every rebase.

Windows is built on a Windows runner because Tauri needs the MSVC toolchain - it
cannot be cross-compiled from Linux. Upstream ships an NSIS **installer**
(`installMode: currentUser`), not a portable app, and ours matches.

### One-time setup

Add the repository secret `ADB_SYNC_KEY_BUILTIN`, set to the fork key in
`~/.local/share/webdevbar/gameretro-adb-api.md`:

```bash
gh secret set ADB_SYNC_KEY_BUILTIN --repo WebDevBar/AdbAutoPlayer
```

Without it the build still succeeds and sync disables itself - the right
behaviour for a build with no key, rather than a failure.

### Cutting a release

```bash
# bump bundle.linux.rpm.release in src-tauri/tauri.bundle.linux.json first
gh release create wdb-12.9.24-6 --repo WebDevBar/AdbAutoPlayer \
  --title "WDB 12.9.24-6" --notes "Screenshot archiving removed"
```

Publishing the release triggers the workflow. Artifacts also appear on any
`workflow_dispatch` run, so a build can be tested without cutting a release.

Tag names are prefixed `wdb-` so they never collide with upstream tags, which the
rebase flow relies on.

**The tag number must equal the RPM release you just bumped to.** `wdb-12.9.24-4` was
re-run after a later commit and ended up carrying `AdbAutoPlayer-12.9.24-5.x86_64.rpm`,
so the release read as one build older than the assets actually were. Only the RPM
filename carries a release number - the `.exe` and `.deb` do not - which is exactly why
the tag has to.
