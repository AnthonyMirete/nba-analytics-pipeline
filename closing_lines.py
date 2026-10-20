"""
closing_lines.py: saves each game's prop lines just before tip-off.

These "closing lines" are the market's final, best-informed prices. Comparing
the price a pick got with the closing price (closing line value, see clv.py)
shows within a few hundred picks whether there is a real edge.

It's meant to run every 10 minutes (setup_closing.sh schedules it). Each run
looks for games starting within the next 20 minutes that haven't been saved
yet, so every game is captured once, about 10-20 minutes before tip-off.
Uses PropLine: 1 request per run plus 1 per game.

Run by hand:
    python3 closing_lines.py
"""

import csv
import os
from datetime import datetime, timedelta, timezone

import save_props

OUTPUT_FILE = "closing_lines.csv"
WINDOW_MINUTES = 20


def already_saved():
    if not os.path.exists(OUTPUT_FILE):
        return set()
    with open(OUTPUT_FILE, newline="") as f:
        return {row["game_id"] for row in csv.DictReader(f)}


def save(rows):
    if not rows:
        return
    new = not os.path.exists(OUTPUT_FILE)
    with open(OUTPUT_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        if new:
            writer.writeheader()
        writer.writerows(rows)


def main():
    source, key = save_props.pick_source()
    if source != "propline":
        print("Closing lines need the PropLine key in .env.")
        return
    now = datetime.now(timezone.utc)
    games = save_props.get_todays_games(source, key)
    done = already_saved()
    soon = [g for g in games
            if g["id"] not in done
            and now < datetime.fromisoformat(str(g["commence_time"]).replace("Z", "+00:00"))
            <= now + timedelta(minutes=WINDOW_MINUTES)]
    if not soon:
        print(f"{now:%H:%M} UTC: no games tipping in the next {WINDOW_MINUTES} minutes that still need closing lines.")
        return
    captured_at = now.isoformat(timespec="seconds")
    for game in soon:
        data, left = save_props.get_props_for_game(source, key, game["id"])
        rows = [r for r in save_props.flatten(game, data, captured_at) if r.get("line_type") != "milestone"]
        tip = datetime.fromisoformat(str(game["commence_time"]).replace("Z", "+00:00"))
        for r in rows:
            r["minutes_before_tip"] = round((tip - now).total_seconds() / 60)
        save(rows)
        print(f"{now:%H:%M} UTC: saved {len(rows)} closing lines for "
              f"{game.get('away_team')} @ {game.get('home_team')} (requests left: {left})")


if __name__ == "__main__":
    main()
