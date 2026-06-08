import json
import os
import requests
import urllib3
from flask import Flask, render_template, request, redirect, url_for, session
from datetime import datetime, timedelta

urllib3.disable_warnings()

app = Flask(__name__)
app.secret_key = 'hearing-calendar-secret'

SUPA_URL = os.environ.get('SUPA_URL') or 'https://zeocbvzhwhpqnrmlmzyr.supabase.co'
SUPA_KEY = os.environ.get('SUPA_KEY') or 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inplb2Nidnpod2hwcW5ybWxtenlyIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA4NTM1ODYsImV4cCI6MjA5NjQyOTU4Nn0.Igq--cRQxDO9ZQI7pDU2ONjg26ugZbh5Ij1J3eAxwvo'

HEADERS = {
    'apikey': SUPA_KEY,
    'Authorization': f'Bearer {SUPA_KEY}',
}

USERS = {
    'elchanan': {'name': 'אלחנן שמריה', 'court_id': '5',  'court_name': 'רחובות'},
    'avi':      {'name': 'אבי אושרי',   'court_id': '12', 'court_name': 'בית הדין הגדול'},
}


def get_last_sync(court_id: str) -> str:
    """Return the last sync time for the given court as a formatted string, or ''."""
    try:
        r = requests.get(
            f'{SUPA_URL}/rest/v1/last_sync?id=eq.{court_id}&select=synced_at',
            headers=HEADERS, timeout=5, verify=False,
        )
        rows = r.json()
        if rows:
            dt = datetime.fromisoformat(rows[0]['synced_at'])
            return dt.strftime('%d/%m/%Y %H:%M')
    except Exception:
        pass
    return ''


def _case_priority(subject: str) -> int:
    s = subject or ''
    if 'גירושין' in s: return 1
    if 'מזונות'  in s: return 2
    if 'רכוש'    in s: return 3
    if 'שהות'    in s: return 4
    return 5


def _dedup_by_couple(hearings):
    """Keep only the highest-priority case per couple per day."""
    best = {}
    for h in hearings:
        couple = frozenset([h['side_a'].strip(), h['side_b'].strip()])
        key = (couple, h['date'])
        p = _case_priority(h['subject'])
        if key not in best or p < best[key][0]:
            best[key] = (p, h)
    return [v[1] for v in best.values()]


def fetch_from_supabase(from_date, to_date, court_id):
    r = requests.get(
        f'{SUPA_URL}/rest/v1/hearings?select=data',
        headers=HEADERS,
        timeout=15,
        verify=False,
    )
    if r.status_code != 200:
        raise RuntimeError(f'Supabase error: {r.status_code} {r.text}')

    all_rows = [row['data'] for row in r.json()]

    from_dt = datetime.strptime(from_date, '%d/%m/%Y')
    to_dt   = datetime.strptime(to_date,   '%d/%m/%Y')

    def in_range(h):
        try:
            d = datetime.strptime(h.get('date', ''), '%d/%m/%Y')
            return from_dt <= d <= to_dt
        except Exception:
            return False

    # Filter by court and date range
    filtered = [h for h in all_rows
                if str(h.get('court_id', '')) == str(court_id) and in_range(h)]

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

    remapped = [remap(h) for h in filtered]
    return _dedup_by_couple(remapped)


def _month_range(year: int, month: int):
    start = datetime(year, month, 1)
    end   = datetime(year + 1, 1, 1) - timedelta(days=1) if month == 12 \
            else datetime(year, month + 1, 1) - timedelta(days=1)
    return start, end


@app.route('/select', methods=['GET', 'POST'])
def select_user():
    if request.method == 'POST':
        user_key = request.form.get('user')
        if user_key in USERS:
            session['user'] = user_key
            return redirect(url_for('index'))
    return render_template('select.html', users=USERS)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('select_user'))


@app.route('/')
def index():
    if 'user' not in session:
        return redirect(url_for('select_user'))

    user = USERS[session['user']]
    court_id   = user['court_id']
    court_name = user['court_name']

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
        all_hearings = fetch_from_supabase(from_date, to_date, court_id)
        for h in all_hearings:
            hearings_by_date.setdefault(h['date'], []).append(h)
    except Exception as e:
        error = str(e)

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

    return render_template(
        'index.html',
        weeks=weeks,
        month_name=start.strftime('%B %Y'),
        year=year, month=month,
        prev_year=prev_year, prev_month=prev_month,
        next_year=next_year, next_month=next_month,
        error=error,
        hearings_json=json.dumps(all_hearings, ensure_ascii=False),
        total=len(all_hearings),
        court_name=court_name,
        user_name=user['name'],
        last_sync=get_last_sync(court_id),
    )


if __name__ == '__main__':
    app.run(debug=True, port=5000)
