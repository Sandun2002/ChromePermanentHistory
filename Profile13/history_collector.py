#!/usr/bin/env python3
"""
Permanent, profile-specific Chrome history archive for Profile 13.

This program only reads a temporary snapshot of Chrome's History database. It
never writes to, clears, or replaces Chrome profile data. Every successfully
observed visit is inserted once into a separate, append-only SQLite archive.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlparse


# This setup is deliberately locked to the Chrome profile supplied by the user.
CHROME_PROFILE = Path(
    r"C:\Users\sandu\AppData\Local\Google\Chrome\User Data\Profile 13"
)
ARCHIVE_DIRECTORY = Path(r"C:\ChromePermanentHistory\Profile13")
ARCHIVE_DATABASE = ARCHIVE_DIRECTORY / "PermanentChromeHistory.sqlite"
CSV_EXPORT = ARCHIVE_DIRECTORY / "PermanentChromeHistory.csv"
LOG_FILE = ARCHIVE_DIRECTORY / "collector.log"
PAUSE_REQUEST = ARCHIVE_DIRECTORY / ".collector-paused"
SCAN_REQUEST = ARCHIVE_DIRECTORY / ".collector-scan-now"
STOP_REQUEST = ARCHIVE_DIRECTORY / ".collector-stop"

CHECK_INTERVAL_SECONDS = 20
# A full reconciliation catches late-arriving synced visits whose timestamps are
# older than the most recently seen local visit.
FULL_RECONCILIATION_SECONDS = 60 * 60
SNAPSHOT_RETRIES = 8
SNAPSHOT_RETRY_DELAY_SECONDS = 1
MUTEX_NAME = r"Local\ChromePermanentHistory-Profile13"

# Exact URLs in this list remain in normal Chrome history but are intentionally
# not copied into the permanent archive.
EXCLUDED_URLS = frozenset(
    {
        "https://myactivity.google.com/myactivity?hl=en&utm_source=google-account&utm_medium=web",
    }
)

CHROME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
SRI_LANKA_TIMEZONE = timezone(timedelta(hours=5, minutes=30), name="Asia/Colombo")
STOP_REQUESTED = False
MAX_LOG_BYTES = 1 * 1024 * 1024
LOG_BACKUP_COUNT = 2


class CollectorError(RuntimeError):
    """Raised when a safe, read-only collection attempt cannot complete."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def configure_logging() -> None:
    ARCHIVE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    # Do not preserve an oversized legacy log as a rotation backup. It contains
    # only routine collector status, not archive data.
    if LOG_FILE.is_file() and LOG_FILE.stat().st_size > MAX_LOG_BYTES:
        LOG_FILE.unlink()
    logging.basicConfig(
        handlers=[
            RotatingFileHandler(
                LOG_FILE,
                maxBytes=MAX_LOG_BYTES,
                backupCount=LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
        ],
        level=logging.INFO,
        format="%(asctime)sZ %(levelname)s %(message)s",
    )


def write_status(message: str, *, error: bool = False) -> None:
    print(message, flush=True)
    if error:
        logging.error(message)
    else:
        logging.info(message)


def chrome_time_to_iso(chrome_timestamp: int | None) -> str | None:
    if not chrome_timestamp:
        return None

    try:
        return (CHROME_EPOCH + timedelta(microseconds=int(chrome_timestamp))).isoformat()
    except (OverflowError, TypeError, ValueError):
        return None


def utc_iso_to_sri_lanka(utc_iso: str | None) -> str | None:
    """Convert an ISO UTC archive timestamp to an ISO Sri Lanka timestamp."""

    if not utc_iso:
        return None
    try:
        parsed = datetime.fromisoformat(utc_iso)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(SRI_LANKA_TIMEZONE).isoformat()
    except ValueError:
        return utc_iso


def extract_domain(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except ValueError:
        return ""


def database_uri(database_path: Path) -> str:
    return database_path.resolve().as_uri() + "?mode=ro"


def initialise_archive() -> sqlite3.Connection:
    ARCHIVE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(ARCHIVE_DATABASE, timeout=30)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS permanent_visits (
            archive_id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            title TEXT,
            domain TEXT,
            chrome_visit_time INTEGER NOT NULL,
            visited_at_utc TEXT,
            transition INTEGER,
            source_visit_id INTEGER,
            visit_duration INTEGER,
            opener_visit INTEGER,
            first_archived_at_utc TEXT NOT NULL,
            UNIQUE (url, chrome_visit_time, transition)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS archive_metadata (
            setting TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_permanent_visits_time
        ON permanent_visits(chrome_visit_time DESC)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_permanent_visits_domain
        ON permanent_visits(domain)
        """
    )

    # The collector itself can only add visit rows. These triggers prevent
    # accidental UPDATE or DELETE commands against the permanent archive.
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS permanent_visits_no_update
        BEFORE UPDATE ON permanent_visits
        BEGIN
            SELECT RAISE(ABORT, 'permanent_visits is append-only');
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS permanent_visits_no_delete
        BEFORE DELETE ON permanent_visits
        BEGIN
            SELECT RAISE(ABORT, 'permanent_visits is append-only');
        END
        """
    )
    set_metadata(connection, "chrome_profile_path", str(CHROME_PROFILE))
    set_metadata(connection, "archive_format", "1")
    connection.commit()
    return connection


def get_metadata(connection: sqlite3.Connection, setting: str) -> str | None:
    row = connection.execute(
        "SELECT value FROM archive_metadata WHERE setting = ?", (setting,)
    ).fetchone()
    return None if row is None else str(row[0])


def set_metadata(connection: sqlite3.Connection, setting: str, value: str) -> None:
    connection.execute(
        """
        INSERT INTO archive_metadata(setting, value)
        VALUES (?, ?)
        ON CONFLICT(setting) DO UPDATE SET value = excluded.value
        """,
        (setting, value),
    )


def get_metadata_int(connection: sqlite3.Connection, setting: str) -> int | None:
    value = get_metadata(connection, setting)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def get_metadata_float(connection: sqlite3.Connection, setting: str) -> float | None:
    value = get_metadata(connection, setting)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def copy_history_snapshot() -> tuple[Path, Path]:
    """Copy the live database and any SQLite sidecars to a temporary folder.

    Windows permits this non-mutating copy while Chrome has the file open. The
    collector reads the copy, so it never takes a write lock on Chrome's data.
    """

    source_history = CHROME_PROFILE / "History"
    if not source_history.is_file():
        raise FileNotFoundError(
            f"Chrome's History database was not found: {source_history}"
        )

    snapshot_directory = Path(tempfile.mkdtemp(prefix="chrome-history-profile13-"))
    snapshot_history = snapshot_directory / "History"

    try:
        shutil.copy2(source_history, snapshot_history)
        for suffix in ("-wal", "-shm", "-journal"):
            source_sidecar = Path(f"{source_history}{suffix}")
            if source_sidecar.is_file():
                try:
                    shutil.copy2(source_sidecar, Path(f"{snapshot_history}{suffix}"))
                except FileNotFoundError:
                    # Chrome can rotate a sidecar immediately after this check.
                    pass
        return snapshot_directory, snapshot_history
    except Exception:
        shutil.rmtree(snapshot_directory, ignore_errors=True)
        raise


def cleanup_abandoned_snapshots() -> None:
    """Remove only this collector's stale snapshots after it owns the mutex."""

    temporary_root = Path(tempfile.gettempdir()).resolve()
    for snapshot_directory in temporary_root.glob("chrome-history-profile13-*"):
        try:
            if not snapshot_directory.is_dir():
                continue
            # Defend against a path escaping the system temp directory.
            snapshot_directory.resolve().relative_to(temporary_root)
            shutil.rmtree(snapshot_directory)
        except OSError as error:
            logging.warning("Could not remove stale snapshot %s: %s", snapshot_directory, error)


def source_query(visit_columns: set[str], full_scan: bool) -> str:
    duration_column = (
        "visits.visit_duration AS visit_duration"
        if "visit_duration" in visit_columns
        else "NULL AS visit_duration"
    )
    opener_column = (
        "visits.opener_visit AS opener_visit"
        if "opener_visit" in visit_columns
        else "NULL AS opener_visit"
    )
    incremental_filter = "" if full_scan else "AND visits.id > ?"

    return f"""
        SELECT
            visits.id AS source_visit_id,
            urls.url AS url,
            urls.title AS title,
            visits.visit_time AS visit_time,
            visits.transition AS transition,
            {duration_column},
            {opener_column}
        FROM visits
        INNER JOIN urls ON urls.id = visits.url
        WHERE urls.url IS NOT NULL
        {incremental_filter}
        ORDER BY visits.id ASC
    """


def archive_rows(
    archive_connection: sqlite3.Connection,
    source_connection: sqlite3.Connection,
    full_scan: bool,
    last_source_visit_id: int | None,
) -> tuple[int, int]:
    visit_columns = {
        str(row[1]) for row in source_connection.execute("PRAGMA table_info(visits)")
    }
    required_columns = {"id", "url", "visit_time", "transition"}
    missing_columns = required_columns - visit_columns
    if missing_columns:
        raise CollectorError(
            "The Chrome history schema is missing required columns: "
            + ", ".join(sorted(missing_columns))
        )

    source_max_visit_id = int(
        source_connection.execute("SELECT COALESCE(MAX(id), 0) FROM visits").fetchone()[0]
    )
    if last_source_visit_id is not None and source_max_visit_id < last_source_visit_id:
        # Chrome history was cleared or replaced. Scan its remaining entries and
        # reset the cursor; already archived entries stay untouched.
        full_scan = True

    query = source_query(visit_columns, full_scan)
    parameters: tuple[int, ...] = () if full_scan else (last_source_visit_id or 0,)
    cursor = source_connection.execute(query, parameters)
    archived_at = utc_now_iso()
    inserted = 0

    for row in cursor:
        if row["url"] in EXCLUDED_URLS:
            continue
        before = archive_connection.total_changes
        archive_connection.execute(
            """
            INSERT OR IGNORE INTO permanent_visits (
                url,
                title,
                domain,
                chrome_visit_time,
                visited_at_utc,
                transition,
                source_visit_id,
                visit_duration,
                opener_visit,
                first_archived_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["url"],
                row["title"],
                extract_domain(row["url"]),
                row["visit_time"],
                chrome_time_to_iso(row["visit_time"]),
                row["transition"],
                row["source_visit_id"],
                row["visit_duration"],
                row["opener_visit"],
                archived_at,
            ),
        )
        if archive_connection.total_changes > before:
            inserted += 1

    set_metadata(archive_connection, "last_source_visit_id", str(source_max_visit_id))
    if full_scan:
        set_metadata(
            archive_connection, "last_full_reconciliation_unix", str(time.time())
        )
    archive_connection.commit()
    return inserted, source_max_visit_id


def import_history(archive_connection: sqlite3.Connection) -> tuple[int, bool]:
    """Archive one consistent, read-only snapshot. Returns (inserted, full_scan)."""

    last_source_visit_id = get_metadata_int(
        archive_connection, "last_source_visit_id"
    )
    last_full_reconciliation = get_metadata_float(
        archive_connection, "last_full_reconciliation_unix"
    )
    full_scan = (
        last_source_visit_id is None
        or last_full_reconciliation is None
        or time.time() - last_full_reconciliation >= FULL_RECONCILIATION_SECONDS
    )
    last_error: Exception | None = None

    for attempt in range(1, SNAPSHOT_RETRIES + 1):
        snapshot_directory: Path | None = None
        source_connection: sqlite3.Connection | None = None
        try:
            snapshot_directory, snapshot_history = copy_history_snapshot()
            source_connection = sqlite3.connect(
                database_uri(snapshot_history),
                uri=True,
                timeout=10,
            )
            source_connection.row_factory = sqlite3.Row
            inserted, _ = archive_rows(
                archive_connection,
                source_connection,
                full_scan,
                last_source_visit_id,
            )
            return inserted, full_scan
        except (OSError, sqlite3.Error, CollectorError) as error:
            last_error = error
            if attempt < SNAPSHOT_RETRIES:
                time.sleep(SNAPSHOT_RETRY_DELAY_SECONDS)
        finally:
            # sqlite3's context manager commits or rolls back but does not close
            # the connection. Windows otherwise keeps History open and prevents
            # removal of the temporary snapshot directory on every scan.
            if source_connection is not None:
                source_connection.close()
            if snapshot_directory is not None:
                shutil.rmtree(snapshot_directory, ignore_errors=True)

    raise CollectorError(
        f"Could not read a stable History snapshot after {SNAPSHOT_RETRIES} attempts: "
        f"{last_error}"
    )


def export_csv(archive_connection: sqlite3.Connection) -> None:
    temporary_csv = CSV_EXPORT.with_name(f"{CSV_EXPORT.name}.tmp")
    rows = archive_connection.execute(
        """
        SELECT
            visited_at_utc,
            title,
            url,
            domain,
            first_archived_at_utc
        FROM permanent_visits
        ORDER BY chrome_visit_time DESC, archive_id DESC
        """
    )

    with temporary_csv.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "Visited at Sri Lanka time (UTC+05:30)",
                "Title",
                "URL",
                "Domain",
                "First archived at Sri Lanka time (UTC+05:30)",
            ]
        )
        for visited_at_utc, title, url, domain, archived_at_utc in rows:
            writer.writerow(
                [
                    utc_iso_to_sri_lanka(visited_at_utc),
                    title,
                    url,
                    domain,
                    utc_iso_to_sri_lanka(archived_at_utc),
                ]
            )
    os.replace(temporary_csv, CSV_EXPORT)


def archive_count(archive_connection: sqlite3.Connection) -> int:
    return int(
        archive_connection.execute("SELECT COUNT(*) FROM permanent_visits").fetchone()[0]
    )


class SingleInstance:
    """Windows named mutex so Task Scheduler and the launcher cannot duplicate work."""

    ERROR_ALREADY_EXISTS = 183

    def __init__(self, name: str) -> None:
        self.handle = ctypes.windll.kernel32.CreateMutexW(None, False, name)
        if not self.handle:
            raise ctypes.WinError()
        self.already_running = ctypes.GetLastError() == self.ERROR_ALREADY_EXISTS

    def close(self) -> None:
        if self.handle:
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None


def collect_once(archive_connection: sqlite3.Connection) -> int:
    inserted, full_scan = import_history(archive_connection)
    if inserted or full_scan or not CSV_EXPORT.exists():
        export_csv(archive_connection)
    total = archive_count(archive_connection)
    mode = "full reconciliation" if full_scan else "incremental scan"
    # Avoid writing a line every 20 seconds when nothing changed. Full scans
    # remain visible in the operational log and ordinary scans are logged when
    # they archive new visits.
    if inserted or full_scan:
        write_status(f"{mode}: archived {inserted} visit(s); permanent total: {total}")
    return inserted


def run_once() -> int:
    configure_logging()
    if not CHROME_PROFILE.is_dir():
        write_status(f"ERROR: Chrome profile not found: {CHROME_PROFILE}", error=True)
        return 1

    archive_connection = initialise_archive()
    try:
        collect_once(archive_connection)
        return 0
    except Exception as error:
        write_status(f"ERROR: {type(error).__name__}: {error}", error=True)
        return 2
    finally:
        archive_connection.close()


def refresh_csv_only() -> int:
    """Regenerate the readable CSV without reading or changing Chrome data."""

    configure_logging()
    archive_connection = initialise_archive()
    try:
        export_csv(archive_connection)
        write_status(
            f"CSV refreshed in Sri Lanka time; permanent total: "
            f"{archive_count(archive_connection)}"
        )
        return 0
    except Exception as error:
        write_status(f"ERROR: {type(error).__name__}: {error}", error=True)
        return 2
    finally:
        archive_connection.close()


def run_forever() -> int:
    configure_logging()
    if not CHROME_PROFILE.is_dir():
        write_status(f"ERROR: Chrome profile not found: {CHROME_PROFILE}", error=True)
        return 1

    mutex = SingleInstance(MUTEX_NAME)
    if mutex.already_running:
        write_status("Collector is already running; this second instance will exit.")
        mutex.close()
        return 0

    cleanup_abandoned_snapshots()
    archive_connection = initialise_archive()
    write_status(
        "Permanent Chrome History collector started for Profile 13 "
        f"(checks every {CHECK_INTERVAL_SECONDS} seconds)."
    )
    pause_announced = False
    try:
        while not STOP_REQUESTED:
            if STOP_REQUEST.is_file():
                STOP_REQUEST.unlink(missing_ok=True)
                write_status("Graceful stop requested from the local dashboard.")
                break
            if PAUSE_REQUEST.is_file():
                if not pause_announced:
                    write_status("Collector paused from the local dashboard; archive remains available.")
                    pause_announced = True
                time.sleep(1)
                continue
            if pause_announced:
                write_status("Collector resumed from the local dashboard.")
                pause_announced = False
            SCAN_REQUEST.unlink(missing_ok=True)
            try:
                collect_once(archive_connection)
            except Exception as error:
                write_status(
                    f"Collection attempt failed: {type(error).__name__}: {error}",
                    error=True,
                )
            for _ in range(CHECK_INTERVAL_SECONDS):
                if STOP_REQUESTED or STOP_REQUEST.is_file() or PAUSE_REQUEST.is_file():
                    break
                if SCAN_REQUEST.is_file():
                    SCAN_REQUEST.unlink(missing_ok=True)
                    break
                time.sleep(1)
    finally:
        try:
            export_csv(archive_connection)
        except Exception as error:
            write_status(f"Final CSV export failed: {error}", error=True)
        archive_connection.close()
        mutex.close()
        write_status("Permanent Chrome History collector stopped.")
    return 0


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Append-only archive collector for Chrome Profile 13."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Take one read-only snapshot, archive it, update the CSV, then exit.",
    )
    parser.add_argument(
        "--export-csv",
        action="store_true",
        help="Regenerate the readable CSV from the permanent archive, then exit.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_arguments()
    if arguments.export_csv:
        sys.exit(refresh_csv_only())
    sys.exit(run_once() if arguments.once else run_forever())
