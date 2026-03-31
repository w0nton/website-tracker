"""
Checks the Met Opera student tickets page every 3 minutes.
- Sends an immediate alert if any new performance appears.
- Sends a daily summary of all available performances.
"""

import json
import os
import re
import smtplib
import sys
from datetime import date, datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

import cloudscraper
from bs4 import BeautifulSoup

URL = "https://www.metopera.org/season/tickets/student-tickets/"
STATE_FILE = Path(__file__).parent.parent / "state.json"
RECIPIENT_EMAILS = ["zengyofficial@gmail.com", "info@juventuspromusica.org", "akbarali672006@gmail.com"]

MONTH_ABBR = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def parse_performance_date(date_str: str) -> date | None:
    date_str = date_str.strip()
    date_str = re.sub(r"^[A-Za-z]{3},\s*", "", date_str)
    match = re.match(r"([A-Za-z]{3})\s+(\d{1,2})", date_str)
    if not match:
        return None
    month = MONTH_ABBR.get(match.group(1))
    if month is None:
        return None
    day = int(match.group(2))
    today = date.today()
    try:
        candidate = date(today.year, month, day)
    except ValueError:
        return None
    if (candidate - today).days < -30:
        try:
            candidate = date(today.year + 1, month, day)
        except ValueError:
            return None
    return candidate


def fetch_page() -> str:
    scraper = cloudscraper.create_scraper()
    response = scraper.get(URL, timeout=30)
    response.raise_for_status()
    return response.text


def extract_performances(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    performances = []
    date_pattern = re.compile(
        r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun),?\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\b"
    )
    seen_dates = set()

    for tag in soup.find_all(True):
        if tag.find(True):
            continue
        text = tag.get_text(separator=" ", strip=True)
        match = date_pattern.search(text)
        if not match:
            continue
        date_str = match.group(0)
        parsed = parse_performance_date(date_str)
        if parsed is None or parsed in seen_dates:
            continue
        seen_dates.add(parsed)

        name = ""
        ancestor = tag.parent
        for _ in range(6):
            if ancestor is None:
                break
            ancestor_text = ancestor.get_text(separator=" ", strip=True)
            if len(ancestor_text) > len(date_str) + 3 and len(ancestor_text) < 500:
                name_candidate = date_pattern.sub("", ancestor_text).strip(" ,|/-")
                name_candidate = re.sub(r"\s{2,}", " ", name_candidate)
                if name_candidate:
                    name = name_candidate
                    break
            ancestor = ancestor.parent

        performances.append({"name": name or "(unknown performance)", "date": parsed, "date_str": date_str})

    if not performances:
        text = soup.get_text(separator="\n")
        for match in date_pattern.finditer(text):
            date_str = match.group(0)
            parsed = parse_performance_date(date_str)
            if parsed is None or parsed in seen_dates:
                continue
            seen_dates.add(parsed)
            start = max(0, match.start() - 80)
            end = min(len(text), match.end() + 80)
            context = text[start:end].replace("\n", " ").strip()
            performances.append({"name": context, "date": parsed, "date_str": date_str})

    performances.sort(key=lambda p: p["date"])
    return performances


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def send_email(subject: str, body: str) -> None:
    smtp_user = os.environ["SMTP_USER"]
    smtp_password = os.environ["SMTP_PASSWORD"]
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = ", ".join(RECIPIENT_EMAILS)

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user, RECIPIENT_EMAILS, msg.as_string())

    print(f"Email sent: {subject}")


def format_performance_list(performances: list[dict]) -> str:
    lines = []
    for p in performances:
        lines.append(f"  - {p['date_str']}: {p['name']}")
    return "\n".join(lines)


def should_send_daily_summary(state: dict) -> bool:
    """Send daily summary once per day."""
    now = datetime.now(timezone.utc)
    last_sent_str = state.get("last_daily_summary")
    if not last_sent_str or last_sent_str == "null":
        return True
    last_sent = datetime.fromisoformat(last_sent_str)
    return last_sent.date() < now.date()


def main() -> None:
    print(f"Fetching {URL} ...")
    try:
        html = fetch_page()
    except Exception as e:
        print(f"ERROR: Could not fetch page: {e}", file=sys.stderr)
        sys.exit(1)

    performances = extract_performances(html)
    if not performances:
        print("WARNING: No performances found on page — page structure may have changed.")
        sys.exit(0)

    earliest = performances[0]
    print(f"Found {len(performances)} performance(s). Earliest: {earliest['name']!r} on {earliest['date_str']}")

    state = load_state()
    now = datetime.now(timezone.utc)

    # --- Alert: any new performances ---
    stored_dates = set(state.get("known_dates", []))
    current_dates = {p["date"].isoformat() for p in performances}
    new_performances = [p for p in performances if p["date"].isoformat() not in stored_dates]

    if stored_dates and new_performances:
        stored_earliest = min(date.fromisoformat(d) for d in stored_dates)
        for p in new_performances:
            if p["date"] < stored_earliest:
                subject = f"URGENT: New sooner Met Opera ticket — {p['name']} on {p['date_str']}"
            else:
                subject = f"New Met Opera student ticket added — {p['name']} on {p['date_str']}"
            body = (
                f"A new performance has appeared on the Met Opera student tickets page!\n\n"
                f"Performance: {p['name']}\n"
                f"Date: {p['date_str']}\n\n"
                f"All currently available performances:\n"
                f"{format_performance_list(performances)}\n\n"
                f"Book here: {URL}"
            )
            send_email(subject, body)
    elif not stored_dates:
        print("No stored state yet, recording current performances.")

    # --- Daily summary ---
    if should_send_daily_summary(state):
        print("Sending daily summary email...")
        subject = f"Daily Met Opera student tickets update — {now.strftime('%d %b %Y')}"
        body = (
            f"Here are all Met Opera student performances currently available:\n\n"
            f"{format_performance_list(performances)}\n\n"
            f"Book here: {URL}"
        )
        send_email(subject, body)
        state["last_daily_summary"] = now.isoformat()
    else:
        print("Daily summary already sent today, skipping.")

    # --- Save state ---
    state["known_dates"] = list(current_dates)
    state["earliest_date"] = earliest["date"].isoformat()
    state["earliest_performance"] = earliest["name"]
    state["last_checked"] = now.isoformat()
    save_state(state)
    print("State saved.")


if __name__ == "__main__":
    main()
