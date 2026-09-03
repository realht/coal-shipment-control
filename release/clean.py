from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path


CACHE_DIRECTORY_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "htmlcov",
}
FILE_SUFFIXES = {".pyc", ".pyo"}
EXCLUDED_DIRECTORY_NAMES = {".git", ".venv", "venv", "env", "node_modules"}


def is_excluded(path: Path, root: Path) -> bool:
    relative_parts = path.resolve().relative_to(root).parts
    return any(part in EXCLUDED_DIRECTORY_NAMES for part in relative_parts)


def iter_targets(root: Path):
    directories = []
    files = []

    for current_root, dir_names, file_names in os.walk(root):
        current_path = Path(current_root)

        try:
            if is_excluded(current_path, root):
                dir_names[:] = []
                continue
        except ValueError:
            dir_names[:] = []
            continue

        cache_dirs = [name for name in dir_names if name in CACHE_DIRECTORY_NAMES]
        directories.extend(current_path / name for name in cache_dirs)

        dir_names[:] = [
            name
            for name in dir_names
            if name not in CACHE_DIRECTORY_NAMES
            and name not in EXCLUDED_DIRECTORY_NAMES
        ]

        files.extend(
            current_path / name
            for name in file_names
            if Path(name).suffix in FILE_SUFFIXES
        )

    directories.sort(key=lambda item: len(item.parts), reverse=True)
    files.sort()
    yield from directories
    yield from files


def remove_target(path: Path) -> bool:
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError as error:
        print(f"Skipped (could not remove): {path} ({error})")
        return False
    return True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Remove local Python/test cache files from the project tree.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be removed without deleting anything.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Project root to clean. Defaults to the repository root.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Print only the final summary.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    targets = list(iter_targets(root))

    removed = 0
    for target in targets:
        if not args.quiet:
            action = "Would remove" if args.dry_run else "Removing"
            print(f"{action}: {target}")

        if not args.dry_run and remove_target(target):
            removed += 1

    if args.dry_run:
        print(f"Dry run finished. Targets found: {len(targets)}.")
    else:
        print(f"Cleanup finished. Removed: {removed}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
