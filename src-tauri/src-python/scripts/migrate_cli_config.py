"""Move the CLI's settings out of the repo and into the GUI's config directory.

Why this exists: `src-tauri/settings/*.toml` are UPSTREAM-TRACKED files. `App.toml`
and `ADB.toml` are byte-identical to upstream and `AFKJourney.toml` differs by a
handful of local lines. A settings editor writing into them would conflict on every
`merge upstream/main` and leave the working tree permanently dirty.

It creates the TWO-LEVEL layout explicitly rather than letting a first run make one.
The target directory does not exist yet, and the resolver's "neither file present"
branch classes an empty directory as FLAT - so a first run would write
`<config>/App.toml` alongside everything else and diverge from the GUI permanently.

Run it once:

    uv run python src-python/scripts/migrate_cli_config.py          # dry run
    uv run python src-python/scripts/migrate_cli_config.py --apply

The repo copies are left exactly where they are, still tracked, so upstream merges
stay clean.
"""

import argparse
import shutil
import sys
from pathlib import Path

APP_IDENTIFIER = "com.AdbAutoPlayer.AdbAutoPlayer"
ROOT_FILES = ("App.toml",)
PROFILE_FILES = ("ADB.toml", "AFKJourney.toml")


def default_target() -> Path:
    """Where the GUI keeps its config on this platform.

    Returns:
        The config root for the app identifier.
    """
    if sys.platform == "win32":
        base = Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".config"
    return base / APP_IDENTIFIER


def plan(source: Path, target: Path, profile: int) -> list[tuple[Path, Path]]:
    """Work out what would be copied where.

    Args:
        source: The repo settings directory.
        target: The config root to create.
        profile: Profile index the per-profile files go under.

    Returns:
        (from, to) pairs, skipping anything the source does not have.
    """
    pairs = []
    for name in ROOT_FILES:
        if (source / name).is_file():
            pairs.append((source / name, target / name))
    for name in PROFILE_FILES:
        if (source / name).is_file():
            pairs.append((source / name, target / str(profile) / name))
    return pairs


def main() -> int:
    """Run the migration.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    repo_settings = Path(__file__).resolve().parents[2] / "settings"
    parser.add_argument("--source", type=Path, default=repo_settings)
    parser.add_argument("--target", type=Path, default=default_target())
    parser.add_argument("--profile", type=int, default=0)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually copy. Without this it only prints what it would do.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite files already present at the target.",
    )
    args = parser.parse_args()

    if not args.source.is_dir():
        print(f"source does not exist: {args.source}", file=sys.stderr)
        return 1

    pairs = plan(args.source, args.target, args.profile)
    if not pairs:
        print(f"nothing to copy from {args.source}", file=sys.stderr)
        return 1

    existing = [dst for _, dst in pairs if dst.exists()]
    if existing and not args.force:
        print("target already has these files - re-run with --force to overwrite:")
        for path in existing:
            print(f"  {path}")
        return 1

    for src, dst in pairs:
        print(f"{'copy' if args.apply else 'would copy'}  {src}\n      -> {dst}")
        if args.apply:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    if not args.apply:
        print("\ndry run - nothing written. Re-run with --apply.")
        return 0

    print(f"\ndone. Point --app-config-dir at the ROOT: {args.target}")
    print("The repo copies were left in place and are still tracked by git.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
