# PC permanent archive, Supabase temporary relay (0.5.0)

The desktop is the permanent archive. Desktop browsing URLs and searches are never uploaded
by the relay worker. Supabase temporarily queues Android browsing records for delivery to the PC.
The phone keeps its own encrypted local copies and does not download desktop history.

## Delivery and deletion

1. Android captures an observed HTTP(S) browsing record locally, then uploads it over HTTPS when signed in.
2. The PC reads relay records belonging to the same account.
3. It validates each record and commits it to `SharedArchive.sqlite` with SQLite synchronous FULL.
4. It opens a separate connection and verifies every saved payload field.
5. Only then it calls the owner-scoped, exact-payload consumption RPC to delete the cloud rows.

Local disk/write/validation failures leave the cloud records untouched. Network/delete failures
retain the permanent local copy and retry safely. A lost deletion acknowledgment cannot erase
the local copy. Every run reads the queue head, so old timestamps and late arrivals are not skipped.
Existing local records and legacy outbox/cache copies are retained. No local archive table is cleared.

If the PC is offline, records remain in Supabase until it receives them. There is no automatic
time-based purge that could discard undelivered records. Android force-stop, battery policies,
network loss and PC sleep can delay delivery. There is no instant/every-search guarantee.

The cloud is still trusted with readable browsing URLs/titles while records are waiting. This is not
end-to-end encryption. Deleting live rows is not a guarantee that provider logs, backups, WAL
or storage remnants are immediately erased. For zero cloud exposure, use a direct private
phone-to-PC transport instead; that is not implemented in this version.

At cutover on 2026-10-16, both live cloud history tables were verified empty after all earlier
records had been retained and checked on the PC, including the initial wrong-account upload.

## This installation

- Archive account: **jenukivinupama@gmail.com** on desktop and Android DataSync.
- Project URL and public publishable key: [PROJECT_CONNECTION.json](PROJECT_CONNECTION.json).
- Desktop original database: `C:\ChromePermanentHistory\Profile13\PermanentChromeHistory.sqlite`.
- Permanent mobile/legacy cloud copies: `C:\ChromePermanentHistory\Profile13\SharedArchive.sqlite`.
- Dashboard: `http://127.0.0.1:8765/`. The Startup relay worker runs when Windows is signed in.
- Install [Android 0.5.0 preview](E:/DataSync/downloads/DataSync-0.5.0-preview.apk), Android 15+.
  It updates earlier previews in place with the same signing certificate; original 0.1.0 is separate.
- In Android, enter the project URL/public key, sign into the archive account, enable capture
  consent, **Save ALL exposed Chrome browsing URLs and available titles**, accessibility,
  then recording. Both tab-mode opt-ins are needed for exposed Incognito browsing.
  All browsing is a new opt-in and defaults off, including on upgrade. Without it, only
  supported search URLs are recorded, but their observed full URL is now retained.
  Hidden URLs cannot be captured.

## Full mobile browsing capture

Version 0.5.0 records non-search sites as well as search pages, preserving parameters and fragments.
It prefers the active web document URL exposed by Chromium in accessibility root extras["url"].
If unavailable, it records a labelled address-bar fallback; missing schemes are inferred as HTTPS
and explicitly marked, while the exact observed text is also retained. Bare typed queries and
focused/unsubmitted omnibox text are never treated as browsing URLs.

Records preserve URL, observed URL, domain, available title metadata and its source, phone
observation timestamp, normal/Incognito/unknown tab mode, stable record UUID and device UUID.
Only pane/window title metadata is considered; no page-body/form text is scraped to invent titles.
Missing titles remain empty. Transient toolbar/document host disagreement is skipped and counted.

This does not access Chrome's protected History database. It cannot recover visits before
installation, hidden URLs, internal/file pages, background tabs not exposed by Chrome, or
Chrome-only fields such as visit duration, transition type and originator visit IDs.
URLs longer than 32,768 characters are not captured; title metadata is limited to 4,096 characters.
Fast navigation and Chrome/OEM changes can cause gaps; exact desktop-equivalent completeness
has not been verified on a real phone. Actual URL/title availability needs device testing.

The existing encrypted search queue and previously downloaded visits are preserved through
database versions 1/2 → 3. New encrypted mobile visits use a separate table and are retained
after cloud acknowledgment. Explicit Chrome sharing now accepts any complete HTTP(S) URL.
JSONL and desktop CSV include the full record and provenance fields.

Back up BOTH PC databases using SQLite's backup API. A phone reinstall cannot restore delivered
history from the relay after consumption; the permanent copy is on the PC. Do not uninstall an
older phone installation containing unsynced records. Old Android encrypted copies remain readable.

## Duplicate observations

Chrome history sync may deliver a mobile visit into Profile 13 while DataSync independently
captures it. The desktop dashboard groups unique desktop/automatic-Android matches within
2 seconds of their original observation times. New document-URL records also compare the
full normalized URL, preserving meaningful search-mode, filter and pagination differences.
Non-search browsing can be paired by full URL. Known Incognito records are never grouped
into ordinary desktop history. For older search-only records, engine and decoded query are used.
Common tracking parameters and supported host aliases do not create separate legacy query keys. Query case
is preserved, with Unicode NFC normalization. Cloud arrival/download time is never used.

Both sides must have only one candidate; repeated or ambiguous observations remain separate
and are flagged. Explicit shares are not paired, since sharing may happen much later. With older
search-only counterparts, desktop searches with image/video/filter/pagination parameters are flagged but not grouped because
historical Android records lack that search-mode metadata. Device clock drift can prevent a match.
Two independent searches within two seconds can still look identical; this is a likely-match
heuristic, not proof or permission to erase raw records.

Choose **Duplicate observations → Show all original observations** to inspect/export both records.
Grouped rows are labelled **Profile 13 + Android · likely duplicate grouped**; their desktop URL
and timestamp are representative. Grouped observations appear under both desktop and Android
source filters. Charts, counts, sessions and CSV exports use the selected duplicate view; CSV
includes duplicate status and observation count. All original rows remain in the two local databases.
Future mobile imports are evaluated automatically. Relay deletion still follows durable saving,
independently of whether a possible duplicate is grouped.

## Deploying to another project

New projects: run `backend/SETUP_SHARED.sql` once. Existing projects: apply
`backend/005_temporary_relay.sql` after 001–004, then `006_mobile_browsing.sql`. This blocks desktop uploads on the server,
retains owner-only RLS, and adds the consume endpoint. It does not erase any existing data by itself.
Existing cloud rows are consumed by the PC only after local persistence and verification.

Keep Google OAuth and the exact two app callback addresses configured as before. The Google
client secret stays in Supabase. Use only a public publishable key in the phone and desktop.

Implementation references: [Chromium Android accessibility URL metadata](https://chromium.googlesource.com/chromium/src/+/main/content/public/android/java/src/org/chromium/content/browser/accessibility/WebContentsAccessibilityImpl.java),
[Android window title metadata](https://developer.android.com/reference/android/view/accessibility/AccessibilityWindowInfo#getTitle()).
