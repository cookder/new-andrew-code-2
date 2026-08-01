"""Stage 1's watched folder.

Poll rather than inotify: a multi-GB file copied off a phone appears long before
it is complete, and the simplest reliable "is it done" test is a size that stops
changing. A filesystem event would just tell us the write started.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from ..config import settings
from ..db import connect, init_db
from .runner import run

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v"}


def is_settled(path: Path, *, checks: int = 3, interval: float = 2.0) -> bool:
    """True once the file size has been stable across consecutive checks."""
    try:
        last = path.stat().st_size
    except OSError:
        return False
    for _ in range(checks):
        time.sleep(interval)
        try:
            current = path.stat().st_size
        except OSError:
            return False
        if current != last:
            return False
        last = current
    return True


def pending(watch_dir: Path) -> list[Path]:
    return sorted(
        p
        for p in watch_dir.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES and not p.name.startswith(".")
    )


def watch_once(watch_dir: Path | None = None, *, with_clips: bool = True) -> list[dict]:
    """One sweep of the watched folder."""
    directory = Path(watch_dir) if watch_dir else settings.watch_dir
    directory.mkdir(parents=True, exist_ok=True)

    conn = connect()
    out: list[dict] = []
    try:
        for video in pending(directory):
            if not is_settled(video):
                continue
            result = run(conn, video, with_clips=with_clips)
            out.append(
                {
                    "file": video.name,
                    "session_id": result.session_id,
                    "halted": result.halted,
                    "stages": [
                        {"name": s.name, "ok": s.ok, "detail": s.detail}
                        for s in result.stages
                    ],
                }
            )
    finally:
        conn.close()
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Watch a folder and ingest new range videos.")
    parser.add_argument("--dir", type=Path, default=None, help="watched folder")
    parser.add_argument("--once", action="store_true", help="single sweep, then exit")
    parser.add_argument("--interval", type=float, default=30.0, help="poll seconds")
    parser.add_argument("--no-clips", action="store_true", help="skip stage 7")
    args = parser.parse_args(argv)

    settings.ensure_dirs()
    init_db()
    directory = args.dir or settings.watch_dir
    print(f"watching {directory}")

    while True:
        for entry in watch_once(directory, with_clips=not args.no_clips):
            status = "HALTED" if entry["halted"] else "ok"
            print(f"{entry['file']}: session {entry['session_id']} [{status}]")
            for stage in entry["stages"]:
                mark = "+" if stage["ok"] else "!"
                print(f"  {mark} {stage['name']}: {stage['detail']}")
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
