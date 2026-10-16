# Shared archive project status

Historical implementation notes, retained for reference. During the 2026-10-17
workspace cleanup, older backup folders and completed imports were moved outside
the project. Their original paths below describe where they existed at the time.
See the [cleanup record](../../README.md#workspace-cleanup--2026-10-17) for recovery
locations. References to RELAY.md and backend/ belong to the separate Android
project; this workspace's current desktop guide is [SHARED_SYNC_SETUP.md](SHARED_SYNC_SETUP.md).

## Current mobile format — 0.5.0

Migration 006 and the desktop reader now support full observed mobile HTTP(S) browsing records:
URL with parameters/fragments, exact observed text, available title metadata, original timestamp,
tab mode and provenance. Desktop uploads remain disabled. Cloud records are consumed only after
full local verification. The correct account remains jenukivinupama@gmail.com.
New phone capture is a separate all-browsing opt-in; earlier encrypted records are preserved.
Install DataSync-0.5.0-preview.apk and follow RELAY.md. Real-phone capture remains to be verified.
34 desktop and 30 Android JVM tests passed. No original local history records were removed.

## Current privacy mode — 0.4.0, 2026-10-16

The PC is permanent; Supabase is a temporary mobile relay. Desktop uploads are blocked both
in the worker and by server policy. The PC commits and verifies full records before consuming
cloud rows. Android 0.4.0 uploads searches and keeps encrypted local copies; no cloud mirroring.
Desktop owner remains jenukivinupama@gmail.com. All 6,378 original records remain untouched.
Both the original database and relay cache have verified pre-cutover SQLite backups.
The wrong-account cloud copies were verified against the retained local backup and deleted.
Final live verification: search_history = 0 rows, browser_history = 0 rows across both accounts.
The correct-owner worker verified and consumed 8,931 rows (2,579 searches, 6,352 web visits).
All those records remain permanently in SharedArchive.sqlite; no local row was removed.
See RELAY.md for the current flow and installation instructions.

Duplicate grouping is installed in the dashboard: unique cross-source engine/query matches
within 2 seconds are displayed once, with all raw observations retained. Ambiguous matches
are flagged, not grouped. The all-observations view and exports remain available. 30 desktop
tests passed; no Android capture rows are present in the current archive yet.

## Earlier setup history (superseded where it describes cloud permanence)

Verified 2026-10-15:

- Project: chrome-shared-archive (`xipjyocdunopwovhsolk`), Astryx Global, Mumbai.
- Organization remains on the Free plan; no paid upgrade was selected.
- Shared search and desktop visit schema installed; both tables have owner-only RLS.
- Anonymous SELECT and authenticated UPDATE/DELETE are denied.
- All four archive RPCs use invoker security. Authenticated empty-page reads passed.
- Restricted the dashboard-generated `public.rls_auto_enable()` event-trigger function
  by revoking direct execution from PUBLIC, anon and authenticated. Latest security advisor: no findings.
- Desktop connection saved with the public publishable key and full web-history uploads enabled.
- Initial login used the wrong Google account and uploaded desktop records under that owner.
  It was signed out on 2026-10-16. Existing cloud rows were preserved, not deleted.
- Its local sync state was moved to account-backup-sandun-20261016-001836 in Profile13.
  The original PermanentChromeHistory.sqlite remains unchanged.
- A fresh sync sidecar requires jenukivinupama@gmail.com and rejects other email identities
  before binding or uploading. Correct-account login succeeded on 2026-10-16; uploads verified.
- All 6,378 original local history records remain present. A SQLite backup with the same count
  passed quick_check: Profile13/backups/PermanentChromeHistory-preserved-20261016-002357.sqlite.
- Desktop suite now has 16 passing tests, including expected-account rejection.
- Both exact desktop/Android redirect addresses were saved and verified in Supabase.
- Google Web OAuth client DataSync Supabase Login was created and verified.
- Supabase Google provider is enabled and the OAuth redirect was verified through login.
- Google Auth Platform branding created as DataSync Shared Archive in the existing
  Google Cloud project silver-idea-425005-f0, with an external testing audience.
- Exact project URL, public key and callback addresses are in PROJECT_CONNECTION.json.

Next: install Android 0.3.0 Preview, configure the same project/public key, and sign in
as jenukivinupama@gmail.com. Android installation and live phone capture remain pending.
Never put the OAuth client secret in either client or this repository.
