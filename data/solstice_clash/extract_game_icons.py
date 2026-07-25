#!/usr/bin/env python3
"""Decode AFK Journey's hero icons from the installed Waydroid game files.

These are the REAL in-game assets, not wiki uploads. Matching against them beats the
wiki library on every measure (see docs/solstice-clash/README.md):

    locked_pick blind test : 52/54 -> 54/54 correct, median score 0.797 -> 0.973
    draft_card             : 17/18 -> 18/18 above 0.90, worst 0.63 -> 0.906
    Reinier                : 0.631 -> 0.975   (wiki art is a different picture entirely)
    Eironn                 : 0.648 -> 0.953   (via his skin, spui_herohead_15_s1)

## Where the assets are

    ~/.local/share/waydroid/data/data/com.farlightgames.igame.gp/files/data/ui/icon/

Waydroid's Android filesystem lives on the HOST, so no root/adb is needed - but the
directory is owned by the Android UID, so copying it out needs sudo:

    sudo cp -r <that path>/ui /mnt/vault/solstice/gamefiles/
    sudo chown -R $USER:$USER /mnt/vault/solstice/gamefiles/ui

Relevant subdirectories: hero/ (593), heroskin/ (153), heroult/ (208), duelicon/ (169).

## The container format

Files are named `*.png` but are NOT PNGs. Layout:

    bytes 0-2   "AST"
    byte  3,4   width  = b3 + b4*256
    byte  5,6   height = b5 + b6*256
    byte  7     13  (block-size code; all observed files are ASTC 6x6)
    bytes 8-11  uncompressed size, uint32 LE
    bytes 12+   LZ4 *block* (not frame) compressed ASTC data

Decode: LZ4-block decompress to the stated size, then ASTC 6x6 at the stated dimensions,
then **flip vertically** (Unity's texture origin is bottom-left).

Verified against every size variant present: 180x248 -> 20160, 508x716 -> 163200,
300x565 -> 76000, 280x168 -> 21056.

## Naming

    spui_herohead_<ID>.png        base hero icon, <ID> is the game's own hero id
    spui_herohead_<ID>_s1.png     skin variant (suffix after the id = skin)

IDs below 1000 are heroes; 1000+ are NPCs, mobs and bosses.

## Gamma

Decoded RGB renders darker than the game draws it. Applying exponent **1/1.8** both looks
correct and measurably improves matching against real ADB frames (median 0.9550 -> 0.9718,
worst 0.9055 -> 0.9115). Do NOT bake it into the files - apply at library-build time so it
stays tunable per image. Stored in `library_config.gamma`.

Note: comparing against wiki art suggests a bimodal correction (20 of 103 need gamma, 83
do not). That is an artifact of inconsistent wiki uploads. Measured against captured game
frames - the only ground truth that matters - a single 1/1.8 improves everything.

Usage:
    python3 extract_game_icons.py <ui_icon_dir> <output_dir>
"""

from __future__ import annotations

import glob
import os
import struct
import sys

import cv2
import lz4.block
import numpy as np
import texture2ddecoder

ASTC_BLOCK = 6          # header byte 7 == 13 on every observed file
GAMMA_EXPONENT = 1 / 1.8
SUBDIRS = ("hero", "heroskin", "heroult", "duelicon")


def decode(path: str) -> np.ndarray | None:
    """Decode one AST container to a BGRA image, correctly oriented."""
    data = open(path, "rb").read()
    if data[:3] != b"AST":
        return None
    width = data[3] + data[4] * 256
    height = data[5] + data[6] * 256
    raw_size = struct.unpack("<I", data[8:12])[0]
    raw = lz4.block.decompress(data[12:], uncompressed_size=raw_size)
    rgba = texture2ddecoder.decode_astc(raw, width, height, ASTC_BLOCK, ASTC_BLOCK)
    img = np.frombuffer(rgba, dtype=np.uint8).reshape(height, width, 4)
    return cv2.flip(img, 0)     # Unity origin is bottom-left


def apply_gamma(bgra: np.ndarray, exponent: float = GAMMA_EXPONENT) -> np.ndarray:
    """Brighten decoded RGB. Alpha is untouched."""
    out = bgra.copy()
    rgb = np.clip(out[:, :, :3].astype(np.float32) / 255.0, 0, 1)
    out[:, :, :3] = (255.0 * np.power(rgb, exponent)).astype(np.uint8)
    return out


def main() -> None:
    src = sys.argv[1] if len(sys.argv) > 1 else "/mnt/vault/solstice/gamefiles/ui/icon"
    dst = sys.argv[2] if len(sys.argv) > 2 else "/mnt/vault/solstice/gamefiles"
    total = 0
    for sub in SUBDIRS:
        indir = os.path.join(src, sub)
        if not os.path.isdir(indir):
            print(f"  {sub}: missing, skipped")
            continue
        outdir = os.path.join(dst, f"{sub}_png")
        os.makedirs(outdir, exist_ok=True)
        ok = fail = 0
        for f in sorted(glob.glob(os.path.join(indir, "*.png"))):
            try:
                img = decode(f)
                if img is None:
                    fail += 1
                    continue
                cv2.imwrite(os.path.join(outdir, os.path.basename(f)), img)
                ok += 1
            except Exception:
                fail += 1
        print(f"  {sub:9s} decoded {ok:4d}  failed {fail}")
        total += ok
    print(f"  total {total} icons -> {dst}")
    print("  NOTE: files are written WITHOUT gamma. Apply apply_gamma() when building the")
    print("        match library, using library_config.gamma from heroes.sqlite.")


if __name__ == "__main__":
    main()
