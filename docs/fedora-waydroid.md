# Running this fork on Fedora against Waydroid

What the WebDevBar fork needs from the machine it runs on. Upstream ships no Linux
release and assumes a Windows emulator, so none of this is in upstream's docs.

Split of responsibility: **this file covers what AdbAutoPlayer needs.** The host-side
Waydroid install itself - storage on `/mnt/docs`, SELinux labelling, fstab binds, the
systemd ordering drop-in, the tray companion, hiding per-app launcher entries - lives in
`~/Dev/todor-wdb/fedora-setup/SETUP-waydroid-adbautoplayer.md`. Build and release flow for
the fork itself is in [`PERSONAL-NOTES.md`](../PERSONAL-NOTES.md).

## Runtime requirements

| Requirement | Why it is not optional |
| --- | --- |
| `android-tools` (provides `adb`) | The bot talks to the Waydroid container over adb. Without it: `adb not found in system PATH`. |
| `tesseract` | OCR. Without it the bot silently does nothing - no error, no output. The RPM declares it (`rpm.depends`), the `.deb` declares `tesseract-ocr`. |
| Waydroid at **1080x1920** internally | Every template and cell geometry in the codebase is calibrated to that resolution. `persist.waydroid.width/height = 1080/1920`. |
| `persist.waydroid.suspend = false` | Waydroid freezes the container when no window is open, which kills adb mid-run. |
| gamescope as the viewer, **not scrcpy** | See below. |

## The viewer must not be scrcpy

scrcpy claims the single software H.264 encoder, and that is the same encoder Device
Stream needs. Run both and the bot loses its stream.

gamescope renders Waydroid's native Wayland surface with no encoder in the path, in a
small movable window, while the container keeps its internal 1080x1920:

```bash
gamescope --backend wayland --expose-wayland -w 1080 -h 1920 -W 540 -H 960 \
  --cursor-scale-height 960 -- bash -lc 'waydroid show-full-ui; exec sleep infinity'
```

`-w/-h` is the internal resolution the bot requires; `-W/-H` is the window size. The
Waydroid session must start **inside** the nest - it binds to the live `WAYLAND_DISPLAY`
at session start, so launching it outside and then nesting does not work. The wrapper
that does this correctly is `fedora-setup/files/waydroid-view.sh`.

Do **not** re-apply navbar overlays or run a host-side `waydroid app launch` against a
running nest - both race gamescope and the window is then unrecoverable. Open the game by
hand.

## adb connection

The bot reaches the container on port 5555. Connect to the IP `waydroid status` reports,
which is the **container** IP (e.g. `192.168.240.112`), not the gateway `192.168.240.1` -
the gateway refuses on this layout. Two settings in `/var/lib/waydroid/waydroid.prop`
make it work without fiddling:

```ini
ro.adb.secure=0
ro.debuggable=1
```

`fedora-setup` installs a `waydroid-adb.service` that parses `waydroid status` and
connects on container start, so the exact IP never matters. Apply config-file changes
with `sudo waydroid upgrade -o`; `waydroid prop set` silently fails while the session is
stopped.

## Building the RPM

`./build-rpm.sh` does the whole thing and is the only supported path - it replicates
upstream's `publish.yaml` Linux job, which upstream has commented out. The Fedora
toolchain it expects:

```bash
sudo dnf install -y rust cargo nodejs npm uv gcc gcc-c++ make file patchelf \
  webkit2gtk4.1-devel openssl-devel libappindicator-gtk3-devel librsvg2-devel
npm i -g pnpm
```

Two things about the build that are easy to get wrong:

- A bare `pnpm tauri build` produces a binary that dies with `Failed to import
  encodings`. The release runs in PyTauri **standalone** mode, so the embedded
  python-build-standalone interpreter has to be installed into `src-tauri/pyembed/`
  first. `build-rpm.sh` handles it.
- Fedora's system Python runs ahead of what the project pins, which is why
  `scripts/pytauri-bootstrap.cjs` is patched on this branch to drop
  `--python-preference only-system` and let uv use its own managed 3.13.9.

## Waydroid-specific patch in this fork

`device/adb/device_stream.py` streams continuously instead of using the BlueStacks-style
chunked `screenrecord --time-limit=1`. Chunked mode churns Waydroid's encoder and after
roughly an hour it stops producing frames altogether, leaving the bot on the permanent
screenshot fallback. This patch is the reason the fork exists on Linux at all.

## Known host bug: amdgpu oops with Waydroid running

A kernel NULL dereference in `amdgpu_hmm_invalidate_gfx`, triggered by background memory
compaction while Waydroid pins a large GPU userptr region. Waydroid is the trigger, not
the cause, and the affected processes become unkillable. Full analysis, the
`vm.compaction_proactiveness=0` mitigation, and the revert condition are in
[`PERSONAL-NOTES.md`](../PERSONAL-NOTES.md).
