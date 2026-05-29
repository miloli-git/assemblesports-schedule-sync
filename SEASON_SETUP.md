# Season Setup Guide

## Required Configuration

Set these as GitHub Actions Variables:
**Settings -> Secrets and variables -> Actions -> Variables**

| Variable | Description | Example |
|----------|-------------|---------|
| `TEAM_ID` | Team's numeric ID from the AssembleSports team page URL | `1234567` |
| `TEAM_NAME` | Display name for calendar events and notifications | `YourTeam` |
| `CLUB_SLUG` | Club slug from the URL: `/c/club/<CLUB_SLUG>/` | `sydneycity` |
| `SEASON_KEY` | Short key used for cache and artifact naming | `2026` |

The script raises `EnvironmentError` at startup if `TEAM_ID`, `TEAM_NAME`, or `CLUB_SLUG` are missing.

## Optional Configuration

Leave unset to use OzTag Australia defaults.

| Variable | Default | Description |
|----------|---------|-------------|
| `ASSEMBLESPORTS_BASE_URL` | `https://api.oztagaustralia.assemblesports.io` | API base URL. Only change if your league uses a different AssembleSports instance. |
| `ASSEMBLESPORTS_LIVE_URL` | `https://live.oztagaustralia.assemblesports.io` | Live site URL for request headers and match permalinks. |
| `TIMEZONE` | `Australia/Sydney` | IANA timezone for match times and calendar events. |
| `DEBUG_DATES` | (unset) | Set to `true` to enable API date debugging in the Actions log. |

## Competition Auto-Detection

The competition ID is automatically detected from the team's own match data.
No manual updates are needed between seasons.

---

## At the Start of a New Season

Each season your team gets a **new `TEAM_ID`**, and last season's cached state
(`calendar_events.json` etc.) no longer applies. These steps reset the tracker
cleanly. The competition/ladder ID is auto-detected, so you never touch it.

### 1. Find the new Team ID

Open your team's page on the live site and read the ID from the URL:

```
https://live.oztagaustralia.assemblesports.io/c/club/sydneycity/teams/<TEAM_ID>
                                                         ^club slug      ^this number
```

### 2. Update the GitHub Variables

Settings → Secrets and variables → Actions → **Variables**:

| Variable | Set to |
|----------|--------|
| `TEAM_ID` | the new season's team ID |
| `SEASON_KEY` | a new value, e.g. bump `2026` → `2026s2` or `2027`. **This is what forces a clean cache** — the new key misses the old cache, so the next run starts fresh and recreates all events. |
| `TEAM_NAME` / `CLUB_SLUG` | only if they changed (e.g. you moved clubs) |

> ⚠️ If you reuse the same `SEASON_KEY`, the run restores last season's
> `calendar_events.json`, whose match IDs don't exist this season — the tracker
> still works (it re-detects), but bumping the key is the clean path.

### 3. (Optional) Pre-bump tidy of last season's calendar

The tracker only manages the **current** schedule; it does not delete last
season's events. They'll simply remain as past events on the calendar. If you
want them gone, run the local cleanup tool against the old events before the
first run of the new season:

```bash
TEAM_NAME='YourTeam' GOOGLE_CREDENTIALS_JSON="$(cat key.json)" \
  GOOGLE_CALENDAR_ID=... python cleanup_duplicates.py
```

(Interactive — it prompts before deleting. Skip this if you're happy to leave
the history on the calendar.)

### 4. Trigger the first run and verify

Don't wait for the cron — kick it manually:

**Actions → Oztag Schedule Checker with Calendar Sync → Run workflow.**

A `workflow_dispatch` run also force-posts the current standings, so you get an
immediate notification confirming the channel works.

Then check the run log for:

- ✅ `Auto-detected competition ID: <id>` — confirms the new competition was found
- ✅ `Found N <team> matches` with a sensible N — confirms `TEAM_ID` is correct
- ✅ `First run: creating all calendar events...` — confirms the cache reset took
- ✅ A notification arrived on your configured channel (`NOTIFIER`)
- ✅ New events appear on the Google Calendar

If `Found 0 matches`, the `TEAM_ID` is wrong or the fixtures aren't published yet.

### 5. Done

Normal operation resumes on the cron schedule (Thu–Sun). The old season's cache
expires on its own (7-day idle eviction); no manual cleanup of cache is needed.

---

## State Storage

State is stored in GitHub Actions cache and artifacts between runs.

| File | Purpose |
|------|---------|
| `previous_schedule.json` | Last known schedule (change detection) |
| `calendar_events.json` | Match ID -> Calendar Event ID mapping |
| `match_history.json` | Completed matches (result detection) |
| `ladder_standings.json` | Previous ladder state (position tracking) |

**Cache key:** `schedule-state-{SEASON_KEY}-*` (bump `SEASON_KEY` at season start)
**Artifact:** `schedule-data-{SEASON_KEY}` (90-day retention, for manual inspection)

If the cache is evicted (7 days of no access, or repo exceeds 10 GB total cache),
the next run treats it as a first run and recreates all calendar events.
Duplicate detection prevents double-booking.
