# -*- coding: utf-8 -*-
import re
import requests
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any, Optional, Tuple
from zoneinfo import ZoneInfo
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from collections import defaultdict

# ── AssembleSports API configuration ─────────────────────────────────────────────
# ASSEMBLESPORTS_BASE_URL: API base URL (scheme + host only).
#   Default: OzTag Australia's endpoint. Change if your league uses a different
#   AssembleSports instance.
# ASSEMBLESPORTS_LIVE_URL: The browser-facing live site URL.
#   Used in HTTP request headers (origin/referer) and match permalink URLs.
#   Kept as a separate variable rather than derived from BASE_URL because other
#   AssembleSports installations may not follow the api.*/live.* naming pattern.
#
# NOTE: "national-id" header value "18" is OzTag Australia's static org ID
#   within AssembleSports. It is not a credential and is not expected to change.
#   For a different AssembleSports organisation, set ASSEMBLESPORTS_BASE_URL and
#   ASSEMBLESPORTS_LIVE_URL -- the national ID is implicit in those URLs.
# ────────────────────────────────────────────────────────────────────────────
_BASE_URL = os.environ.get('ASSEMBLESPORTS_BASE_URL') or 'https://api.oztagaustralia.assemblesports.io'
_LIVE_URL = os.environ.get('ASSEMBLESPORTS_LIVE_URL') or 'https://live.oztagaustralia.assemblesports.io'
_TIMEZONE = os.environ.get('TIMEZONE') or 'Australia/Sydney'

# ── Season configuration ────────────────────────────────────────────────────────
# Required: set these as GitHub Actions Variables in your repo.
# Settings -> Secrets and variables -> Actions -> Variables
#
# TEAM_ID:   The team's numeric ID on AssembleSports (changes each season).
#            Find it in the team page URL on the live site.
# TEAM_NAME: Display name used in calendar events and Discord messages.
# CLUB_SLUG: The club URL slug used in match permalink construction.
#            Found in the URL: /c/club/<CLUB_SLUG>/
#
# The script raises EnvironmentError at startup if any of these are unset.
# There are no silent defaults -- a misconfigured deployment fails loudly.
# ────────────────────────────────────────────────────────────────────────────
TEAM_ID   = os.environ.get('TEAM_ID')
TEAM_NAME = os.environ.get('TEAM_NAME')
CLUB_SLUG = os.environ.get('CLUB_SLUG')

_missing = [name for name, val in [('TEAM_ID', TEAM_ID), ('TEAM_NAME', TEAM_NAME), ('CLUB_SLUG', CLUB_SLUG)] if not val]
if _missing:
    raise EnvironmentError(
        f"Required environment variables not set: {', '.join(_missing)}\n"
        "Set these as GitHub Actions Variables: "
        "Settings -> Secrets and variables -> Actions -> Variables"
    )

TEAM_ID = int(TEAM_ID)

# API
BASE_API     = f"{_BASE_URL}/assemble/api/v1/live"
TEAM_API_URL = f"{BASE_API}/teams/{TEAM_ID}"
HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en;q=0.9",
    "national-id": "18",  # OzTag Australia's static org ID within AssembleSports
    "origin": _LIVE_URL,
    "referer": _LIVE_URL + "/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

DISCORD_WEBHOOK_URL     = os.environ.get('DISCORD_WEBHOOK_URL')
GOOGLE_CREDENTIALS_JSON = os.environ.get('GOOGLE_CREDENTIALS_JSON')
CALENDAR_ID             = os.environ.get('GOOGLE_CALENDAR_ID', 'primary')
PREVIOUS_SCHEDULE_FILE  = 'previous_schedule.json'
CALENDAR_EVENTS_FILE    = 'calendar_events.json'
MATCH_HISTORY_FILE      = 'match_history.json'
LADDER_FILE             = 'ladder_standings.json'

# Force-send on manual dispatch -- always post current standings regardless of cache state
FORCE_SEND = os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'


def fetch_schedule() -> List[Dict[str, Any]]:
    """Fetch the current schedule from the API."""
    response = requests.get(TEAM_API_URL, headers=HEADERS)
    response.raise_for_status()
    data = response.json()
    return data['data']['matches']


def detect_competition_url(matches: List[Dict[str, Any]]) -> Optional[str]:
    """
    Auto-detect the competition API URL from the team's own match data.
    This means the competition ID never needs to be manually updated between seasons.
    """
    for match in matches:
        competition_id = match.get('competition', {}).get('_id')
        if competition_id:
            url = f"{BASE_API}/matches/competitions/{competition_id}"
            print(f"   Auto-detected competition ID: {competition_id}")
            return url
    return None


def fetch_competition_matches(competition_url: str) -> List[Dict[str, Any]]:
    """Fetch all matches from the competition using the auto-detected URL."""
    response = requests.get(competition_url, headers=HEADERS)
    response.raise_for_status()
    data = response.json()
    return data.get('data', {}).get('matches', [])


def parse_match(match: Dict[str, Any]) -> Dict[str, Any]:
    """Parse match data into a simplified format."""
    timestamp = match['dateTime']
    dt = datetime.fromtimestamp(timestamp / 1000, tz=ZoneInfo(_TIMEZONE))

    is_home = match['homeTeam']['_id'] == TEAM_ID
    opponent = match['awayTeam']['name'] if is_home else match['homeTeam']['name']
    home_away = "Home" if is_home else "Away"

    home_score = match['scores']['homeTeam']
    away_score = match['scores']['awayTeam']
    team_score     = home_score if is_home else away_score
    opponent_score = away_score if is_home else home_score

    return {
        'match_id': match['_id'],
        'date': dt.strftime('%Y-%m-%d'),
        'time': dt.strftime('%H:%M'),
        'datetime': dt.isoformat(),
        'day_of_week': dt.strftime('%A'),
        'round': match['round']['displayName'],
        'home_away': home_away,
        'opponent': opponent,
        'venue': match['venue']['name'],
        'status': match['status'],
        'competition': match['competition']['shortName'],
        'team_score': team_score,
        'opponent_score': opponent_score
    }


def load_json_file(filename: str, default=None):
    """Load JSON file or return default."""
    if not os.path.exists(filename):
        return default if default is not None else {}
    with open(filename, 'r') as f:
        return json.load(f)


def save_json_file(filename: str, data):
    """Save data to JSON file."""
    with open(filename, 'w') as f:
        json.dump(data, f, indent=2)


def get_latest_round_results(match_history: Dict) -> List[Dict]:
    """Return all completed matches from the most recently completed round."""
    if not match_history:
        return []
    rounds = defaultdict(list)
    for match in match_history.values():
        round_name = match.get('round', {}).get('displayName', '')
        if round_name:
            rounds[round_name].append(match)
    if not rounds:
        return []
    def round_sort_key(name):
        m = re.search(r'(\d+)', name)
        return int(m.group(1)) if m else 0
    latest_round = max(rounds.keys(), key=round_sort_key)
    return rounds[latest_round]


def calculate_ladder(matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Calculate ladder standings from match results."""
    teams = defaultdict(lambda: {
        'team_id': None,
        'team_name': '',
        'played': 0,
        'won': 0,
        'drawn': 0,
        'lost': 0,
        'points_for': 0,
        'points_against': 0,
        'points': 0
    })

    for match in matches:
        if match.get('status') != 'final':
            continue

        home_team_id = match.get('homeTeam', {}).get('_id')
        away_team_id = match.get('awayTeam', {}).get('_id')
        if not home_team_id or not away_team_id:
            continue
        home_team_name = match.get('homeTeam', {}).get('name', 'Unknown')
        away_team_name = match.get('awayTeam', {}).get('name', 'Unknown')
        home_score = match['scores']['homeTeam']
        away_score = match['scores']['awayTeam']

        if not teams[home_team_id]['team_name']:
            teams[home_team_id]['team_id'] = home_team_id
            teams[home_team_id]['team_name'] = home_team_name
        if not teams[away_team_id]['team_name']:
            teams[away_team_id]['team_id'] = away_team_id
            teams[away_team_id]['team_name'] = away_team_name

        teams[home_team_id]['played'] += 1
        teams[away_team_id]['played'] += 1
        teams[home_team_id]['points_for'] += home_score
        teams[away_team_id]['points_for'] += away_score
        teams[home_team_id]['points_against'] += away_score
        teams[away_team_id]['points_against'] += home_score

        if home_score > away_score:
            teams[home_team_id]['won'] += 1
            teams[home_team_id]['points'] += 3
            teams[away_team_id]['lost'] += 1
        elif away_score > home_score:
            teams[away_team_id]['won'] += 1
            teams[away_team_id]['points'] += 3
            teams[home_team_id]['lost'] += 1
        else:
            teams[home_team_id]['drawn'] += 1
            teams[away_team_id]['drawn'] += 1
            teams[home_team_id]['points'] += 1
            teams[away_team_id]['points'] += 1

    ladder = []
    for team_id, stats in teams.items():
        stats['differential'] = stats['points_for'] - stats['points_against']
        ladder.append(stats)

    ladder.sort(key=lambda x: (-x['points'], -x['differential'], -x['points_for']))

    for i, team in enumerate(ladder, 1):
        team['position'] = i

    return ladder


def format_ladder_embed(ladder: List[Dict[str, Any]], new_results: List[Dict[str, Any]] = None,
                        position_changed: bool = False, old_position: int = None, new_position: int = None) -> Dict:
    """Format ladder as a Discord-style rich embed with change information.
    Non-Discord channels render this via embed_to_text()."""
    team_pos  = None
    team_data = None
    for team in ladder:
        if team['team_id'] == TEAM_ID:
            team_pos  = team['position']
            team_data = team
            break

    change_summary = []
    if new_results:
        change_summary.append(f"{len(new_results)} new result(s)")
    if position_changed and old_position and new_position:
        arrow = "UP" if new_position < old_position else "DOWN"
        change_summary.append(
            f"Position {arrow}: {old_position}{get_ordinal_suffix(old_position)} -> {new_position}{get_ordinal_suffix(new_position)}"
        )
    elif team_data and not position_changed and new_results:
        change_summary.append("Ladder updated")

    ladder_text  = "```\n"
    ladder_text += "  Pos Team                  P  W  D  L   F   A +/-  Pts\n"
    ladder_text += " " + "-" * 62 + "\n"

    for team in ladder:
        pos    = f"{team['position']:2d}"
        name   = team['team_name'][:20].ljust(20)
        played = f"{team['played']:2d}"
        won    = f"{team['won']:2d}"
        drawn  = f"{team['drawn']:2d}"
        lost   = f"{team['lost']:2d}"
        pf     = f"{team['points_for']:3d}"
        pa     = f"{team['points_against']:3d}"
        diff   = f"{team['differential']:+4d}"
        pts    = f"{team['points']:3d}"
        prefix = ">" if team['team_id'] == TEAM_ID else " "
        ladder_text += f"{prefix}{pos} {name} {played} {won} {drawn} {lost} {pf} {pa} {diff} {pts}\n"

    ladder_text += "```"

    title = "Ladder Update"
    if change_summary:
        title += " - " + " | ".join(change_summary)

    embed = {
        "title": title,
        "description": ladder_text,
        "color": 0x2ecc71 if team_pos and team_pos <= 4 else 0x3498db,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {
            "text": f"{TEAM_NAME} is {team_pos}{get_ordinal_suffix(team_pos)}" if team_pos else TEAM_NAME
        }
    }

    if new_results:
        results_text = ""
        for match in new_results:
            home  = match.get('homeTeam', {}).get('name', 'TBD')[:15]
            away  = match.get('awayTeam', {}).get('name', 'TBD')[:15]
            score = f"{match['scores']['homeTeam']}-{match['scores']['awayTeam']}"
            results_text += f"**{home}** {score} **{away}**\n"
        if results_text:
            embed["fields"] = [{"name": "Results", "value": results_text, "inline": False}]

    return embed


def get_ordinal_suffix(n: int) -> str:
    """Get ordinal suffix for a number (1st, 2nd, 3rd, etc.)."""
    if 10 <= n % 100 <= 20:
        return 'th'
    return {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')


def compare_schedules(old: Dict[str, Dict], new: Dict[str, Dict]) -> Dict[str, List]:
    """Compare two schedules and return changes."""
    changes = {
        'time_changes': [],
        'venue_changes': [],
        'status_changes': [],  # Only populated when time/venue did NOT also change
        'new_matches': [],
        'cancelled_matches': []
    }

    for match_id, new_match in new.items():
        if match_id in old:
            old_match = old[match_id]

            has_time_change   = (old_match['date'] != new_match['date'] or old_match['time'] != new_match['time'])
            has_venue_change  = old_match['venue'] != new_match['venue']
            has_status_change = old_match['status'] != new_match['status']

            if has_time_change:
                changes['time_changes'].append({
                    'match_id':  match_id,
                    'round':     new_match['round'],
                    'opponent':  new_match['opponent'],
                    'old_date':  old_match['date'],
                    'old_time':  old_match['time'],
                    'new_date':  new_match['date'],
                    'new_time':  new_match['time']
                })

            if has_venue_change:
                changes['venue_changes'].append({
                    'match_id':  match_id,
                    'round':     new_match['round'],
                    'opponent':  new_match['opponent'],
                    'old_venue': old_match['venue'],
                    'new_venue': new_match['venue'],
                    'date':      new_match['date'],
                    'time':      new_match['time']
                })

            # Only track status changes if time/venue did not also change.
            # This prevents double-updating the same calendar event.
            # NOTE: if a match is cancelled AND has a simultaneous time/venue
            # correction, it is handled via the time/venue branch (updated, not
            # deleted). This edge case is not expected in practice.
            if has_status_change and not has_time_change and not has_venue_change:
                changes['status_changes'].append({
                    'match_id':   match_id,
                    'round':      new_match['round'],
                    'opponent':   new_match['opponent'],
                    'old_status': old_match['status'],
                    'new_status': new_match['status']
                })
        else:
            changes['new_matches'].append(new_match)

    for match_id, old_match in old.items():
        if match_id not in new:
            changes['cancelled_matches'].append(old_match)

    return changes


def format_schedule_changes_message(changes: Dict[str, List]) -> Optional[str]:
    """Format schedule changes into a plain-text notification message."""
    if not any(changes.values()):
        return None

    change_types = []
    if changes['time_changes']:      change_types.append(f"{len(changes['time_changes'])} time change(s)")
    if changes['venue_changes']:     change_types.append(f"{len(changes['venue_changes'])} venue change(s)")
    if changes.get('status_changes'):change_types.append(f"{len(changes['status_changes'])} status change(s)")
    if changes['new_matches']:       change_types.append(f"{len(changes['new_matches'])} new match(es)")
    if changes['cancelled_matches']: change_types.append(f"{len(changes['cancelled_matches'])} cancelled match(es)")

    message = f"**Schedule Update** | {' | '.join(change_types)}\n\n"

    if changes['time_changes']:
        message += "**TIME CHANGES:**\n"
        for c in changes['time_changes']:
            message += f"- **{c['round']}** vs {c['opponent']}\n"
            message += f"  Old: {c['old_date']} {c['old_time']}\n"
            message += f"  New: {c['new_date']} {c['new_time']}\n\n"

    if changes['venue_changes']:
        message += "**VENUE CHANGES:**\n"
        for c in changes['venue_changes']:
            message += f"- **{c['round']}** vs {c['opponent']} ({c['date']} {c['time']})\n"
            message += f"  Old: {c['old_venue']}\n"
            message += f"  New: {c['new_venue']}\n\n"

    if changes.get('status_changes'):
        message += "**STATUS CHANGES:**\n"
        for c in changes['status_changes']:
            message += f"- **{c['round']}** vs {c['opponent']}: {c['old_status']} -> {c['new_status']}\n\n"

    if changes['new_matches']:
        message += "**NEW MATCHES:**\n"
        for m in changes['new_matches']:
            message += f"- **{m['round']}** vs {m['opponent']} - {m['date']} {m['time']} at {m['venue']}\n\n"

    if changes['cancelled_matches']:
        message += "**CANCELLED:**\n"
        for m in changes['cancelled_matches']:
            message += f"- **{m['round']}** vs {m['opponent']} (was {m['date']} {m['time']})\n\n"

    return message


# ── Notifications ────────────────────────────────────────────────────────────
# The notification channel is pluggable. Discord is the only channel shipped,
# but adding another (WhatsApp, SMS, Slack, Telegram, ...) is a small, isolated
# change: subclass Notifier, implement send(), and register it in
# build_notifier(). See the "Notifications" section of README.md for a worked
# SMS-via-Twilio stub.
#
# Contract: send(text, embed) delivers exactly ONE representation. `embed` is a
# Discord-style rich payload; channels that support rich formatting render it,
# all others fall back to the plain-text `text`. Every notification carries a
# `text` fallback so text-only channels never go silent.
# ──────────────────────────────────────────────────────────────────────────────
def embed_to_text(embed: Dict) -> str:
    """Render a Discord-style embed dict as plain text, so non-Discord channels
    (SMS, WhatsApp, ...) have a usable fallback for rich notifications."""
    parts = []
    if embed.get('title'):
        parts.append(embed['title'])
    if embed.get('description'):
        parts.append(embed['description'])
    for field in embed.get('fields', []):
        name, value = field.get('name', ''), field.get('value', '')
        parts.append(f"{name}\n{value}" if name else value)
    footer = (embed.get('footer') or {}).get('text')
    if footer:
        parts.append(footer)
    return "\n\n".join(p for p in parts if p)


class Notifier:
    """A notification channel. To add a platform, subclass this, implement
    send(), and register it in build_notifier()."""
    name = "base"

    def send(self, text: Optional[str] = None, embed: Optional[Dict] = None) -> None:
        raise NotImplementedError


class DiscordNotifier(Notifier):
    """Posts to a Discord channel via an incoming webhook. Prefers the rich
    embed; falls back to plain content."""
    name = "discord"

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send(self, text: Optional[str] = None, embed: Optional[Dict] = None) -> None:
        payload = {}
        if embed:
            payload['embeds'] = [embed]
        elif text:
            payload['content'] = text
        if not payload:
            return
        response = requests.post(self.webhook_url, json=payload)
        response.raise_for_status()
        print("Discord notification sent successfully")


class NullNotifier(Notifier):
    """No channel configured — logs to stdout instead of sending. Lets the
    Calendar sync run on its own without a notification target."""
    name = "none"

    def send(self, text: Optional[str] = None, embed: Optional[Dict] = None) -> None:
        rendered = text or (embed_to_text(embed) if embed else "")
        print("[notify] no channel configured; would have sent:")
        if rendered:
            print(rendered)


def build_notifier() -> Notifier:
    """Select the notification channel from the NOTIFIER env var (default
    'discord'). To add a channel, register a new branch here."""
    channel = (os.environ.get('NOTIFIER') or 'discord').lower()
    if channel == 'discord':
        if not DISCORD_WEBHOOK_URL:
            print("NOTIFIER=discord but DISCORD_WEBHOOK_URL is not set — notifications disabled")
            return NullNotifier()
        return DiscordNotifier(DISCORD_WEBHOOK_URL)
    if channel in ('none', 'off', 'disabled'):
        return NullNotifier()
    raise EnvironmentError(
        f"Unknown NOTIFIER '{channel}'. Built-in channels: discord, none. "
        "Add your own by subclassing Notifier and registering it in build_notifier() "
        "— see the Notifications section of README.md."
    )


def get_calendar_service():
    """Initialize and return Google Calendar service."""
    if not GOOGLE_CREDENTIALS_JSON:
        raise ValueError("GOOGLE_CREDENTIALS_JSON environment variable not set")

    credentials_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    credentials = Credentials.from_service_account_info(
        credentials_dict,
        scopes=['https://www.googleapis.com/auth/calendar']
    )
    return build('calendar', 'v3', credentials=credentials)


def create_calendar_event(service, match: Dict[str, Any]) -> str:
    """Create a calendar event for a match. Returns the new event ID."""
    dt     = datetime.fromisoformat(match['datetime'])
    end_dt = dt + timedelta(hours=1, minutes=30)

    if match['status'] == 'final':
        title = f"{TEAM_NAME} vs {match['opponent']} ({match['team_score']}-{match['opponent_score']})"
    else:
        title = f"{TEAM_NAME} vs {match['opponent']}"

    # Match ID tag in description is used by duplicate detection logic
    match_url = f"{_LIVE_URL}/c/club/{CLUB_SLUG}/matches/{match['match_id']}"
    event = {
        'summary': title,
        'location': match['venue'],
        'description': (
            f"{match['competition']} - {match['round']}\n"
            f"Status: {match['status']}\n"
            f"Match ID: {match['match_id']}\n\n"
            f"{match_url}"
        ),
        'start': {'dateTime': dt.isoformat(),     'timeZone': _TIMEZONE},
        'end':   {'dateTime': end_dt.isoformat(), 'timeZone': _TIMEZONE},
        'reminders': {
            'useDefault': False,
            'overrides': [
                {'method': 'popup', 'minutes': 24 * 60},  # 1 day before
                {'method': 'popup', 'minutes': 60},       # 1 hour before
            ],
        },
    }

    created_event = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
    print(f"Created calendar event: {match['round']} vs {match['opponent']}")
    return created_event['id']


def update_calendar_event(service, event_id: str, match: Dict[str, Any]):
    """Update an existing calendar event."""
    dt     = datetime.fromisoformat(match['datetime'])
    end_dt = dt + timedelta(hours=1, minutes=30)

    event = service.events().get(calendarId=CALENDAR_ID, eventId=event_id).execute()

    if match['status'] == 'final':
        title = f"{TEAM_NAME} vs {match['opponent']} ({match['team_score']}-{match['opponent_score']})"
    else:
        title = f"{TEAM_NAME} vs {match['opponent']}"

    match_url = f"{_LIVE_URL}/c/club/{CLUB_SLUG}/matches/{match['match_id']}"
    event['summary']     = title
    event['location']    = match['venue']
    event['description'] = (
        f"{match['competition']} - {match['round']}\n"
        f"Status: {match['status']}\n"
        f"Match ID: {match['match_id']}\n\n"
        f"{match_url}"
    )
    event['start'] = {'dateTime': dt.isoformat(),     'timeZone': _TIMEZONE}
    event['end']   = {'dateTime': end_dt.isoformat(), 'timeZone': _TIMEZONE}

    service.events().update(calendarId=CALENDAR_ID, eventId=event_id, body=event).execute()
    print(f"Updated calendar event: {match['round']} vs {match['opponent']}")


def delete_calendar_event(service, event_id: str):
    """Delete a calendar event."""
    service.events().delete(calendarId=CALENDAR_ID, eventId=event_id).execute()
    print(f"Deleted calendar event: {event_id}")


def check_and_remove_duplicate_calendar_events(service) -> Dict[str, str]:
    """
    Scan Google Calendar for duplicate team events and remove them.
    Events are grouped by the 'Match ID: <id>' tag in their description --
    this tag is written by create_calendar_event() into every event.
    Returns a cleaned mapping of match_id -> event_id.
    """
    print("\nChecking calendar for duplicate events...")

    time_min = (datetime.now() - timedelta(days=30)).isoformat() + 'Z'
    time_max = (datetime.now() + timedelta(days=180)).isoformat() + 'Z'

    try:
        events_result = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=time_min,
            timeMax=time_max,
            q=TEAM_NAME,
            singleEvents=True,
            orderBy='startTime',
            maxResults=500
        ).execute()

        events = events_result.get('items', [])
        print(f"   Found {len(events)} {TEAM_NAME} events in calendar")

        if not events:
            print("   No events to check")
            return {}

        match_groups          = defaultdict(list)
        events_without_match_id = []

        for event in events:
            description = event.get('description', '')
            match_id    = None
            for line in description.split('\n'):
                if 'Match ID:' in line:
                    match_id = line.split('Match ID:')[1].strip()
                    break
            if match_id:
                match_groups[match_id].append(event)
            else:
                events_without_match_id.append(event)

        cleaned_calendar_events = {}
        duplicates_removed      = 0

        for match_id, group_events in match_groups.items():
            if len(group_events) > 1:
                print(f"   WARNING: Match {match_id} has {len(group_events)} duplicate events")
                group_events.sort(key=lambda e: e.get('updated', ''), reverse=True)
                keep_event = group_events[0]
                cleaned_calendar_events[match_id] = keep_event['id']
                print(f"      Keeping: {keep_event['summary']} (ID: {keep_event['id'][:20]}...)")
                for event in group_events[1:]:
                    print(f"      Deleting: {event['summary']} (ID: {event['id'][:20]}...)")
                    try:
                        delete_calendar_event(service, event['id'])
                        duplicates_removed += 1
                    except Exception as e:
                        print(f"         Failed: {e}")
            else:
                cleaned_calendar_events[match_id] = group_events[0]['id']

        if events_without_match_id:
            print(f"   WARNING: {len(events_without_match_id)} events without Match ID tag (old format)")
            for event in events_without_match_id:
                start = event['start'].get('dateTime', event['start'].get('date', 'unknown'))
                print(f"      - {event['summary']} on {start}")
            print("      Run cleanup_duplicates.py to remove these manually.")

        if duplicates_removed > 0:
            print(f"   Removed {duplicates_removed} duplicate events")
        else:
            print("   No duplicates found")

        return cleaned_calendar_events

    except Exception as e:
        print(f"   Failed to check calendar for duplicates: {e}")
        import traceback
        traceback.print_exc()
        return {}


def sync_to_calendar(service, current_schedule: Dict[str, Dict], changes: Dict[str, List]):
    """Sync changes to Google Calendar."""
    calendar_events = load_json_file(CALENDAR_EVENTS_FILE, {})

    # Create new events
    for match in changes['new_matches']:
        match_id = str(match['match_id'])
        if match_id in calendar_events:
            print(f"Event already exists for match {match_id}, skipping")
            continue
        event_id = create_calendar_event(service, match)
        calendar_events[match_id] = event_id

    # Deduplicate updates: collect unique match_ids.
    # Prevents multiple updates to the same event when time and venue both change.
    matches_to_update = set()
    for change in changes['time_changes']:           matches_to_update.add(str(change['match_id']))
    for change in changes['venue_changes']:          matches_to_update.add(str(change['match_id']))
    for change in changes.get('status_changes', []): matches_to_update.add(str(change['match_id']))

    for match_id in matches_to_update:
        if match_id in calendar_events and match_id in current_schedule:
            try:
                update_calendar_event(service, calendar_events[match_id], current_schedule[match_id])
            except Exception as e:
                print(f"Failed to update calendar event for match {match_id}: {e}")
        elif match_id not in calendar_events:
            print(f"No calendar event found for match {match_id}, creating new event")
            event_id = create_calendar_event(service, current_schedule[match_id])
            calendar_events[match_id] = event_id

    # Delete cancelled events
    for match in changes['cancelled_matches']:
        match_id = str(match['match_id'])
        if match_id in calendar_events:
            try:
                delete_calendar_event(service, calendar_events[match_id])
                del calendar_events[match_id]
            except Exception as e:
                print(f"Failed to delete event for match {match_id}: {e}")
                del calendar_events[match_id]

    # Remove orphaned tracking entries
    orphaned = set(calendar_events.keys()) - set(current_schedule.keys())
    for match_id in orphaned:
        print(f"Removing orphaned calendar event for match {match_id}")
        try:
            delete_calendar_event(service, calendar_events[match_id])
        except Exception as e:
            print(f"Failed to delete orphaned event {match_id}: {e}")
        finally:
            del calendar_events[match_id]

    save_json_file(CALENDAR_EVENTS_FILE, calendar_events)


def main():
    print("=" * 60)
    print(f"{TEAM_NAME.upper()} SCHEDULE & LADDER TRACKER")
    print("=" * 60)

    # 1. Fetch current data
    print("\n1. Fetching schedule...")
    matches = fetch_schedule()
    current_schedule = {str(match['_id']): parse_match(match) for match in matches}
    print(f"   Found {len(current_schedule)} {TEAM_NAME} matches")

    print("\n2. Fetching competition matches...")
    competition_url = detect_competition_url(matches)
    if competition_url:
        all_matches = fetch_competition_matches(competition_url)
        print(f"   Found {len(all_matches)} total matches in competition")
    else:
        print("   WARNING: Could not detect competition URL -- ladder will be skipped")
        all_matches = []
    completed_count = len([m for m in all_matches if m.get('status') == 'final'])
    print(f"   {completed_count} matches completed")

    # 2. Load previous state
    previous_schedule     = load_json_file(PREVIOUS_SCHEDULE_FILE, {})
    previous_match_history= load_json_file(MATCH_HISTORY_FILE, {})
    previous_ladder       = load_json_file(LADDER_FILE, [])

    # 3. Check for new completed matches
    new_results          = []
    current_match_history= {}
    for match in all_matches:
        if match.get('status') == 'final':
            match_id = str(match['_id'])
            current_match_history[match_id] = match
            if match_id not in previous_match_history:
                new_results.append(match)
                home = match.get('homeTeam', {}).get('name', 'TBD')
                away = match.get('awayTeam', {}).get('name', 'TBD')
                print(f"   New result: {home} {match['scores']['homeTeam']}-{match['scores']['awayTeam']} {away}")

    # 4. Calculate ladder
    print("\n3. Calculating ladder...")
    current_ladder = calculate_ladder(all_matches)
    team_entry = next((t for t in current_ladder if t['team_id'] == TEAM_ID), None)
    if team_entry:
        pos = team_entry['position']
        print(f"   {TEAM_NAME}: {pos}{get_ordinal_suffix(pos)} - {team_entry['won']}W {team_entry['drawn']}D {team_entry['lost']}L ({team_entry['points']} pts)")

    # 5. Check for ladder changes
    ladder_changed  = False
    position_changed= False
    old_position    = None
    new_position    = None

    if previous_ladder:
        prev_team = next((t for t in previous_ladder if t['team_id'] == TEAM_ID), None)
        if prev_team and team_entry:
            if prev_team['position'] != team_entry['position']:
                print(f"   Position changed: {prev_team['position']} -> {team_entry['position']}")
                ladder_changed   = True
                position_changed = True
                old_position     = prev_team['position']
                new_position     = team_entry['position']
            elif prev_team['points'] != team_entry['points']:
                print(f"   Points changed: {prev_team['points']} -> {team_entry['points']}")
                ladder_changed = True

    # 6. Check for schedule changes
    print("\n4. Checking for schedule changes...")
    if previous_schedule:
        schedule_changes = compare_schedules(previous_schedule, current_schedule)
    else:
        schedule_changes = {
            'time_changes': [], 'venue_changes': [], 'status_changes': [],
            'new_matches': [], 'cancelled_matches': []
        }
    has_schedule_changes = any(schedule_changes.values())

    print("   Changes detected" if has_schedule_changes else "   No schedule changes")

    # 7. Sync to Google Calendar
    if GOOGLE_CREDENTIALS_JSON:
        print("\n5. Syncing to Google Calendar...")
        try:
            service = get_calendar_service()

            cleaned = check_and_remove_duplicate_calendar_events(service)
            if cleaned:
                existing = load_json_file(CALENDAR_EVENTS_FILE, {})
                existing.update(cleaned)
                save_json_file(CALENDAR_EVENTS_FILE, existing)
                print(f"   Updated tracking file with {len(cleaned)} calendar events")

            if not previous_schedule:
                print("   First run: creating all calendar events...")
                calendar_events = {}
                existing = load_json_file(CALENDAR_EVENTS_FILE, {})
                for match_id, match in current_schedule.items():
                    if match_id in existing:
                        calendar_events[match_id] = existing[match_id]
                    else:
                        event_id = create_calendar_event(service, match)
                        calendar_events[match_id] = event_id
                save_json_file(CALENDAR_EVENTS_FILE, calendar_events)
            elif has_schedule_changes:
                sync_to_calendar(service, current_schedule, schedule_changes)

            print("   Calendar sync completed")
        except Exception as e:
            print(f"   Calendar sync failed: {e}")
            import traceback
            traceback.print_exc()

    # 8. Send notifications (channel selected by NOTIFIER env var)
    notifier = build_notifier()
    print(f"\n6. Notifications (channel: {notifier.name})...")
    if has_schedule_changes:
        message = format_schedule_changes_message(schedule_changes)
        if message:
            notifier.send(text=message)
            print("   Schedule update sent")

    if FORCE_SEND and current_ladder:
        latest_round = get_latest_round_results(current_match_history)
        round_name = latest_round[0].get('round', {}).get('displayName', '') if latest_round else ''
        embed = format_ladder_embed(current_ladder, latest_round if latest_round else None)
        embed['title'] = f'Current Standings -- {round_name}' if round_name else 'Current Standings'
        notifier.send(text=embed_to_text(embed), embed=embed)
        print(f"   Force-send: current standings posted ({round_name})")
    elif (ladder_changed or new_results) and current_ladder:
        embed = format_ladder_embed(
            current_ladder,
            new_results if new_results else None,
            position_changed=position_changed,
            old_position=old_position,
            new_position=new_position
        )
        notifier.send(text=embed_to_text(embed), embed=embed)
        print("   Ladder update sent")

    if not FORCE_SEND and not has_schedule_changes and not ladder_changed and not new_results:
        print("   No updates to send")

    # 9. Save state
    print("\n7. Saving state...")
    save_json_file(PREVIOUS_SCHEDULE_FILE, current_schedule)
    save_json_file(MATCH_HISTORY_FILE, current_match_history)
    save_json_file(LADDER_FILE, current_ladder)
    print("   State saved")

    print("\n" + "=" * 60)
    print("COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
