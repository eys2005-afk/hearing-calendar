"""
Fetches hearing data from Shira (http://shira2) for Rehovot court (courtid=5).
Requires Windows + requests-negotiate-sspi for NTLM auth.

Flow:
  1. GET Rep001.aspx  -> extract hidden ASP.NET fields
  2. POST with date range -> 200 OK, ~44KB response
  3. Extract ReportSession + ControlID from response
  4. GET ReportViewerWebControl.axd -> report HTML with hearing data
"""

import re
import logging
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

try:
    from requests_negotiate_sspi import HttpNegotiateAuth
    HAS_SSPI = True
except ImportError:
    HAS_SSPI = False
    logging.warning("requests-negotiate-sspi not available (Linux?). NTLM auth disabled.")

BASE_URL = "http://shira2"
REPORT_PATH = "/classic/Forms/Reports/Rep001.aspx"
VIEWER_PATH = "/classic/Reserved.ReportViewerWebControl.axd"
COURT_ID = "5"
USER_ID = "1"  # overridden per call

log = logging.getLogger(__name__)


def _make_session():
    s = requests.Session()
    s.trust_env = False
    s.proxies = {"http": None, "https": None}
    if HAS_SSPI:
        s.auth = HttpNegotiateAuth()
    return s


def _get_hidden_fields(s: requests.Session, user_id: str) -> dict:
    """Step 1: GET the report page and extract ASP.NET hidden fields."""
    url = f"{BASE_URL}{REPORT_PATH}?userid={user_id}&courtid={COURT_ID}"
    r = s.get(url, timeout=30)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "lxml")
    fields = {}
    for name in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
        tag = soup.find("input", {"name": name})
        if tag:
            fields[name] = tag.get("value", "")
        else:
            log.warning("Hidden field %s not found", name)
            fields[name] = ""
    return fields


def _post_report(s: requests.Session, user_id: str, from_date: str, to_date: str, hidden: dict) -> str:
    """Step 2: POST with date range. Returns response text."""
    url = f"{BASE_URL}{REPORT_PATH}?userid={user_id}&courtid={COURT_ID}"
    data = {
        "__VIEWSTATE": hidden["__VIEWSTATE"],
        "__VIEWSTATEGENERATOR": hidden["__VIEWSTATEGENERATOR"],
        "__EVENTVALIDATION": hidden["__EVENTVALIDATION"],
        "cboAssembly": "-1",
        "txtFromDate": from_date,
        "txtToDate": to_date,
        "cboCourt": COURT_ID,
        "btnView": "הצג",
    }
    r = s.post(url, data=data, timeout=30)
    r.raise_for_status()
    return r.text


def _extract_report_params(html: str) -> tuple[str, str]:
    """Step 3: Extract ReportSession and ControlID from POST response."""
    session_match = re.search(r"ReportSession=([A-Za-z0-9]+)", html)
    control_match = re.search(r"ControlID=([A-Za-z0-9]+)", html)

    if not session_match:
        raise ValueError("ReportSession not found in POST response")
    if not control_match:
        raise ValueError("ControlID not found in POST response")

    return session_match.group(1), control_match.group(1)


def _fetch_report_html(s: requests.Session, report_session: str, control_id: str, debug: bool = False) -> str:
    """Step 4: GET the actual report HTML."""
    url = (
        f"{BASE_URL}{VIEWER_PATH}"
        f"?ReportSession={report_session}"
        f"&ControlID={control_id}"
        f"&Culture=1037&UICulture=1037&ReportStack=1"
        f"&OpType=ReportArea"
        f"&Controller=uctlReportControl_MainReportViewer"
        f"&Mode=true&ZoomMode=FullPage"
    )
    r = s.get(url, timeout=30)
    r.raise_for_status()
    if debug:
        with open("report.html", "w", encoding="utf-8") as f:
            f.write(r.text)
        log.info("Saved report HTML to report.html (%d bytes)", len(r.text))
    return r.text


def _parse_hearings(report_html: str) -> list[dict]:
    """
    Parse the report HTML and return a list of hearing dicts:
      { date, time, case_number, parties, assembly, judge, room }
    """
    soup = BeautifulSoup(report_html, "lxml")
    hearings = []

    # The report renders as a table; rows contain hearing data.
    # Column order (0-based): date, time, case_number, parties, assembly, judge, room
    # Adjust indices if the actual report differs.
    rows = soup.find_all("tr")
    for row in rows:
        cells = [td.get_text(strip=True) for td in row.find_all("td")]
        if len(cells) < 5:
            continue
        # Skip header rows (contain Hebrew column titles)
        if any(h in cells[0] for h in ("תאריך", "מספר", "עניין")):
            continue
        # Basic date validation: expect DD/MM/YYYY
        if not re.match(r"\d{2}/\d{2}/\d{4}", cells[0]):
            continue

        hearing = {
            "date": cells[0] if len(cells) > 0 else "",
            "time": cells[1] if len(cells) > 1 else "",
            "case_number": cells[2] if len(cells) > 2 else "",
            "parties": cells[3] if len(cells) > 3 else "",
            "assembly": cells[4] if len(cells) > 4 else "",
            "judge": cells[5] if len(cells) > 5 else "",
            "room": cells[6] if len(cells) > 6 else "",
        }
        hearings.append(hearing)

    log.info("Parsed %d hearings", len(hearings))
    return hearings


def fetch_hearings(user_id: str, from_date: date, to_date: date) -> list[dict]:
    """
    Main entry point. Returns list of hearing dicts for the given date range.
    Dates are Python date objects; converted internally to DD/MM/YYYY.
    """
    fmt = "%d/%m/%Y"
    from_str = from_date.strftime(fmt)
    to_str = to_date.strftime(fmt)

    log.info("Fetching hearings for user=%s %s -> %s", user_id, from_str, to_str)

    s = _make_session()
    hidden = _get_hidden_fields(s, user_id)
    post_html = _post_report(s, user_id, from_str, to_str, hidden)
    report_session, control_id = _extract_report_params(post_html)
    log.info("ReportSession=%s ControlID=%s", report_session, control_id)
    report_html = _fetch_report_html(s, report_session, control_id)
    return _parse_hearings(report_html)


def fetch_month(user_id: str, year: int, month: int) -> list[dict]:
    """Convenience: fetch all hearings for a calendar month."""
    from_date = date(year, month, 1)
    # Last day of month
    if month == 12:
        to_date = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        to_date = date(year, month + 1, 1) - timedelta(days=1)
    return fetch_hearings(user_id, from_date, to_date)


if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(level=logging.INFO)
    today = date.today()
    hearings = fetch_month(USER_ID, today.year, today.month)
    json.dump(hearings, sys.stdout, ensure_ascii=False, indent=2)
