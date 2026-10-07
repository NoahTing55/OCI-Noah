from __future__ import annotations

import hashlib
import sys
from pathlib import Path

EXCLUDED_DIRS = {
    ".git", ".pytest_cache", "__pycache__", "data", "logs",
}
EXCLUDED_NAMES = {
    ".env", "release_manifest.json",
}
EXCLUDED_SUFFIXES = {
    ".pyc", ".pyo", ".db", ".sqlite", ".sqlite3",
}


def source_fingerprint(root: Path) -> str:
    root = root.resolve()
    digest = hashlib.sha256()
    files: list[Path] = []
    for candidate in root.rglob("*"):
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(root)
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        if candidate.name in EXCLUDED_NAMES or candidate.name.startswith(".env."):
            continue
        if candidate.suffix.lower() in EXCLUDED_SUFFIXES:
            continue
        files.append(candidate)
    for candidate in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        relative = candidate.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(hashlib.sha256(candidate.read_bytes()).digest())
        digest.update(b"\0")
    return digest.hexdigest()


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    if not root.is_dir():
        raise SystemExit(f"source directory not found: {root}")
    print(source_fingerprint(root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
