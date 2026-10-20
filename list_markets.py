"""
list_markets.py: shows every prop type and game period PropLine offers for
one of today's NBA games, so we can decide what else to collect.

Run it:   python3 list_markets.py      (uses 2 of your daily PropLine requests)
Saves the full answer to markets_available.json.
"""

import json

import requests

from config import get_secret

BASE = "https://api.prop-line.com/v1/sports/basketball_nba"


def main():
    key = get_secret("PROPLINE_KEY")
    if not key:
        print("No PROPLINE_KEY in .env")
        return
    events = requests.get(f"{BASE}/events", params={"apiKey": key}, timeout=30)
    events.raise_for_status()
    games = events.json()
    if not games:
        print("No upcoming games right now.")
        return
    game = games[0]
    answer = requests.get(f"{BASE}/events/{game['id']}/markets", params={"apiKey": key}, timeout=30)
    print(f"{game.get('away_team')} @ {game.get('home_team')}: HTTP {answer.status_code}")
    data = answer.json()
    with open("markets_available.json", "w") as f:
        json.dump(data, f, indent=1)
    print("Saved to markets_available.json")


if __name__ == "__main__":
    main()
