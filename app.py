import json
import requests
import urllib3
from flask import Flask, render_template, request
from datetime import datetime, timedelta

urllib3.disable_warnings()

app = Flask(__name__)

# ── Supabase credentials ───────────────────────────────────────────────────
import os
SUPA_URL = os.environ.get('SUPA_URL', '')
SUPA_KEY = os.environ.get('SUPA_KEY', '')

HEADERS = {
    'apikey': SUPA_KEY,
    'Authorization': f'Bearer {SUPA_KEY}',
}


def fetch_from_supabase(from_date: str, to_date: str) -> list[dict]:
    """Read hearings from Supabase for a date range (DD/MM/YYYY)."""
    r = requests.get(
        f'{SUPA_URL}/rest/v1/hearings?select=data',
        headers=HEADERS,
        timeout=15,
        verify=False,
    )
    if r.status_code != 200:
        raise RuntimeError(f'Supabase error: {r.status_code} {r.text}')

    all_rows = [row['data'] for row in r.json()]

    # Filter by date range
    from_dt = datetime.strptime(from_date, '%d/%m/%Y')
    to_dt   = datetime.strptime(to_date,   '%d/%m/%Y')

    def in_range(h):
        try:
            d = datetime.strptime(h.get('date', ''), '%d/%m/%Y')
            return from_dt <= d <= to_dt
        except Exception:
            return False

    filtered = [h for h in all_rows if in_range(h)]

    # Map from Supabase/friend format to our template format
    def remap(h):
        return {
            'assembly':    h.get('herkev') or h.get('assembly', ''),
            'hall':        h.get('hall', ''),
            'date':        h.get('date', ''),
            'time':        h.get('time', ''),
            'subject':     h.get('subj') or h.get('subject', ''),
            'side_a':      h.get('sideA') or h.get('side_a', ''),
            'side_b':      h.get('sideB') or h.get('side_b', ''),
            'file_number': h.get('tik') or h.get('file_number', ''),
        }

    return [remap(h) for h in filtered]


def _month_range(year: int, month: int):
    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = datetime(year, month + 1, 1) - timedelta(days=1)
    return start, end


@app.route('/')
def index():
    today = datetime.today()
    year  = int(request.args.get('year',  today.year))
    month = int(request.args.get('month', today.month))

    start, end = _month_range(year, month)
    from_date  = start.strftime('%d/%m/%Y')
    to_date    = end.strftime('%d/%m/%Y')

    error = None
    hearings_by_date = {}
    all_hearings = []
    try:
        all_hearings = fetch_from_supabase(from_date, to_date)
        for h in all_hearings:
            hearings_by_date.setdefault(h['date'], []).append(h)
    except Exception as e:
        error = str(e)

    # Build calendar weeks (Sunday first)
    first_weekday = (start.weekday() + 1) % 7
    weeks = []
    day = start - timedelta(days=first_weekday)
    while day <= end or len(weeks) == 0 or day.weekday() != 6:
        week = []
        for _ in range(7):
            date_str = day.strftime('%d/%m/%Y')
            week.append({
                'date':     date_str,
                'day':      day.day,
                'in_month': day.month == month,
                'is_today': day.date() == today.date(),
                'hearings': hearings_by_date.get(date_str, []),
            })
            day += timedelta(days=1)
        weeks.append(week)

    prev_month = month - 1 if month > 1 else 12
    prev_year  = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year  = year if month < 12 else year + 1

    month_name = start.strftime('%B %Y')

    return render_template(
        'index.html',
        weeks=weeks,
        month_name=month_name,
        year=year, month=month,
        prev_year=prev_year, prev_month=prev_month,
        next_year=next_year, next_month=next_month,
        error=error,
        hearings_json=json.dumps(all_hearings, ensure_ascii=False),
        total=len(all_hearings),
        court_name='בית הדין הרבני',
    )


if __name__ == '__main__':
    app.run(debug=True, port=5000)
