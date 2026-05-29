#!/usr/bin/env python3
"""
Debug script: prints raw API match data with UTC and local timestamps.

Enable in CI by setting DEBUG_DATES=true as a GitHub Actions Variable.
Can also be run locally.

Required:
  TEAM_ID  -- numeric team ID
Optional:
  ASSEMBLESPORTS_BASE_URL  -- defaults to OzTag Australia API
  ASSEMBLESPORTS_LIVE_URL  -- defaults to OzTag Australia live site
  TIMEZONE                 -- defaults to Australia/Sydney
"""
import os
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

_base_url     = os.environ.get('ASSEMBLESPORTS_BASE_URL') or 'https://api.oztagaustralia.assemblesports.io'
_live_url     = os.environ.get('ASSEMBLESPORTS_LIVE_URL') or 'https://live.oztagaustralia.assemblesports.io'
_timezone     = os.environ.get('TIMEZONE') or 'Australia/Sydney'
_team_id_str  = os.environ.get('TEAM_ID', '')

if not _team_id_str:
    raise EnvironmentError("TEAM_ID environment variable must be set")

TEAM_ID      = int(_team_id_str)
TEAM_API_URL = f"{_base_url}/assemble/api/v1/live/teams/{TEAM_ID}"
HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en;q=0.9",
    "national-id": "18",
    "origin": _live_url,
    "referer": _live_url + "/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

print("=" * 70)
print(f"MATCH DATE DEBUG  |  Team ID: {TEAM_ID}  |  Timezone: {_timezone}")
print("=" * 70)

try:
    print("\nFetching schedule...")
    response = requests.get(TEAM_API_URL, headers=HEADERS, timeout=15)
    response.raise_for_status()
    matches = response.json()['data']['matches']
    print(f"Found {len(matches)} matches\n")

    print("-" * 70)
    for i, match in enumerate(matches, 1):
        ts       = match['dateTime']
        dt_utc   = datetime.fromtimestamp(ts / 1000, tz=ZoneInfo('UTC'))
        dt_local = datetime.fromtimestamp(ts / 1000, tz=ZoneInfo(_timezone))
        is_home  = match['homeTeam']['_id'] == TEAM_ID
        opponent = match['awayTeam']['name'] if is_home else match['homeTeam']['name']

        print(f"{i}. [{match['_id']}] {match['round']['displayName']} vs {opponent}")
        print(f"   UTC:   {dt_utc.strftime('%a %Y-%m-%d %H:%M %Z')}")
        print(f"   Local: {dt_local.strftime('%a %Y-%m-%d %H:%M %Z')}")
        print(f"   Status: {match['status']}")
        print()

except requests.exceptions.HTTPError as e:
    if e.response.status_code == 403:
        print("403 Forbidden -- may be rate limiting or IP restriction")
        print("Works fine in GitHub Actions environment")
    else:
        print(f"HTTP Error: {e}")
except Exception as e:
    import traceback
    print(f"Error: {e}")
    traceback.print_exc()

print("=" * 70)
