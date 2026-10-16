#!/usr/bin/env python3
"""One-time, guarded removal of the explicitly excluded My Activity URL."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


ARCHIVE_DATABASE = Path(r"C:\ChromePermanentHistory\Profile13\PermanentChromeHistory.sqlite")
EXCLUDED_URL = (
    "https://myactivity.google.com/myactivity?"
    "hl=en&utm_source=google-account&utm_medium=web"
)
EXPECTED_ROWS = 43


def main() -> int:
    with sqlite3.connect(ARCHIVE_DATABASE) as connection:
        matching = connection.execute(
            "SELECT COUNT(*) FROM permanent_visits WHERE url = ?",
            (EXCLUDED_URL,),
        ).fetchone()[0]
        if matching != EXPECTED_ROWS:
            print(
                f"Cancelled: expected exactly {EXPECTED_ROWS} matching rows; "
                f"found {matching}."
            )
            return 1

        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DROP TRIGGER permanent_visits_no_delete")
            deleted = connection.execute(
                "DELETE FROM permanent_visits WHERE url = ?",
                (EXCLUDED_URL,),
            ).rowcount
            if deleted != EXPECTED_ROWS:
                raise RuntimeError(
                    f"Expected to remove {EXPECTED_ROWS} rows, but removed {deleted}."
                )
            connection.execute(
                """
                CREATE TRIGGER permanent_visits_no_delete
                BEFORE DELETE ON permanent_visits
                BEGIN
                    SELECT RAISE(ABORT, 'permanent_visits is append-only');
                END
                """
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

        remaining = connection.execute(
            "SELECT COUNT(*) FROM permanent_visits WHERE url = ?",
            (EXCLUDED_URL,),
        ).fetchone()[0]
        print(f"Removed: {deleted}")
        print(f"Matching rows remaining: {remaining}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
