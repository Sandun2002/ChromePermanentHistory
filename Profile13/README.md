# Permanent Chrome History Archive — Profile 13

This folder independently archives every Chrome history visit that reaches
Chrome profile C:\Users\sandu\AppData\Local\Google\Chrome\User Data\Profile 13.

The collector only reads temporary snapshots of Chrome's History database. It
does not modify Chrome history. Once a visit has been copied into
PermanentChromeHistory.sqlite, later history deletion or sync deletion in
Chrome does not remove the archived row.

## Files

- PermanentChromeHistory.sqlite — main append-only archive.
- PermanentChromeHistory.csv — readable export in Sri Lanka time (UTC+05:30),
  refreshed after collection. The SQLite archive retains UTC timestamps.
- collector.log — private operational log; it does not list visited URLs.
- history_collector.py — collector, configured only for Profile 13.
- open_profile13.cmd — launcher that starts the collector and opens Profile 13.
- install_task.ps1 — repairs or reinstalls the Windows sign-in task.
- dashboard_server.py — private, local analytics dashboard server.
- open_dashboard.cmd — opens the analytics dashboard in your browser.
- check_collector_status.cmd — shows whether the background collector is running.

## Daily use

Use open_profile13.cmd to open Chrome Profile 13. The collector also starts
automatically at every Windows sign-in through the current user's Startup
folder. Windows Task Scheduler was unavailable to this user during setup.

## Analytics dashboard

Double-click open_dashboard.cmd. It opens the private dashboard at
http://127.0.0.1:8765. It offers date filtering, title or URL search, visit
totals, daily activity, top websites, detected search-query analysis,
time-of-day and weekday patterns, a paginated visit list, and a filtered CSV
export.

Use the From date and From time fields together with the To date and To time
fields to analyze a precise Sri Lanka-time period. Every dashboard section and
CSV export follows the same selected period.

The To time is exclusive. For example, 31 Jul 2026 14:23 to 31 Jul 2026 14:24
shows only events from that one minute. The dashboard opens in white mode and
has a Black mode toggle in the top-right corner.

Archived visits display 100 rows per page. The dashboard also includes a
private Adult / NSFW search-insights section. It is a conservative keyword
match of search URLs, intended as a review aid rather than a perfect classifier.

The dashboard remains local to this PC. Optional shared sync uploads supported
searches and full desktop HTTP(S) browsing URLs to your configured private
Supabase project after Google sign-in. Original local records remain independent.

At the top of the dashboard, the Collector Status panel shows whether the
background collector is running, its latest update, archive availability, and
whether automatic start is installed. The separate check_collector_status.cmd
file remains available for a quick status window.

The same panel also provides local controls: Check now, Start, Pause/Resume,
Restart, Open archive folder, recent operational log, and a guarded Stop
collector action under Advanced controls. Pause keeps the collector process
available but suspends scans. Resume requests an immediate scan. Stop is
temporary; automatic collection starts again at the next Windows sign-in.

To manually run one read-only import, run:

  C:\Users\sandu\AppData\Local\Programs\Python\Python312\python.exe C:\ChromePermanentHistory\Profile13\history_collector.py --once

## Excluded URL

Future visits to the exact My Activity URL below remain in ordinary Chrome
history but are not copied into this permanent archive:

  https://myactivity.google.com/myactivity?hl=en&utm_source=google-account&utm_medium=web

## Important limitations

- The archive can save only visits that reach this desktop profile. If this PC
  is offline, a visit occurs on another device, and that visit is deleted before
  Chrome sync sends it here, no desktop collector can archive it.
- Incognito browsing does not enter ordinary Chrome history, so it is not
  collected.
- The SQLite archive and CSV contain sensitive browsing data. Keep this folder
  private, ideally on an encrypted drive, and back it up elsewhere. For a
  consistent file-copy backup, stop the running collector first, then copy the
  whole Profile13 folder.


## Shared desktop + Android archive (0.3.0)

**Superseded by 0.4.0 temporary relay mode.** The PC now permanently retains history and
never uploads desktop records. Mobile searches are deleted from Supabase only after durable
local saving and exact verification. See [the current guide](SHARED_SYNC_SETUP.md).
Back up both PermanentChromeHistory.sqlite and SharedArchive.sqlite.

Shared sync is installed but not connected until one-time Supabase/Google setup
and sign-in on both devices. Expand Shared desktop + Android archive in the
dashboard. Use the same project URL, public key, and Google account as Android.
The original collector and its archive are preserved. SharedArchive.sqlite is a
separate queue/cache; .cloud-sync.dpapi protects Windows credentials. A separate
Windows Startup entry runs the sync daemon even when the dashboard is closed.

Follow [the setup guide](SHARED_SYNC_SETUP.md). The ready-to-run new-project SQL
is [SETUP_SHARED.sql](SETUP_SHARED.sql). For a consistent whole-folder backup,
stop the collector, dashboard, and sync daemon, or use SQLite backup APIs.
Android 0.3.0 can download shared searches and desktop web visits; Android
automatic capture still depends on exposed search URLs and tab-mode options.
