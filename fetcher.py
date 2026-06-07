import re
import io
import csv
import requests
from requests_negotiate_sspi import HttpNegotiateAuth
from bs4 import BeautifulSoup
import urllib3
urllib3.disable_warnings()

SHIRA = 'http://shira2'
PROXY_ME = 'http://localhost:5050/api/me'

_user_cache = None


def _make_session():
    s = requests.Session()
    s.auth = HttpNegotiateAuth()
    s.verify = False
    s.trust_env = False
    s.proxies = {'http': None, 'https': None}
    return s


def get_user_info() -> dict:
    """
    Fetch userId and courtId from the running shira_proxy (/api/me).
    Falls back to reading directly from Shira if proxy isn't running.
    Returns dict with keys: userId, courtId, courtName
    """
    global _user_cache
    if _user_cache:
        return _user_cache

    # Try shira_proxy first (already authenticated)
    try:
        r = requests.get(PROXY_ME, timeout=5)
        d = r.json()
        if d.get('courtId'):
            # Get userId from Shira directly
            s = _make_session()
            ru = s.get(f'{SHIRA}/api/api/userController/GetUser', timeout=10)
            ru.raise_for_status()
            ud = ru.json()
            user_id = ud.get('userId') or ud.get('id') or ud.get('userID') or 0
            _user_cache = {
                'userId': str(user_id),
                'courtId': str(d['courtId']),
                'courtName': d.get('courtName', ''),
            }
            return _user_cache
    except Exception:
        pass

    # Fallback: call Shira directly
    try:
        s = _make_session()
        r = s.get(f'{SHIRA}/api/api/userController/GetUser', timeout=10)
        r.raise_for_status()
        d = r.json()
        cl = d.get('courtList', [])
        court_id = cl[0]['courtId'] if cl else 5
        court_name = cl[0].get('courtName', '') if cl else ''
        user_id = d.get('userId') or d.get('id') or d.get('userID') or 0
        _user_cache = {
            'userId': str(user_id),
            'courtId': str(court_id),
            'courtName': court_name,
        }
        return _user_cache
    except Exception:
        # Last resort: hardcoded defaults
        return {'userId': '0', 'courtId': '5', 'courtName': 'רחובות'}


def fetch_hearings(from_date: str, to_date: str) -> list[dict]:
    """
    Fetch hearings between from_date and to_date (DD/MM/YYYY).
    Auto-detects userId and courtId from the logged-in user.
    Returns list of dicts: assembly, hall, date, time, subject, side_a, side_b, file_number
    """
    info = get_user_info()
    user_id = info['userId']
    court_id = info['courtId']

    base_url = f'{SHIRA}/classic/Forms/Reports/Rep001.aspx?userid={user_id}&courtid={court_id}'
    session = _make_session()

    # Step 1 — GET to obtain hidden form fields
    r1 = session.get(base_url)
    soup = BeautifulSoup(r1.text, 'html.parser')

    def _val(name):
        el = soup.find('input', {'name': name})
        return el['value'] if el else ''

    # Step 2 — POST to trigger report generation
    data = {
        '__VIEWSTATE':          _val('__VIEWSTATE'),
        '__VIEWSTATEGENERATOR': _val('__VIEWSTATEGENERATOR'),
        '__EVENTVALIDATION':    _val('__EVENTVALIDATION'),
        '__FORM_ACTION':        'SHOW_REPORT',
        '__SHIRA_USER_ID':      user_id,
        '__SHIRA_COURT_ID':     court_id,
        '__SHIRA_ALLOW_FILE_SEARCH': '0',
        '__SHIRA_FORMBASE_SCREEN_ID': '89',
        '__CLIENT_IP':          '10.67.4.32',
        '__FORM_SUBMIT_COUNTER': '1',
        'cboCourt':             court_id,
        'cboAssembly':          '-1',
        'txtFromDate':          from_date,
        'txtToDate':            to_date,
        'cboReportType':        '1',
        'cmdSearch':            'הצג דוח',
        'hdnCurrentReportId':   '-1',
        'hdnDefaultAssembly':   _val('hdnDefaultAssembly') or '0',
    }
    r2 = session.post(base_url, data=data)

    # Step 3 — extract ReportSession + ControlID
    match = re.search(
        r'ReportSession=([a-zA-Z0-9]+)&ControlID=([a-zA-Z0-9]+).*?OpType=ReportArea',
        r2.text
    )
    if not match:
        raise RuntimeError('Could not find ReportSession/ControlID in Step 2 response')

    session_id = match.group(1)
    control_id = match.group(2)

    # Step 4 — export as CSV
    csv_url = (
        f'{SHIRA}/classic/Reserved.ReportViewerWebControl.axd'
        f'?ReportSession={session_id}'
        f'&ControlID={control_id}'
        f'&Culture=1037&UICulture=1037&ReportStack=1'
        f'&OpType=Export&FileName=Rep001'
        f'&ContentDisposition=OnlyHtmlInline&Format=CSV'
    )
    r3 = session.get(csv_url)
    if r3.status_code != 200:
        raise RuntimeError(f'CSV export failed: HTTP {r3.status_code}')

    return _parse_csv(r3.text)


def _parse_csv(text: str) -> list[dict]:
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if 'AssemblyNameAndHall' in line and 'MeetingStartDate1' in line:
            start = i
            break
    if start is None:
        return []

    def s(v): return (v or '').strip()

    hearings = []
    reader = csv.DictReader(iter(lines[start:]))
    for row in reader:
        date = s(row.get('MeetingStartDate1'))
        assembly = s(row.get('AssemblyName'))
        if not date or not assembly:
            break
        assembly_hall = s(row.get('AssemblyNameAndHall'))
        hall_match = re.search(r'אולם:\s*(\d+)', assembly_hall)
        hall = hall_match.group(1) if hall_match else ''
        hearings.append({
            'assembly': assembly,
            'hall': hall,
            'date': date,
            'time': s(row.get('MeetingStartHour2')),
            'subject': s(row.get('SubjectSubDesc')),
            'side_a': s(row.get('SideA_FullName')),
            'side_b': s(row.get('SideB_FullName')),
            'file_number': s(row.get('FileNumber')),
        })
    return hearings
