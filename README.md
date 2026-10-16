# Permanent Chrome History

Windows Chrome Profile 13 history collector, local analytics dashboard, and
Android-to-PC history relay. The application and its live data remain in
`Profile13/` so existing Startup entries and launchers continue to work.

See [daily use and architecture](Profile13/README.md),
[mobile relay setup](Profile13/docs/SHARED_SYNC_SETUP.md), and
[database setup SQL](Profile13/docs/SETUP_SHARED.sql).

## Source control

Git tracks application code, launchers, SQL, and documentation. Databases,
SQLite sidecars, CSV exports, logs, credentials, private installation configuration,
classification rules, caches, and historical browsing imports are ignored.
Git is a source backup; it does not back up your browsing history.

## Workspace cleanup — 2026-10-17

Before cleanup, the full workspace was copied outside this repository to:

`C:\ChromePermanentHistory-backups\cleanup-20261017-015244\original-workspace`

Separate consistent SQLite backups are in the sibling `verified-databases/`
directory. Both passed full SQLite integrity checks. `manifest.json` records
table counts and backup SHA-256 hashes. At backup time, the desktop archive
contained 6,448 visits; the relay archive contained 8,931 remote records and
8,931 legacy outbox records.

Old code snapshots, previous account/database backups, completed manual-import
artifacts, one-time maintenance scripts, setup screenshots, and Python bytecode
were moved to the sibling `archived-items/` directory. Nothing from that group
was permanently deleted. The one-time import script contains real browsing data
and was deliberately excluded from Git.

Live databases, sidecars, CSV, credentials, connection configuration, logs, runtime
cache, application modules, and launchers stay in their original locations.
The collector, relay, and dashboard were left running. No application behavior
was changed during this cleanup.

These backups are on the same drive. Keep an additional copy on another drive
if you need protection against drive failure. For future backups while services
are running, use SQLite's backup API rather than copying database files alone.
