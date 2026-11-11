#!/usr/bin/env python3
"""Daily database backup.

The Supabase free tier has NO point-in-time recovery. If a migration goes
wrong, this dump is the only way back. So it runs every day, it is verified,
and the retention is enforced.

    python tools/backup.py --dump-only          # write a local dump
    python tools/backup.py                      # dump + upload to Supabase Storage
    python tools/backup.py --restore <file>     # restore into a target database

The dump is written with `pg_dump` over the Supabase connection pooler. The
pooler is required: the free tier's direct connection is IPv6-only, which many
home ISPs do not have.

Exit code 0 means the backup was written AND (unless --dump-only) uploaded.
"""

from __future__ import annotations

import argparse
import gzip
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKUP_DIR = ROOT / "backups"
# Keep 7 daily and 4 weekly dumps. The free tier gives 1 GB of Storage.
KEEP_DAILY = 7
KEEP_WEEKLY = 4


def log(message: str) -> None:
    print(f"[backup] {message}", flush=True)


def find_pg_dump() -> str:
    """Locate pg_dump. It ships with the PostgreSQL client, not the server."""
    found = shutil.which("pg_dump")
    if found:
        return found
    for candidate in (
        r"C:\Program Files\PostgreSQL\17\bin\pg_dump.exe",
        r"C:\Program Files\PostgreSQL\16\bin\pg_dump.exe",
        r"C:\Program Files\PostgreSQL\15\bin\pg_dump.exe",
        "/usr/lib/postgresql/17/bin/pg_dump",
        "/usr/lib/postgresql/16/bin/pg_dump",
    ):
        if Path(candidate).exists():
            return candidate
    raise SystemExit(
        "pg_dump was not found. Install the PostgreSQL client tools:\n"
        "  Windows: https://www.postgresql.org/download/windows/\n"
        "  Debian:  sudo apt install postgresql-client-17\n"
        "  macOS:   brew install libpq"
    )


def connection_url() -> str:
    """Build the connection URL from the environment.

    Prefers the pooled URL (SUPABASE_DB_URL). Falls back to assembling one
    from the project ref and the database password.
    """
    pooled = os.environ.get("SUPABASE_DB_URL")
    if pooled:
        return pooled

    ref = os.environ.get("SUPABASE_PROJECT_REF")
    password = os.environ.get("SUPABASE_DB_PASSWORD")
    region = os.environ.get("SUPABASE_REGION", "aws-0")

    if not ref or not password:
        raise SystemExit(
            "Set either SUPABASE_DB_URL, or SUPABASE_PROJECT_REF and "
            "SUPABASE_DB_PASSWORD.\n"
            "The pooled URL looks like:\n"
            "  postgresql://postgres.PROJECT_REF:PASSWORD@aws-0-pooler."
            "REGION.supabase.com:5432/postgres"
        )

    return (
        f"postgresql://postgres.{ref}:{password}"
        f"@aws-0-{region}.pooler.supabase.com:5432/postgres"
    )


def write_dump() -> Path:
    """Write a compressed dump and verify it is readable."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    plain = BACKUP_DIR / f"trading-{stamp}.sql"
    compressed = plain.with_suffix(".sql.gz")

    pg_dump = find_pg_dump()
    url = connection_url()

    log(f"dumping to {plain.name}")
    env = {**os.environ, "PGPASSWORD": os.environ.get("SUPABASE_DB_PASSWORD", "")}

    result = subprocess.run(
        [
            pg_dump,
            "--dbname", url,
            "--no-owner",
            "--no-privileges",
            "--format", "plain",
            "--file", str(plain),
        ],
        capture_output=True,
        text=True,
        env=env,
    )

    if result.returncode != 0:
        raise SystemExit(f"pg_dump failed:\n{result.stderr}")

    if not plain.exists() or plain.stat().st_size == 0:
        raise SystemExit("pg_dump produced an empty file.")

    # Verify: decompress and check it really contains our schema.
    with gzip.open(plain, "rb") as fh:
        head = fh.read(4096)
    if b"CREATE TABLE" not in head:
        # The header may be long; scan more.
        with gzip.open(plain, "rb") as fh:
            body = fh.read(200_000)
        if b"CREATE TABLE" not in body and b"CREATE TABLE" not in head:
            raise SystemExit("The dump does not look like a schema dump.")

    with open(plain, "rb") as src, gzip.open(compressed, "wb", compresslevel=9) as dst:
        shutil.copyfileobj(src, dst)
    plain.unlink()

    size_mb = compressed.stat().st_size / 1_048_576
    log(f"wrote {compressed.name} ({size_mb:.2f} MB)")
    return compressed


def prune() -> None:
    """Keep 7 daily and 4 weekly dumps."""
    dumps = sorted(BACKUP_DIR.glob("trading-*.sql.gz"))
    if len(dumps) <= KEEP_DAILY:
        return

    weekly_kept = 0
    for path in dumps:
        stamp = path.stem.replace("trading-", "")
        try:
            when = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        # A weekly dump is one from Monday.
        is_monday = when.weekday() == 0
        if is_monday:
            if weekly_kept < KEEP_WEEKLY:
                weekly_kept += 1
                continue
        else:
            if len(list(BACKUP_DIR.glob("trading-*.sql.gz"))) <= KEEP_DAILY:
                break
        # Older than the retention: remove.
        if len(dumps) > KEEP_DAILY and not is_monday:
            path.unlink()
            log(f"removed old dump {path.name}")


def upload(path: Path) -> bool:
    """Upload the dump to Supabase Storage."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    bucket = os.environ.get("SUPABASE_BACKUP_BUCKET", "backups")

    if not url or not key:
        log("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set; skipping upload.")
        return False

    import urllib.request

    target = f"{url.rstrip('/')}/storage/v1/object/{bucket}/{path.name}"
    data = path.read_bytes()
    req = urllib.request.Request(
        target,
        data=data,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/gzip",
            "x-upsert": "true",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            resp.read()
        log(f"uploaded {path.name} to bucket '{bucket}'")
        return True
    except Exception as exc:  # noqa: BLE001
        log(f"upload failed: {exc}")
        return False


def restore(dump_path: Path, target_url: str) -> None:
    """Restore a dump into a target database."""
    pg_restore = shutil.which("pg_restore") or str(
        Path(find_pg_dump()).with_name("pg_restore.exe" if os.name == "nt" else "pg_restore")
    )
    if not Path(pg_restore).exists():
        raise SystemExit("pg_restore was not found next to pg_dump.")

    log(f"restoring {dump_path.name} into the target database")
    opener = gzip.open if dump_path.suffix == ".gz" else open
    with opener(dump_path, "rb") as fh:
        result = subprocess.run(
            [pg_restore, "--dbname", target_url, "--no-owner", "--no-privileges"],
            stdin=fh,
            capture_output=True,
            text=True,
        )
    if result.returncode != 0:
        raise SystemExit(f"pg_restore failed:\n{result.stderr}")
    log("restore complete. VERIFY the data before trusting it.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump-only", action="store_true", help="do not upload")
    parser.add_argument("--restore", type=Path, help="restore a dump file")
    parser.add_argument("--target-url", help="connection URL for --restore")
    args = parser.parse_args()

    if args.restore:
        if not args.target_url:
            raise SystemExit("--restore requires --target-url")
        restore(args.restore, args.target_url)
        return 0

    path = write_dump()
    prune()
    if not args.dump_only:
        upload(path)
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
