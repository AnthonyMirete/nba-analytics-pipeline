"""
get_injuries.py: downloads the NBA's official injury report.

Every team must report who is Out, Doubtful, Questionable or Probable, and
the league publishes the list every 15 minutes on game days as a PDF, e.g.
    https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-10-20_12_45PM.pdf
This finds the newest one, reads it, and saves it to injuries.csv;
simple_model.py and parlay.py then skip injured players.

One-time install (used only to read the PDF):
    pip3 install nba-injury-report
Run it:
    python3 get_injuries.py
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests

OUTPUT_FILE = "injuries.csv"
LOOK_BACK_HOURS = 24     # if the newest report isn't up yet, try earlier ones
URL = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_{date}_{time}.pdf"


def report_url(stamp):
    """2026-10-20 13:45 -> ...Injury-Report_2026-10-20_01_45PM.pdf"""
    return URL.format(date=stamp.strftime("%Y-%m-%d"), time=stamp.strftime("%I_%M%p"))


def to_first_last(name):
    """The report lists 'Brunson, Jalen'; the odds site says 'Jalen Brunson'."""
    name = str(name)
    if "," in name:
        last, first = name.split(",", 1)
        return f"{first.strip()} {last.strip()}"
    return name.strip()


def newest_report():
    """Walk back 15 minutes at a time until a published report is found."""
    now = datetime.now(ZoneInfo("America/New_York"))
    stamp = now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)
    oldest = stamp - timedelta(hours=LOOK_BACK_HOURS)
    last_problem = "none tried"
    while stamp >= oldest:
        url = report_url(stamp)
        try:
            response = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            if response.status_code == 200 and response.content[:4] == b"%PDF":
                return stamp, url, response.content
            last_problem = f"HTTP {response.status_code} for {url}"
        except requests.RequestException as error:
            last_problem = f"{error.__class__.__name__}: {error}"
        stamp -= timedelta(minutes=15)
    print(f"No injury report found in the last {LOOK_BACK_HOURS} hours. Last problem: {last_problem}")
    return None, None, None


def main():
    try:
        from nba_injury_report.report import _process_pdf
    except ImportError:
        print("Missing package. Run this once in Terminal:  pip3 install nba-injury-report")
        return

    stamp, url, pdf = newest_report()
    if pdf is None:
        return

    entries = _process_pdf(pdf)
    table = pd.DataFrame([entry.model_dump() for entry in entries])
    if table.empty:
        print(f"Found the report ({url}) but couldn't read any players from it. "
              f"Send this message to Claude.")
        return

    table = table.rename(columns={
        "Game Date": "game_date", "Game Time": "game_time", "Matchup": "matchup",
        "Team": "team", "Player Name": "player", "Current Status": "status",
        "Reason": "reason",
    })
    # Rows like "NOT YET SUBMITTED" have no player or status; drop them.
    table = table.dropna(subset=["player", "status"])
    table["player"] = table["player"].map(to_first_last)
    table["report_time_et"] = stamp.strftime("%Y-%m-%d %H:%M")
    table = table[["report_time_et", "game_date", "matchup", "team", "player", "status", "reason"]]
    table.to_csv(OUTPUT_FILE, index=False)

    counts = table["status"].value_counts().to_dict()
    summary = ", ".join(f"{n} {s}" for s, n in counts.items())
    print(f"Injury report from {stamp:%b %d %I:%M %p} ET: {summary}. Saved to {OUTPUT_FILE}.")


if __name__ == "__main__":
    main()
