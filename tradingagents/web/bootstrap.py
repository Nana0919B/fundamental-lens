"""Install the bundled research archive into an empty local Web data directory."""

import errno
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path

SEED_DIR = Path(__file__).parent / "seed_data"


def initialize_shared_data(store: Path, seed_dir: Path = SEED_DIR) -> bool:
    """Copy a verified seed atomically; an existing nonempty store always wins."""
    store = Path(store)
    if store.exists() and any(store.iterdir()):
        return False
    manifest_file = seed_dir / "manifest.json"
    if not manifest_file.exists():
        return False
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    files = manifest["files"]
    if "research.sqlite3" not in files or not any(name.startswith("jobs/") for name in files):
        raise ValueError("Shared research archive requires a database and job records")
    for name, digest in files.items():
        if not re.fullmatch(r"research\.sqlite3|jobs/[0-9a-f]{32}\.json", name):
            raise ValueError("Invalid shared archive filename")
        if hashlib.sha256((seed_dir / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Shared research archive checksum mismatch: {name}")

    store.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".fundanalyst-seed-", dir=store.parent) as temporary:
        staging = Path(temporary) / "web"
        staging.mkdir()
        for name in files:
            shutil.copyfile(seed_dir / name, staging / Path(name).name)
        (staging / ".seed-version").write_text(str(manifest["format_version"]), encoding="utf-8")
        if store.exists():
            try:
                store.rmdir()  # Only removes a still-empty destination.
            except OSError:
                return False
        try:
            staging.rename(store)
        except OSError as exc:
            if exc.errno in (errno.EEXIST, errno.ENOTEMPTY):
                return False  # Another process has already created local data.
            raise
    return True
