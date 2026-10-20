"""
save_props.py: saves today's NBA player prop lines to a CSV file.

Run it once a day (the daily routine does this for you):
    python3 save_props.py

Every run ADDS rows to nba_props.csv (it never deletes old data),
so over the season you build your own history of prop lines.

Two possible data sources:
  - PropLine (preferred): free 1,000 requests a day, so it saves points,
    rebounds, assists and threes from every sportsbook it carries.
  - The Odds API (fallback): free 500 credits a month, enough for points only.
If a PropLine key is filled in below, PropLine is used.
"""

import csv
import os
from datetime import datetime, timedelta, timezone

import requests

from config import get_secret

# ---------------------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------------------

# API keys are read from the .env file in this folder (see .env.example),
# so they never appear in the code.
PROPLINE_KEY = get_secret("PROPLINE_KEY")

API_KEY = get_secret("ODDS_API_KEY")   # used only if there is no PropLine key

# Markets for each source. PropLine charges one request per game whatever
# the markets, so it can take all four.
PROPLINE_MARKETS = ["player_points", "player_rebounds", "player_assists", "player_threes"]
ODDS_API_MARKETS = ["player_points"]
ODDS_API_BOOKMAKERS = ["draftkings", "fanduel", "betmgm"]

OUTPUT_FILE = "nba_props.csv"

# ---------------------------------------------------------------------------
# The code below does the work. You don't need to change it.
# ---------------------------------------------------------------------------

SOURCES = {
    "propline": "https://api.prop-line.com/v1/sports/basketball_nba",
    "oddsapi": "https://api.the-odds-api.com/v4/sports/basketball_nba",
}


def pick_source():
    if PROPLINE_KEY and "PASTE" not in PROPLINE_KEY:
        return "propline", PROPLINE_KEY
    if API_KEY and "PASTE" not in API_KEY:
        return "oddsapi", API_KEY
    return None, None


def get_todays_games(source, key):
    """Upcoming NBA games in the next 24 hours."""
    response = requests.get(f"{SOURCES[source]}/events", params={"apiKey": key}, timeout=30)
    response.raise_for_status()
    games = response.json()
    if isinstance(games, dict):                     # some APIs wrap the list
        games = games.get("data") or games.get("events") or []

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=24)
    todays = []
    for game in games:
        start = game.get("commence_time") or game.get("start_time")
        if not start:
            continue
        tip_time = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        if now < tip_time < cutoff:
            game["commence_time"] = start
            todays.append(game)
    return todays


def get_props_for_game(source, key, game_id):
    """Prop lines for one game. Returns the data and how many requests/credits are left."""
    params = {"apiKey": key, "oddsFormat": "american"}
    if source == "propline":
        params["markets"] = ",".join(PROPLINE_MARKETS)
    else:
        params["markets"] = ",".join(ODDS_API_MARKETS)
        params["bookmakers"] = ",".join(ODDS_API_BOOKMAKERS)
    response = requests.get(f"{SOURCES[source]}/events/{game_id}/odds", params=params, timeout=30)
    response.raise_for_status()
    left = (response.headers.get("x-requests-remaining")
            or response.headers.get("x-ratelimit-remaining") or "?")
    return response.json(), left


def flatten(game, game_data, captured_at):
    """Turn the nested answer into simple rows: one row per player/book/side."""
    rows = []
    for book in game_data.get("bookmakers", []):
        for market in book.get("markets", []):
            for outcome in market.get("outcomes", []):
                if outcome.get("point") is None:
                    continue                        # skip yes/no style props
                rows.append({
                    "captured_at_utc": captured_at,
                    "game_id": game["id"],
                    "tip_time_utc": game["commence_time"],
                    "home_team": game_data.get("home_team", game.get("home_team")),
                    "away_team": game_data.get("away_team", game.get("away_team")),
                    "bookmaker": book["key"],
                    "market": market["key"],
                    "player": outcome.get("description", ""),
                    "side": outcome.get("name", ""),
                    "line": outcome.get("point", ""),
                    "price": outcome.get("price", ""),
                })
    return rows


def save_rows(rows):
    if not rows:
        return
    file_is_new = not os.path.exists(OUTPUT_FILE)
    with open(OUTPUT_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        if file_is_new:
            writer.writeheader()
        writer.writerows(rows)


def main():
    source, key = pick_source()
    if source is None:
        print("No API key found. Put your keys in the .env file in this folder (see .env.example).")
        return

    print(f"Using {'PropLine' if source == 'propline' else 'The Odds API'}.")
    captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    games = get_todays_games(source, key)
    print(f"Found {len(games)} NBA games in the next 24 hours.")

    total_rows, left = 0, "?"
    for game in games:
        data, left = get_props_for_game(source, key, game["id"])
        rows = flatten(game, data, captured_at)
        save_rows(rows)
        total_rows += len(rows)
        books = len({r["bookmaker"] for r in rows})
        print(f"  {game.get('away_team')} @ {game.get('home_team')}: "
              f"{len(rows)} lines from {books} sportsbooks")

    print(f"Done. Saved {total_rows} lines to {OUTPUT_FILE}.")
    print(f"Requests/credits left: {left}")


if __name__ == "__main__":
    main()
