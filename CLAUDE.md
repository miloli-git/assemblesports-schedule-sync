# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-purpose GitHub Actions automation (not a deployable app or library). On a cron schedule it polls the [AssembleSports](https://assemblesports.io) API for one team's schedule, syncs matches to Google Calendar, tracks competition ladder standings, and posts change notifications to Discord. Built for OzTag Australia but configurable for any AssembleSports league via env vars.

There is **no build step, no test suite, no linter config, and no `requirements.txt`**. Dependencies are pip-installed inline in the workflow. The entire runtime is one Python script executed by one GitHub Actions workflow.

## Running and testing

```bash
# Install deps (mirrors the workflow's install step)
pip install requests google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client

# Run the main script locally — requires env vars set (see Configuration below)
TEAM_ID=... TEAM_NAME=... CLUB_SLUG=... \
  DISCORD_WEBHOOK_URL=... GOOGLE_CREDENTIALS_JSON="$(cat key.json)" GOOGLE_CALENDAR_ID=... \
  python check_schedule_with_ladder.py

# Inspect raw API match dates (UTC + local) — read-only, no Calendar/Discord side effects
python debug_match_dates.py

# Interactively remove duplicate / mistracked calendar events (manual recovery only)
python cleanup_duplicates.py
```

There is no automated test harness. To verify changes, run the script with real (or test) credentials, or run `debug_match_dates.py` which only reads the API. The script is designed to be safe to re-run: duplicate-detection prevents double-booking calendar events.

The workflow can also be triggered manually from the GitHub **Actions** tab (`workflow_dispatch`), which force-posts current ladder standings to Discord regardless of cache state.

## Architecture

```
AssembleSports API → check_schedule_with_ladder.py → Google Calendar (create/update/delete)
                                                    → Discord webhook (schedule + ladder embeds)
                                                    → GitHub Actions cache (4 JSON state files)
```

| File | Role |
|------|------|
| `check_schedule_with_ladder.py` | The **only** file the workflow runs. ~825 lines, flat module of pure-ish functions orchestrated by `main()`. |
| `.github/workflows/schedule-checker-enhanced.yml` | Scheduler, runtime, secret/variable injection, cache restore+save. The script does nothing on its own. |
| `cleanup_duplicates.py` | Manual local recovery tool — **intentionally interactive** (`input()` gate before deleting). Not invoked by CI. |
| `debug_match_dates.py` | Read-only date debugger. Invoked by the workflow only when `DEBUG_DATES=true`. |
| `DESIGN.md` | Living design doc — **update it when design decisions change** (it has a Change Log section). Read it before non-trivial changes. |

### `main()` execution flow
Fetch team schedule → auto-detect competition URL from match data → fetch all competition matches → load 4 prior-state JSON files → diff for new results / ladder changes / schedule changes → sync Calendar → post Discord → save state. State files are read at start and written at end of every run.

### State persistence (critical to understand)
State lives in 4 JSON files persisted between runs via `actions/cache`, **never committed to the repo** (they're gitignored):

- `previous_schedule.json` — detects time/venue/status changes
- `calendar_events.json` — `match_id → Google Calendar event_id` map; prevents duplicate event creation
- `match_history.json` — completed matches; detects new results for ladder notifications
- `ladder_standings.json` — detects position/points changes

Cache is ephemeral by design. If evicted (7-day idle, or repo cache > 10 GB), the next run behaves as a **first run** and recreates all calendar events — duplicate detection makes this safe. The cache key embeds `SEASON_KEY`; bumping it forces a clean slate at season rollover.

## Configuration

All config comes from environment variables (GitHub Actions **Variables** for non-secrets, **Secrets** for credentials). The script raises `EnvironmentError` at startup if `TEAM_ID`, `TEAM_NAME`, or `CLUB_SLUG` are unset — **no silent defaults** for these.

**Required Variables:** `TEAM_ID`, `TEAM_NAME`, `CLUB_SLUG`, `SEASON_KEY`
**Required Secrets:** `DISCORD_WEBHOOK_URL`, `GOOGLE_CREDENTIALS_JSON`, `GOOGLE_CALENDAR_ID`
**Optional Variables (default to OzTag AU):** `ASSEMBLESPORTS_BASE_URL`, `ASSEMBLESPORTS_LIVE_URL`, `TIMEZONE` (default `Australia/Sydney`), `NOTIFIER` (default `discord`), `DEBUG_DATES`

`DISCORD_WEBHOOK_URL` is only required when `NOTIFIER=discord` (the default).

See `SEASON_SETUP.md` for the season-rollover procedure and `DESIGN.md` §4 for the full rationale.

## Conventions and gotchas

- **`national-id: "18"` is hardcoded, not configurable.** It is OzTag Australia's static org ID, not a credential. For a different org, set `ASSEMBLESPORTS_BASE_URL` / `ASSEMBLESPORTS_LIVE_URL` instead — see `DESIGN.md` §5.1.
- **Competition ID is auto-detected** from `competition._id` in the team's match data (`detect_competition_url()`), so it never needs manual config between seasons.
- **Duplicate calendar prevention is multi-layered:** skip-if-exists on create, deduplicated updates, and a live-calendar audit (`check_and_remove_duplicate_calendar_events()`). The authoritative dedup key is a `Match ID: <id>` line written into every event's description — preserve this when editing event creation.
- **Notifications are pluggable.** Delivery goes through a `Notifier` base class selected by the `NOTIFIER` env var via `build_notifier()`. Only `DiscordNotifier` ships (plus a `NullNotifier` for `none`/`off`). The `send(text, embed)` contract delivers exactly one representation — Discord renders the rich `embed`, text-only channels use the `text` fallback (the ladder embed is rendered to text by `embed_to_text()`). To add a channel: subclass `Notifier`, implement `send()`, register a branch in `build_notifier()`. See `DESIGN.md` §5.10 and the README "Notifications" section. Don't reintroduce a bare `send_discord_message()` free function — route everything through the notifier.
- **Status changes are tracked separately from time/venue changes** in `compare_schedules()` to avoid updating the same event twice in one run.
- **No API retry logic** by design — the API has been reliable; a failed run surfaces in the Actions log.
- **Calendar reminders (24h + 1h popups) are hardcoded** in `create_calendar_event()`.
- Use `datetime.now(timezone.utc)`, not the deprecated `datetime.utcnow()`.
- **`FORCE_CALENDAR_SYNC` and `SEND_HEARTBEAT` secrets are injected but not yet consumed** by the script (reserved).

## Outstanding issue (from DESIGN.md §7/§8)

The workflow YAML was previously un-updatable via tooling lacking the `workflow` OAuth scope, leaving these as intended-but-verify items. **Confirm against the current `.github/workflows/schedule-checker-enhanced.yml` before relying on `DESIGN.md`'s claim** — as of this writing the YAML already reads `DEBUG_DATES`/`ASSEMBLESPORTS_*`/`TIMEZONE` from `vars` and uses `${{ vars.SEASON_KEY }}` for the cache key, so the issue appears resolved. If you change config plumbing, keep the script, workflow `env:` blocks, `DESIGN.md`, and `SEASON_SETUP.md` in sync.

## Branch policy

Develop on `claude/claude-md-docs-9eeI2`. Do not push to `main` without explicit permission. Do not create PRs unless asked.
