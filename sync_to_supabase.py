"""
sync_to_supabase.py
Fetches hearings from Shira and saves them to Supabase.
Run automatically at 18:00 via Windows Task Scheduler.
"""

import json
import requests
import urllib3
from datetime import datetime, timedelta
from fetcher import fetch_hearings, get_user_info

urllib3.disable_warnings()

# ── Supabase credentials (from the friend's HTML) ──────────────────────────
SUPA_URL = 'https://zeocbvzhwhpqnrmlmzyr.supabase.co'
SUPA_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inplb2Nidnpod2hwcW5ybWxtenlyIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA4NTM1ODYsImV4cCI6MjA5NjQyOTU4Nn0.Igq--cRQxDO9ZQI7pDU2ONjg26ugZbh5Ij1J3eAxwvo'

# Hall mapping — matches the friend's JS HALL constant
HALL_MAP = {
    'א': 'א', 'ב': 'א', 'ג': 'א', 'ד': 'א', 'ה': 'א',
    'ו': 'ב', 'ז': 'ב', 'הקדשות': 'ב', 'ח': 'ב', 'י': 'ב', 'בהן': 'ב',
}

HEADERS = {
    'apikey': SUPA_KEY,
    'Authorization': f'Bearer {SUPA_KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'return=minimal',
}


def supabase_delete():
    """Delete all existing hearings (full replace)."""
    r = requests.delete(
        f'{SUPA_URL}/rest/v1/hearings?id=neq.__never__',
        headers=HEADERS,
        timeout=30,
        verify=False,
    )
    if r.status_code not in (200, 204):
        raise RuntimeError(f'Delete failed: {r.status_code} {r.text}')


def supabase_insert(rows: list):
    """Upsert hearing rows into Supabase."""
    headers = {**HEADERS, 'Prefer': 'resolution=merge-duplicates'}
    r = requests.post(
        f'{SUPA_URL}/rest/v1/hearings',
        headers=headers,
        data=json.dumps(rows, ensure_ascii=False),
        timeout=30,
        verify=False,
    )
    if r.status_code not in (200, 201):
        raise RuntimeError(f'Insert failed: {r.status_code} {r.text}')


def map_hearing(h: dict) -> dict:
    """Convert our fetcher format to the friend's JS format."""
    herkev = h['assembly']
    hall   = HALL_MAP.get(herkev, 'א')
    tik    = h['file_number']
    time   = h['time']
    date   = h['date']

    hh, mm = (int(x) for x in time.split(':')) if ':' in time else (0, 0)
    key = f"{tik}|{herkev}|{date}|{time}"

    return {
        'key':      key,
        'tik':      tik,
        'herkev':   herkev,
        'hall':     hall,
        'date':     date,
        'dateHeb':  '',
        'time':     time,
        'timeMin':  hh * 60 + mm,
        'tipul':    'רגיל',
        'subj':     h['subject'],
        'sideA':    h['side_a'],
        'sideB':    h['side_b'],
        'judges':   [],
        'assistant': '',
    }


def main():
    now = datetime.now()
    # Fetch current month + next month
    months = [
        (now.year, now.month),
        ((now.month == 12 and (now.year + 1, 1)) or (now.year, now.month + 1))[0]
        if now.month == 12 else (now.year, now.month + 1),
    ]

    all_hearings = []
    for year, month in months:
        start = datetime(year, month, 1)
        if month == 12:
            end = datetime(year + 1, 1, 1) - timedelta(days=1)
        else:
            end = datetime(year, month + 1, 1) - timedelta(days=1)

        from_date = start.strftime('%d/%m/%Y')
        to_date   = end.strftime('%d/%m/%Y')

        print(f'Fetching {from_date} → {to_date}...')
        try:
            hearings = fetch_hearings(from_date, to_date)
            print(f'  Got {len(hearings)} hearings')
            all_hearings.extend(hearings)
        except Exception as e:
            print(f'  ERROR: {e}')

    if not all_hearings:
        print('No hearings fetched — aborting.')
        return

    # Convert to Supabase row format and deduplicate by id
    seen = {}
    for h in all_hearings:
        m = map_hearing(h)
        seen[m['key']] = {'id': m['key'], 'data': m, 'updated_at': now.isoformat()}
    rows = list(seen.values())

    print(f'\nSaving {len(rows)} unique hearings to Supabase...')
    supabase_delete()
    supabase_insert(rows)
    print(f'Done at {now.strftime("%d/%m/%Y %H:%M")} ✓')


if __name__ == '__main__':
    main()
