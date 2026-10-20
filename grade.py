"""
grade.py: checks how the model's past calls turned out.

Run it the day after games, once get_stats.py has the new box scores:
    python3 get_stats.py
    python3 grade.py

It reads model_history.csv (written by simple_model.py), finds each player's
real result in player_logs.csv, and reports:
  - how often the side the model leaned toward actually won
  - the profit or loss if you had bet $100 on every lean, at the real price
  - the same, split by how strongly the model disagreed with the book

Saves every graded prop to graded_props.csv.
"""

import unicodedata

import pandas as pd

HISTORY_FILE = "model_history.csv"
LOGS_FILE = "player_logs.csv"
OUTPUT_FILE = "graded_props.csv"

MARKET_TO_STAT = {
    "player_points": "PTS",
    "player_rebounds": "REB",
    "player_assists": "AST",
    "player_threes": "FG3M",
    "player_points_rebounds_assists": "PRA",
    "player_points_rebounds": "PR",
    "player_points_assists": "PA",
    "player_rebounds_assists": "RA",
    "player_blocks": "BLK",
    "player_steals": "STL",
    "player_turnovers": "TOV",
}

def add_combo_stats(logs):
    """Combo props (PRA, P+R, P+A, R+A) are sums of the box-score stats."""
    logs["PRA"] = logs["PTS"] + logs["REB"] + logs["AST"]
    logs["PR"] = logs["PTS"] + logs["REB"]
    logs["PA"] = logs["PTS"] + logs["AST"]
    logs["RA"] = logs["REB"] + logs["AST"]
    return logs



def clean_name(name):
    name = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    name = name.lower().replace(".", "").replace("'", "").replace("-", " ")
    words = [w for w in name.split() if w not in {"jr", "sr", "ii", "iii", "iv"}]
    return " ".join(words)


def profit_on_100(price):
    """Profit from a winning $100 bet at American odds: -110 -> 90.91, +120 -> 120."""
    price = float(price)
    return 100 * 100 / -price if price < 0 else price


def summarize(group):
    decided = group[group["result"] != "Push"]
    if decided.empty:
        return pd.Series({"props": len(group), "lean_won": "-", "profit_per_$100": "-"})
    won = (decided["result"] == decided["model_leans"]).mean()
    return pd.Series({
        "props": len(decided),
        "lean_won": f"{won:.0%}",
        "profit_per_$100": f"{decided['profit'].sum():+.0f}",
    })


def split_table(g, column):
    """One summary row per group (e.g. per disagreement size)."""
    rows = {}
    for value, group in g.groupby(column, observed=True):
        rows[value] = summarize(group)
    return pd.DataFrame(rows).T.to_string()


def main():
    history = pd.read_csv(HISTORY_FILE)
    history["tip_time_utc"] = pd.to_datetime(history["tip_time_utc"], utc=True)
    history["model_run_utc"] = pd.to_datetime(history["model_run_utc"], utc=True)

    # If the model ran several times before a game, keep its last call before tip-off.
    history = history[history["model_run_utc"] < history["tip_time_utc"]]
    keys = ["tip_time_utc", "player", "market", "bookmaker"]
    history = history.sort_values("model_run_utc").drop_duplicates(keys, keep="last")

    # NBA.com dates games in US Eastern time.
    history["game_date"] = history["tip_time_utc"].dt.tz_convert("America/New_York").dt.date
    history["name_key"] = history["player"].map(clean_name)

    logs = add_combo_stats(pd.read_csv(LOGS_FILE, parse_dates=["GAME_DATE"]))
    logs["game_date"] = logs["GAME_DATE"].dt.date
    logs["name_key"] = logs["PLAYER_NAME"].map(clean_name)

    graded = []
    for market, stat in MARKET_TO_STAT.items():
        part = history[history["market"] == market]
        if part.empty:
            continue
        actual = logs[["name_key", "game_date", stat]].rename(columns={stat: "actual"})
        graded.append(part.merge(actual, on=["name_key", "game_date"], how="inner"))

    if not graded or all(g.empty for g in graded):
        print("Nothing to grade yet. Run get_stats.py after the games finish, then try again.")
        return
    g = pd.concat(graded, ignore_index=True)

    g["result"] = "Push"
    g.loc[g["actual"] > g["line"], "result"] = "Over"
    g.loc[g["actual"] < g["line"], "result"] = "Under"

    lean_price = g["price_over"].where(g["model_leans"] == "Over", g["price_under"])
    g["profit"] = 0.0
    won = g["result"] == g["model_leans"]
    lost = (g["result"] != g["model_leans"]) & (g["result"] != "Push")
    g.loc[won, "profit"] = lean_price[won].map(profit_on_100)
    g.loc[lost, "profit"] = -100.0

    strength = g["difference"].abs()
    g["disagreement"] = pd.cut(strength, [0, 0.05, 0.10, 0.20, 1.0],
                               labels=["0-5%", "5-10%", "10-20%", "20%+"],
                               include_lowest=True)
    g.to_csv(OUTPUT_FILE, index=False)

    print(f"Graded {len(g)} props from {g['game_date'].nunique()} game day(s).\n")
    print("Overall:")
    print(summarize(g).to_string())
    print("\nBy how strongly the model disagreed with the book:")
    print(split_table(g, "disagreement"))
    print("\nBy which side the model leaned:")
    print(split_table(g, "model_leans"))
    print(f"\nTo break even at typical -110 prices, the lean must win about 52.4%.")
    print("A few days of results is mostly luck; judge the model after several hundred props.")
    print(f"Every graded prop saved to {OUTPUT_FILE}.")


if __name__ == "__main__":
    main()
