# Design Document: AssembleSports Schedule Tracker

> **Version:** 1.2
> **Status:** Living document -- update this file when design decisions change.

---

## 1. Purpose

A GitHub Actions automation that monitors an [AssembleSports](https://assemblesports.io)-hosted
team's schedule, syncs matches to Google Calendar, tracks competition ladder standings, and posts
change notifications to a Discord channel. Designed to run unattended on a cron schedule with no
manual intervention between seasons beyond updating a team ID and cache key.

---

## 2. System Architecture

### Data Flow

```
AssembleSports API
    |
    |  HTTP GET (polling, hourly on game days)
    v
check_schedule_with_ladder.py
    |
    +---> Google Calendar API     (create / update / delete match events)
    +---> Discord Webhook         (schedule changes + ladder embed)
    +---> GitHub Actions Cache    (state persistence -- 4 JSON files)
```

### Workflow Triggers

| Trigger | When | Notes |
|---------|------|-------|
| `schedule` (cron) | Hourly Thu-Sun, 05:00-11:00 UTC | Primary trigger -- see Section 6 for DST details |
| `workflow_dispatch` | Manually from the Actions tab | For testing and forced runs |
| `push` to `main` | On every commit to main | Useful during setup; remove the push trigger if unwanted |

### Components

| File | Role |
|------|------|
| `check_schedule_with_ladder.py` | Core polling script -- the only file the workflow executes |
| `.github/workflows/schedule-checker-enhanced.yml` | Scheduler, runtime environment, secret injection, state cache |
| `cleanup_duplicates.py` | One-time local maintenance utility -- not invoked by the workflow |
| `debug_match_dates.py` | Debug utility, invoked when `DEBUG_DATES=true` Variable is set |
| `SEASON_SETUP.md` | Operator guide for season rollover |

---

## 3. State Persistence

State is persisted between GitHub Actions runs via `actions/cache`. Four JSON files are saved
after each run and restored at the start of the next:

| File | Purpose |
|------|---------|
| `previous_schedule.json` | Last known team schedule -- used to detect time, venue, and status changes |
| `calendar_events.json` | `match_id -> Google Calendar event_id` mapping -- prevents duplicate event creation |
| `match_history.json` | Completed matches -- used to detect new results for ladder notifications |
| `ladder_standings.json` | Last known ladder -- used to detect position and points changes |

**Why cache instead of committing files?**
Committing state files to the repo would pollute git history with frequent automated commits,
require write permissions on `GITHUB_TOKEN`, and create merge conflicts if a push happens during
a run. Cache is ephemeral by design: if evicted, the next run behaves as a first run --
recreating all calendar events. Duplicate detection prevents double-booking.

Artifacts are saved in parallel as a backup for manual inspection (90-day retention).

**Cache eviction:** GitHub evicts cache entries after 7 days of no access, or when the
repository's total cache usage exceeds 10 GB. Either condition triggers a first-run rebuild.

**Season rollover:**
The cache key includes a season token (`schedule-state-2026-*`). Bumping this prefix at season
start forces a clean slate, necessary because the team may have a new `TEAM_ID` and the old
`calendar_events.json` mapping is invalid. See `SEASON_SETUP.md`.

---

## 4. Configuration

### 4.1 Required -- GitHub Actions Variables

Set in: Settings -> Secrets and variables -> Actions -> Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `TEAM_ID` | Numeric team ID from the AssembleSports team page URL. Changes each season. | `1234567` |
| `TEAM_NAME` | Display name used in calendar event titles and notification messages. | `YourTeam` |
| `CLUB_SLUG` | Club URL slug. Found in `/c/club/<CLUB_SLUG>/` in the live site URL. | `sydneycity` |
| `SEASON_KEY` | Short label for cache/artifact isolation. Bump at season start. | `2026s1` |

The script raises `EnvironmentError` at startup if `TEAM_ID`, `TEAM_NAME`, or `CLUB_SLUG` are
unset. There are no silent defaults -- a misconfigured deployment fails immediately with a clear
message.

### 4.2 Required -- GitHub Actions Secrets

Set in: Settings -> Secrets and variables -> Actions -> Secrets

| Secret | Description |
|--------|-------------|
| `DISCORD_WEBHOOK_URL` | Incoming webhook URL for your Discord channel. |
| `GOOGLE_CREDENTIALS_JSON` | Full JSON content of a Google service account key file. |
| `GOOGLE_CALENDAR_ID` | Calendar ID to sync matches into (`primary` or a calendar email address). |

### 4.3 Optional -- GitHub Actions Variables

Leave unset to use OzTag Australia defaults.

| Variable | Default | Description |
|----------|---------|-------------|
| `ASSEMBLESPORTS_BASE_URL` | `https://api.oztagaustralia.assemblesports.io` | API base URL (scheme + host only). Change if your league is on a different instance. |
| `ASSEMBLESPORTS_LIVE_URL` | `https://live.oztagaustralia.assemblesports.io` | Browser-facing live site URL. Used in HTTP headers and match permalink construction. See Section 5.2. |
| `TIMEZONE` | `Australia/Sydney` | IANA timezone string for match times and calendar events. |
| `DEBUG_DATES` | (unset) | Set to `true` to run `debug_match_dates.py` and log raw API date values. |

### 4.4 Optional -- GitHub Actions Secrets (reserved)

| Secret | Status | Description |
|--------|--------|-------------|
| `FORCE_CALENDAR_SYNC` | Reserved | Injected into the environment but not yet consumed by the script. |
| `SEND_HEARTBEAT` | Reserved | Injected into the environment but not yet consumed by the script. |

---

## 5. Design Decisions

### 5.1 `national-id: "18"` is hardcoded, not configurable

`national-id` is OzTag Australia's static organisation ID within AssembleSports. It is:
- Not a credential
- Not team-specific or season-specific
- Meaningless to configure independently of `ASSEMBLESPORTS_BASE_URL`

Making it an env var would be a footgun: a user could set the wrong value and receive data for
a different organisation with no error. For a different AssembleSports organisation, set
`ASSEMBLESPORTS_BASE_URL` and `ASSEMBLESPORTS_LIVE_URL` instead.

### 5.2 `ASSEMBLESPORTS_LIVE_URL` is a separate env var, not derived from `ASSEMBLESPORTS_BASE_URL`

The API and browser-facing URLs share the same domain but use different subdomains
(`api.oztagaustralia...` vs `live.oztagaustralia...`). Deriving one from the other via string
substitution would be fragile -- other AssembleSports installations may not follow this pattern.
Separate env vars with explicit defaults are self-documenting and safe.

### 5.3 Competition ID is auto-detected from team match data

The script extracts `competition._id` from the team's own match data and constructs the
competition-wide URL from it. This means the competition ID never needs to be manually configured
between seasons -- it is always correct as long as `TEAM_ID` is correct.

Alternative considered: store the competition ID in GitHub Variables. Rejected because it adds an
extra season-rollover step and creates a failure mode where the variable is outdated.

### 5.4 Duplicate calendar event prevention is multi-layered

Calendar event duplication was an operational problem in early versions. Three layers:

1. **Skip-if-exists on creation:** before calling `events.insert`, check `calendar_events.json`.
2. **Deduplicated updates:** collect unique `match_id`s before updating -- prevents multiple API
   calls for the same match when time and venue both change in the same API response.
3. **Live calendar audit:** `check_and_remove_duplicate_calendar_events()` scans the live calendar
   on every run. Events are grouped by a `Match ID: <id>` line written into the description of
   every event the script creates. This tag is the authoritative deduplication key. Events lacking
   this tag (old-format or manually created) are flagged but not auto-deleted -- use
   `cleanup_duplicates.py` to remove them interactively.

### 5.5 Status changes tracked separately from time/venue changes

A match transitioning from `pre-game` to `final` sometimes arrives in the same API response as a
time or venue correction. Processing both independently would update the same calendar event twice.
`compare_schedules()` only adds a match to `status_changes` if neither its time nor venue changed.

**Edge case:** if a match is simultaneously cancelled and has a time/venue correction, the
cancellation is handled via the time/venue branch (event updated, not deleted). This is not
expected in practice but is acknowledged.

### 5.6 State is not committed to the repository

See Section 3. Committing JSON blobs on every run pollutes history and requires write access.
Cache is the appropriate mechanism for ephemeral operational state.

### 5.7 `cleanup_duplicates.py` is intentionally interactive

The script prompts before deleting calendar events that lack a `Match ID:` tag. This safeguard is
intentional -- it is a one-time recovery tool run manually, not a CI step. The `input()` call is
a deliberate gate against accidental data loss.

### 5.8 No retry logic on API calls

The AssembleSports API has not exhibited rate-limiting or transient failures in practice. Adding
retry/backoff logic would add complexity with no observed benefit. If the API is unreachable, the
run fails and the operator sees the error in the Actions log.

### 5.9 Calendar event reminders are hardcoded

Each event is created with two popup reminders: 24 hours before and 1 hour before. These are
sensible defaults for a sports schedule. They are hardcoded to keep setup simple. To change them,
edit `create_calendar_event()` directly.

### 5.10 Notifications are pluggable; only Discord ships

Notification delivery is abstracted behind a `Notifier` base class. The channel is chosen at
runtime by the `NOTIFIER` env var (default `discord`); `build_notifier()` maps the name to an
implementation. Discord (`DiscordNotifier`) is the only channel shipped — a deliberate "one
working default, clean seam for the rest" choice. `none`/`off` selects a `NullNotifier` that logs
to stdout, letting the Calendar sync run without a notification target.

The `send(text, embed)` contract delivers exactly **one** representation. `embed` is a
Discord-style rich payload; channels that support rich formatting render it, all others fall back
to the plain-text `text`. Every notification carries a `text` fallback (the ladder embed is
rendered to text via `embed_to_text()`) so a future text-only channel — SMS, WhatsApp — never
goes silent. Adding a channel is an isolated change: subclass `Notifier`, implement `send()`,
register a branch in `build_notifier()`. The README has a worked Twilio-SMS stub.

Alternative considered: a full multi-channel matrix (Slack, SMS, webhook) up front. Rejected —
more surface to maintain and test for channels no current user needs. The seam makes adding one
cheap when someone does.

---

## 6. Polling Schedule

The workflow runs hourly, Thursday-Sunday, covering **05:00-11:00 UTC** (28 cron entries):

- **AEDT (UTC+11, October-April):** 4 PM - 10 PM Sydney
- **AEST (UTC+10, April-October):** 3 PM -  9 PM Sydney

During AEST the window shifts one hour earlier in Sydney local time. Games still fall within the
window. GitHub Actions does not support timezone-aware cron natively.

---

## 7. Limitations and Known Issues

| Issue | Severity | Notes |
|-------|----------|-------|
| DST shift in cron window | Low | 3-9 PM AEST instead of 4-10 PM AEDT. Within game-day range. |
| No API retry logic | Low | API has been reliable in practice. |
| `FORCE_CALENDAR_SYNC` / `SEND_HEARTBEAT` not consumed by script | Low | Reserved for future use. |
| Cache eviction (7-day gap or 10 GB repo limit) | Low | Duplicate detection prevents double-booking on first run. |
| `cleanup_duplicates.py` requires interactive terminal | Informational | By design -- see Section 5.7. |
| Push-to-main trigger runs outside game-day hours | Informational | Useful during setup; remove if unwanted. |
| Cancellation + simultaneous time/venue correction | Low | Event updated rather than deleted. Not expected in practice. |
| Workflow YAML could not be updated via MCP (missing `workflow` OAuth scope) | **Outstanding** | Requires manual edit. Affects: `DEBUG_DATES` reads from `secrets` not `vars`; `ASSEMBLESPORTS_BASE_URL`, `ASSEMBLESPORTS_LIVE_URL`, `TIMEZONE` not injected into workflow steps. See Section 8 for intended changes. |

---

## 8. Design Decision Log

Rationale for design changes made during the public-release refactor. For consumer-facing
release history, see [`CHANGELOG.md`](CHANGELOG.md).

| Date | Change | Reason |
|------|--------|--------|
| 2026-03-24 | Initial DESIGN.md | Refactor for public release |
| 2026-03-24 | Removed hardcoded team defaults; added startup validation | Silent wrong-team defaults are a footgun |
| 2026-03-24 | Added `ASSEMBLESPORTS_BASE_URL`, `ASSEMBLESPORTS_LIVE_URL`, `TIMEZONE` | Enable non-OzTag orgs and non-Sydney timezones |
| 2026-03-24 | Deleted `check_schedule_with_calendar.py` (legacy) | ~80% duplicate; not referenced by workflow |
| 2026-03-24 | Deleted `2026_READY.md` (internal migration doc) | Not useful to public users |
| 2026-03-24 | v1.1: triggers table, UTC cron range, Match ID tag, reminders, edge cases, cache 10GB | Design review feedback |
| 2026-03-24 | v1.2: renamed the team-score key to `team_score` in parsed match dict | A team-specific key name contradicts the generic-tool framing |
| 2026-03-24 | v1.2: added `status_changes: []` to first-run `schedule_changes` dict | Structural inconsistency with `compare_schedules()` return shape |
| 2026-03-24 | v1.2: replaced `datetime.utcnow()` with `datetime.now(timezone.utc)` | Deprecated since Python 3.12 |
| 2026-03-24 | v1.2: hoisted `load_json_file` out of first-run loop | N redundant disk reads; no functional impact |
| 2026-03-24 | v1.2: added `SEASON_KEY` to required Variables table | Cache/artifact key was hardcoded to `2026` in workflow |
