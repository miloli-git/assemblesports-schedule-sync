# AssembleSports Schedule Tracker

A GitHub Actions automation for sports teams on [AssembleSports](https://assemblesports.io).
Polls your team's schedule, syncs matches to Google Calendar, tracks ladder standings,
and posts notifications to Discord.

Built for OzTag Australia but configurable for any AssembleSports league.

---

## What it does

- **Polls** the AssembleSports API for your team's schedule on game days (hourly, Thursday-Sunday)
- **Syncs** all matches to Google Calendar -- creates, updates, and deletes events as the schedule changes
- **Tracks** competition ladder standings and posts updates when results come in
- **Notifies** a Discord channel of schedule changes (time, venue, cancellations) and ladder movements

---

## Prerequisites

1. **Google Cloud service account** with the Calendar API enabled
   - [Create a service account](https://console.cloud.google.com/iam-admin/serviceaccounts) and download the JSON key file
   - Share your target Google Calendar with the service account's email (grant "Make changes to events")

2. **Discord webhook URL**
   Server Settings -> Integrations -> Webhooks -> New Webhook

3. **A GitHub repository** to host this workflow (public or private)

---

## Setup

### 1. Fork or copy this repo

### 2. Add GitHub Secrets

Settings -> Secrets and variables -> Actions -> **Secrets**

| Secret | Value |
|--------|-------|
| `DISCORD_WEBHOOK_URL` | Your Discord webhook URL (required for the default `discord` channel — see [Notifications](#notifications)) |
| `GOOGLE_CREDENTIALS_JSON` | Full contents of your service account JSON key file |
| `GOOGLE_CALENDAR_ID` | Your calendar ID (your Google account email, or `primary`) |

### 3. Add GitHub Variables

Settings -> Secrets and variables -> Actions -> **Variables**

| Variable | Value |
|----------|-------|
| `TEAM_ID` | Your team's numeric ID from the AssembleSports URL |
| `TEAM_NAME` | Your team's display name (used in calendar events and Discord) |
| `CLUB_SLUG` | Your club's URL slug (found in `/c/club/<CLUB_SLUG>/` in the site URL) |
| `SEASON_KEY` | Short key for cache/artifact naming, e.g. `2026` (bump at season start) |

**Finding your Team ID and Club Slug:** navigate to your team page on the live site.
The URL pattern is: `https://live.assemblesports.io/c/club/<CLUB_SLUG>/teams/<TEAM_ID>`

> **The workflow stays dormant until it is initialised.** Until `TEAM_ID`,
> `TEAM_NAME`, `CLUB_SLUG`, and `SEASON_KEY` are all set, the scheduled job is
> **skipped** rather than run — so it never errors and never emails you a failed
> run. Setting these four Variables *is* the initialisation step; once they
> exist the cron schedule activates on its own, with no edit to the workflow
> file. (The script also fails loudly with a clear message if you somehow run it
> with these unset.)

### 4. Run it

Once the Variables above are set, the workflow runs automatically (Thursday-Sunday,
05:00-11:00 UTC). To test immediately, go to
**Actions -> Oztag Schedule Checker with Calendar Sync -> Run workflow**.

On the first run it creates all calendar events and sends an initialisation notification.

---

## Optional Configuration

Set these as GitHub Variables only if your league uses a different AssembleSports instance,
a different timezone, or a different notification channel.

| Variable | Default | Description |
|----------|---------|-------------|
| `ASSEMBLESPORTS_BASE_URL` | `https://api.oztagaustralia.assemblesports.io` | API base URL (scheme + host only) |
| `ASSEMBLESPORTS_LIVE_URL` | `https://live.oztagaustralia.assemblesports.io` | Browser-facing live site URL |
| `TIMEZONE` | `Australia/Sydney` | IANA timezone string for match times and calendar events |
| `NOTIFIER` | `discord` | Notification channel. Built-in: `discord`, `none`. See [Notifications](#notifications) |
| `DEBUG_DATES` | (unset) | Set to `true` to log raw API date values in the Actions run |

---

## Notifications

Notification delivery is **pluggable**. The channel is chosen by the `NOTIFIER` variable
(default `discord`):

| `NOTIFIER` | Behaviour | Requires |
|------------|-----------|----------|
| `discord` (default) | Posts schedule changes and a rich ladder embed to a Discord channel | `DISCORD_WEBHOOK_URL` secret |
| `none` / `off` | Disables sending; logs what *would* have been sent to the Actions log | nothing |

Discord is the only channel shipped, but the seam for adding others (WhatsApp, SMS, Slack,
Telegram…) is small. Each notification carries a plain-text fallback, so text-only channels
work without any Discord-specific formatting.

### Adding a channel

1. Subclass `Notifier` in `check_schedule_with_ladder.py` and implement `send(text, embed)`.
   Render `embed` (a Discord-style rich payload) if your platform supports rich content,
   otherwise send `text`.
2. Register it in `build_notifier()` under a new `NOTIFIER` value.
3. Set the `NOTIFIER` variable (and any secrets your channel needs).

Worked example — SMS via Twilio:

```python
class TwilioSMSNotifier(Notifier):
    name = "sms"

    def __init__(self, sid, token, from_, to):
        from twilio.rest import Client  # pip install twilio
        self.client, self.from_, self.to = Client(sid, token), from_, to

    def send(self, text=None, embed=None):
        body = text or (embed_to_text(embed) if embed else "")
        if body:
            self.client.messages.create(body=body, from_=self.from_, to=self.to)

# in build_notifier():
#   if channel == "sms":
#       return TwilioSMSNotifier(os.environ["TWILIO_SID"], os.environ["TWILIO_TOKEN"],
#                                os.environ["TWILIO_FROM"], os.environ["TWILIO_TO"])
```

---

## Season Rollover

At the start of each season, update `TEAM_ID` in GitHub Variables and bump the cache key
year in the workflow YAML. See `SEASON_SETUP.md` for the full procedure.

---

## Utilities

**`cleanup_duplicates.py`** -- run locally if calendar events get out of sync.
Requires `TEAM_NAME`, `GOOGLE_CREDENTIALS_JSON`, and optionally `GOOGLE_CALENDAR_ID`
as environment variables. Uses an interactive prompt before deleting ambiguous events.

**`debug_match_dates.py`** -- prints raw API match data with UTC and local timestamps.
Enable in CI by setting `DEBUG_DATES=true` as a GitHub Variable, or run locally.

---

## Design

See `DESIGN.md` for architecture, configuration rationale, and known limitations.
