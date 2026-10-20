"""
get_stats.py: downloads every player's game-by-game stats from NBA.com.

Run it from Terminal (takes about a minute):
    python3 get_stats.py

It saves one file, player_logs.csv, with one row per player per game:
minutes, points, rebounds, assists, threes and more, for the seasons below.
Re-run it any time to pick up the newest games (it rebuilds the file).
"""

import time

import pandas as pd
from nba_api.stats.endpoints import leaguegamelog

# Seasons to download. 2026-27 is the season that's just starting,
# so it will only have a few games at first. That's fine.
SEASONS = ["2024-25", "2025-26", "2026-27"]

# Regular season and playoffs both count as real games for a player's form.
SEASON_TYPES = ["Regular Season", "Playoffs"]

OUTPUT_FILE = "player_logs.csv"

# The columns we keep (NBA.com sends more than we need).
KEEP = [
    "SEASON_ID", "GAME_ID", "GAME_DATE", "PLAYER_ID", "PLAYER_NAME",
    "TEAM_ABBREVIATION", "MATCHUP", "WL", "MIN", "PTS", "REB", "AST",
    "FG3M", "FGA", "FTA", "TOV", "STL", "BLK", "PF", "PLUS_MINUS",
]


def download(season, season_type):
    """Ask NBA.com for every player game in one season. Retries if it's slow."""
    for attempt in range(1, 4):
        try:
            result = leaguegamelog.LeagueGameLog(
                season=season,
                season_type_all_star=season_type,
                player_or_team_abbreviation="P",  # P = player rows, T = team rows
                timeout=60,
            )
            return result.get_data_frames()[0]
        except Exception as error:
            print(f"    attempt {attempt} failed ({error}); waiting and retrying...")
            time.sleep(5 * attempt)
    print(f"    gave up on {season} {season_type}")
    return pd.DataFrame()


def main():
    tables = []
    for season in SEASONS:
        for season_type in SEASON_TYPES:
            print(f"Downloading {season} {season_type}...")
            table = download(season, season_type)
            print(f"    {len(table)} player-games")
            if not table.empty:
                table["SEASON_TYPE"] = season_type
                tables.append(table)
            time.sleep(1.5)  # be polite to NBA.com between requests

    if not tables:
        print("Nothing downloaded. Check your internet connection and try again.")
        return

    logs = pd.concat(tables, ignore_index=True)
    logs = logs[KEEP + ["SEASON_TYPE"]]
    logs["GAME_DATE"] = pd.to_datetime(logs["GAME_DATE"])
    logs = logs.sort_values(["PLAYER_ID", "GAME_DATE"])
    logs.to_csv(OUTPUT_FILE, index=False)

    print(f"\nDone. Saved {len(logs):,} player-games for "
          f"{logs['PLAYER_ID'].nunique()} players to {OUTPUT_FILE}.")
    print(f"Newest game in the file: {logs['GAME_DATE'].max().date()}")


if __name__ == "__main__":
    main()
