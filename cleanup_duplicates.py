"""
One-time script to clean up duplicate calendar events.
Run this manually once to fix the calendar.

Usage:
  export TEAM_NAME='YourTeamName'
  export GOOGLE_CREDENTIALS_JSON='...'
  export GOOGLE_CALENDAR_ID='primary'  # optional, defaults to primary
  python cleanup_duplicates.py
"""
import os
import json
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

GOOGLE_CREDENTIALS_JSON = os.environ.get('GOOGLE_CREDENTIALS_JSON')
CALENDAR_ID = os.environ.get('GOOGLE_CALENDAR_ID', 'primary')
TEAM_NAME   = os.environ.get('TEAM_NAME')

if not TEAM_NAME:
    raise EnvironmentError(
        "TEAM_NAME environment variable must be set before running cleanup_duplicates.py"
    )


def get_calendar_service():
    if not GOOGLE_CREDENTIALS_JSON:
        raise ValueError("GOOGLE_CREDENTIALS_JSON environment variable not set")
    credentials_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    credentials = Credentials.from_service_account_info(
        credentials_dict,
        scopes=['https://www.googleapis.com/auth/calendar']
    )
    return build('calendar', 'v3', credentials=credentials)


def main():
    print("Starting calendar cleanup...")
    service = get_calendar_service()

    time_min = (datetime.now() - timedelta(days=30)).isoformat() + 'Z'
    time_max = (datetime.now() + timedelta(days=180)).isoformat() + 'Z'
    print(f"Fetching {TEAM_NAME} events from {time_min} to {time_max}...")

    events_result = service.events().list(
        calendarId=CALENDAR_ID,
        timeMin=time_min,
        timeMax=time_max,
        q=TEAM_NAME,
        singleEvents=True,
        orderBy='startTime'
    ).execute()

    events = events_result.get('items', [])
    print(f"Found {len(events)} {TEAM_NAME} events")

    if not events:
        print("No events to clean up!")
        return

    match_groups            = {}
    events_without_match_id = []

    for event in events:
        description = event.get('description', '')
        match_id    = None
        for line in description.split('\n'):
            if 'Match ID:' in line:
                match_id = line.split('Match ID:')[1].strip()
                break
        if match_id:
            match_groups.setdefault(match_id, []).append(event)
        else:
            events_without_match_id.append(event)

    print(f"\n{len(match_groups)} unique matches, {len(events_without_match_id)} events without match IDs")

    duplicates_removed = 0

    for match_id, group_events in match_groups.items():
        if len(group_events) > 1:
            print(f"\nMatch {match_id} has {len(group_events)} duplicates")
            group_events.sort(key=lambda e: e.get('updated', ''), reverse=True)
            keep_event = group_events[0]
            print(f"  Keeping: {keep_event['summary']} (updated: {keep_event.get('updated', '?')})")
            for event in group_events[1:]:
                print(f"  Deleting: {event['summary']} (ID: {event['id']})")
                try:
                    service.events().delete(calendarId=CALENDAR_ID, eventId=event['id']).execute()
                    duplicates_removed += 1
                except Exception as e:
                    print(f"    Failed to delete: {e}")

    if events_without_match_id:
        print(f"\nFound {len(events_without_match_id)} events without Match ID tags:")
        for event in events_without_match_id:
            dt = event['start'].get('dateTime', event['start'].get('date'))
            print(f"  - {event['summary']} on {dt}")
        response = input("\nDelete all events without Match ID tags? (yes/no): ")
        if response.lower() == 'yes':
            for event in events_without_match_id:
                try:
                    service.events().delete(calendarId=CALENDAR_ID, eventId=event['id']).execute()
                    print(f"  Deleted: {event['summary']}")
                    duplicates_removed += 1
                except Exception as e:
                    print(f"  Failed: {event['summary']}: {e}")

    print(f"\nDone. Removed {duplicates_removed} events.")


if __name__ == "__main__":
    main()
